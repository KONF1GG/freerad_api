import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .metrics import request_tracker
import metrics
import uuid

logger = logging.getLogger(__name__)


class EnhancedMetricsMiddleware(BaseHTTPMiddleware):
    """Улучшенный middleware для метрик с трекингом очереди."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())

        try:
            # 1. Трекинг начала обработки
            request_tracker.add_request(request_id)
            metrics.metrics.set_gauge(
                "http_requests_queue_size", len(request_tracker.queue)
            )

            # 2. Замер времени ожидания в очереди
            queue_wait_start = time.time()

            # 3. Вызов следующего middleware/обработчика
            response = await call_next(request)

            # 4. Проверяем, что получили валидный ответ
            if response is None:
                logger.error(
                    f"Got None response for request {request_id} to {request.url.path}"
                )
                from fastapi.responses import JSONResponse

                response = JSONResponse(
                    status_code=500,
                    content={"detail": "Internal server error - no response generated"},
                )

            # 5. Запись метрик после обработки
            queue_wait = time.time() - queue_wait_start
            metrics.metrics.record_histogram(
                "http_queue_wait_seconds", queue_wait, tags={"path": request.url.path}
            )

            return response

        except Exception as e:
            # Записываем метрику об ошибке
            logger.error(
                f"Exception in middleware for request {request_id}: {e}", exc_info=True
            )
            try:
                metrics.metrics.increment_counter(
                    "http_requests_failed_total", tags={"exception": type(e).__name__}
                )
            except Exception as metrics_error:
                logger.error(f"Failed to record metrics: {metrics_error}")

            # Важно: re-raise исключение, чтобы FastAPI обработало его правильно
            raise

        finally:
            # 6. Очистка трекера (выполняется всегда)
            try:
                request_tracker.remove_request(request_id)
                metrics.metrics.set_gauge(
                    "http_requests_queue_size", len(request_tracker.queue)
                )
            except Exception as cleanup_error:
                logger.error(f"Error in middleware cleanup: {cleanup_error}")
