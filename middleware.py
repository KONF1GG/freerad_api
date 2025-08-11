import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from urllib.parse import unquote

import metrics

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware для автоматического сбора метрик HTTP запросов"""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.registry = metrics.metrics

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Получаем метод и путь
        method = request.method
        path = request.url.path

        # Нормализуем путь для метрик (убираем динамические части)
        normalized_path = self._normalize_path(path)

        # Если путь не нужен для метрик, просто выполняем запрос без сбора метрик
        if normalized_path is None:
            return await call_next(request)

        # Засекаем время начала
        start_time = time.time()

        # Счетчик входящих запросов
        self.registry.increment_counter(
            "http_requests_total", {"method": method, "path": normalized_path}
        )

        # Gauge активных запросов
        self.registry.increment_counter(
            "http_requests_active", {"method": method, "path": normalized_path}
        )

        try:
            # Выполняем запрос
            response = await call_next(request)

            # Записываем метрики успешного выполнения
            duration = time.time() - start_time
            status_code = str(response.status_code)
            status_class = f"{status_code[0]}xx"

            # Счетчик по статус кодам
            self.registry.increment_counter(
                "http_responses_total",
                {
                    "method": method,
                    "path": normalized_path,
                    "status_code": status_code,
                    "status_class": status_class,
                },
            )

            # Время выполнения запроса
            self.registry.record_timer(
                "http_request_duration_seconds",
                duration,
                {"method": method, "path": normalized_path, "status_code": status_code},
            )

            # Размер ответа
            if hasattr(response, "headers") and "content-length" in response.headers:
                content_length = int(response.headers["content-length"])
                self.registry.record_histogram(
                    "http_response_size_bytes",
                    content_length,
                    {"method": method, "path": normalized_path},
                )

            return response

        except Exception as e:
            # Записываем метрики ошибки
            duration = time.time() - start_time

            self.registry.increment_counter(
                "http_requests_exceptions_total",
                {
                    "method": method,
                    "path": normalized_path,
                    "exception": type(e).__name__,
                },
            )

            self.registry.record_timer(
                "http_request_duration_seconds",
                duration,
                {"method": method, "path": normalized_path, "status_code": "500"},
            )

            logger.error(f"Request failed: {method} {path} - {e}")
            raise

        finally:
            # Уменьшаем счетчик активных запросов
            self.registry.increment_counter(
                "http_requests_active",
                {"method": method, "path": normalized_path, "_dec": "true"},
            )

    def _normalize_path(self, path: str) -> str | None:
        """Нормализация пути для группировки в метриках"""
        path = unquote(path)

        # Собираем метрики только для нужных эндпоинтов
        if path == "/acct/":
            return "/acct/"
        elif path == "/authorize/":
            return "/authorize/"
        else:
            # Для всех остальных путей возвращаем None чтобы не собирать метрики
            return None


class ResourceMetricsMiddleware(BaseHTTPMiddleware):
    """Middleware для сбора системных метрик во время обработки запросов"""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.registry = metrics.metrics

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            import psutil
            import asyncio

            # Метрики до выполнения запроса
            process = psutil.Process()

            # Системная память
            memory_info = process.memory_info()
            self.registry.set_gauge("process_memory_rss_bytes", memory_info.rss)
            self.registry.set_gauge("process_memory_vms_bytes", memory_info.vms)

            # CPU
            cpu_percent = process.cpu_percent()
            self.registry.set_gauge("process_cpu_percent", cpu_percent)

            # Количество потоков
            num_threads = process.num_threads()
            self.registry.set_gauge("process_threads", num_threads)

            # Открытые файловые дескрипторы
            try:
                num_fds = process.num_fds()
                self.registry.set_gauge("process_open_fds", num_fds)
            except AttributeError:
                # Windows не поддерживает num_fds
                pass

            # Asyncio метрики
            loop = asyncio.get_event_loop()

            # Количество запланированных задач
            all_tasks = asyncio.all_tasks(loop)
            self.registry.set_gauge("asyncio_tasks_total", len(all_tasks))

            # Запущенные задачи
            running_tasks = sum(1 for task in all_tasks if not task.done())
            self.registry.set_gauge("asyncio_tasks_running", running_tasks)

        except ImportError:
            # psutil не установлен
            pass
        except Exception as e:
            logger.warning(f"Failed to collect system metrics: {e}")

        # Выполняем запрос
        response = await call_next(request)
        return response
