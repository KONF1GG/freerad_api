import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .metrics import request_tracker
import metrics
import uuid

class EnhancedMetricsMiddleware(BaseHTTPMiddleware):
    """Улучшенный middleware для метрик с трекингом очереди."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        
        # 1. Трекинг начала обработки
        request_tracker.add_request(request_id)
        metrics.metrics.set_gauge(
            "http_requests_queue_size", 
            len(request_tracker.queue)
        )

        # 2. Замер времени ожидания в очереди
        queue_wait_start = time.time()
        
        try:
            response = await call_next(request)
            
            # 3. Запись метрик после обработки
            queue_wait = time.time() - queue_wait_start
            metrics.metrics.record_histogram(
                "http_queue_wait_seconds",
                queue_wait,
                tags={"path": request.url.path}
            )
            
            return response
            
        except Exception as e:
            metrics.metrics.increment_counter(
                "http_requests_failed_total",
                tags={"exception": type(e).__name__}
            )
            raise
            
        finally:
            # 4. Очистка трекера
            request_tracker.remove_request(request_id)
            metrics.metrics.set_gauge(
                "http_requests_queue_size",
                len(request_tracker.queue)
            )