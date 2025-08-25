"""
Модуль для управления метриками Prometheus.

Содержит все метрики, используемые в приложении:
- Redis операции
- RabbitMQ операции
- RADIUS функции
- Информация о воркерах
"""

import os
import time
import asyncio
import logging
from datetime import datetime
from functools import wraps
from prometheus_client import (
    Counter,
    Histogram,
    Info as PrometheusInfo,
    CollectorRegistry,
    multiprocess,
    generate_latest,
    REGISTRY,
)
from fastapi import Response

logger = logging.getLogger("radius_core")


class MetricsManager:
    """Централизованный менеджер метрик для Radius Core."""

    def __init__(self):
        self._init_worker_info()
        self._init_redis_metrics()
        self._init_rabbitmq_metrics()
        self._init_radius_metrics()
        self._init_http_metrics()

    def _init_worker_info(self):
        """Инициализация метрики информации о воркере."""
        self.worker_info = PrometheusInfo(
            "radius_worker", "Information about radius worker process"
        )
        self.worker_info.info({"pid": str(os.getpid())})

    def _init_redis_metrics(self):
        """Инициализация метрик для Redis операций."""
        self.redis_operations_duration = Histogram(
            "redis_operation_duration_seconds",
            "Time spent on Redis operations",
            ["operation"],
            buckets=[
                0.001,
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
            ],
        )

        self.redis_operations_total = Counter(
            "redis_operations_total", "Total Redis operations", ["operation", "status"]
        )

    def _init_rabbitmq_metrics(self):
        """Инициализация метрик для RabbitMQ операций."""
        self.rabbitmq_operations_duration = Histogram(
            "rabbitmq_operation_duration_seconds",
            "Time spent on RabbitMQ operations",
            ["operation", "queue"],
            buckets=[
                0.001,
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
            ],
        )

        self.rabbitmq_operations_total = Counter(
            "rabbitmq_operations_total",
            "Total RabbitMQ operations",
            ["operation", "queue", "status"],
        )

    def _init_radius_metrics(self):
        """Инициализация метрик для RADIUS функций."""
        self.radius_function_duration = Histogram(
            "radius_function_duration_seconds",
            "Time spent in RADIUS functions",
            ["function"],
            buckets=[
                0.001,
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
            ],
        )

        self.radius_function_total = Counter(
            "radius_function_total",
            "Total RADIUS function calls",
            ["function", "status"],
        )

    def _init_http_metrics(self):
        """Инициализация HTTP метрик."""
        self.http_request_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "endpoint", "status_code"],
            buckets=[
                0.001,
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
            ],
        )

        self.http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status_code"],
        )

        # Метрики для RPS (Requests Per Second)
        self.http_requests_per_second = Counter(
            "http_requests_per_second_total",
            "HTTP requests per second counter",
            ["method", "endpoint", "status_code"],
        )

        # Метрики для отслеживания времени запросов по часам (для heatmap)
        self.http_requests_by_hour = Counter(
            "http_requests_by_hour_total",
            "HTTP requests by hour of day",
            ["method", "endpoint", "hour"],
        )

        # Метрики по дням недели
        self.http_requests_by_weekday = Counter(
            "http_requests_by_weekday_total",
            "HTTP requests by day of week",
            ["method", "endpoint", "weekday"],
        )

        # Метрики по минутам (для детального heatmap)
        self.http_requests_by_minute = Counter(
            "http_requests_by_minute_total",
            "HTTP requests by minute of hour",
            ["method", "endpoint", "minute"],
        )

    def track_function(self, category: str, operation: str, **labels):
        """Универсальный декоратор для отслеживания времени выполнения функций.

        Args:
            category: Категория операции (redis, rabbitmq, radius, http, custom)
            operation: Название операции
            **labels: Дополнительные метки для метрик
        """

        def decorator(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                status = "success"
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception:
                    status = "error"
                    raise
                finally:
                    duration = time.time() - start_time
                    self._record_metrics(
                        category, operation, duration, status, **labels
                    )

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                status = "success"
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception:
                    status = "error"
                    raise
                finally:
                    duration = time.time() - start_time
                    self._record_metrics(
                        category, operation, duration, status, **labels
                    )

            # Возвращаем соответствующий wrapper в зависимости от типа функции
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper

        return decorator

    def _record_metrics(
        self, category: str, operation: str, duration: float, status: str, **labels
    ):
        """Запись метрик в соответствующие коллекторы."""
        try:
            if category == "redis":
                self.redis_operations_duration.labels(operation=operation).observe(
                    duration
                )
                self.redis_operations_total.labels(
                    operation=operation, status=status
                ).inc()
            elif category == "rabbitmq":
                queue = labels.get("queue", "default")
                self.rabbitmq_operations_duration.labels(
                    operation=operation, queue=queue
                ).observe(duration)
                self.rabbitmq_operations_total.labels(
                    operation=operation, queue=queue, status=status
                ).inc()
            elif category == "radius":
                self.radius_function_duration.labels(function=operation).observe(
                    duration
                )
                self.radius_function_total.labels(
                    function=operation, status=status
                ).inc()
            elif category == "http":
                method = labels.get("method", "GET")
                endpoint = labels.get("endpoint", operation)
                status_code = labels.get("status_code", 200)

                # Основные метрики
                self.http_request_duration.labels(
                    method=method, endpoint=endpoint, status_code=status_code
                ).observe(duration)
                self.http_requests_total.labels(
                    method=method, endpoint=endpoint, status_code=status_code
                ).inc()

                # RPS метрики
                self.http_requests_per_second.labels(
                    method=method, endpoint=endpoint, status_code=status_code
                ).inc()

                # Метрики по времени для heatmap
                now = datetime.now()
                current_hour = now.hour
                current_minute = now.minute
                current_weekday = now.strftime("%A")  # Monday, Tuesday, etc.

                self.http_requests_by_hour.labels(
                    method=method, endpoint=endpoint, hour=current_hour
                ).inc()
                self.http_requests_by_minute.labels(
                    method=method, endpoint=endpoint, minute=current_minute
                ).inc()
                self.http_requests_by_weekday.labels(
                    method=method, endpoint=endpoint, weekday=current_weekday
                ).inc()
            elif category == "custom":
                # Для кастомных метрик можно добавить дополнительные коллекторы
                pass
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning("Ошибка при записи метрик %s.%s: %s", category, operation, e)

    async def get_metrics(self, prometheus_multiproc_dir: str = None) -> Response:
        """Получение метрик с поддержкой multiprocess aggregation."""

        # Проверяем, доступен ли multiprocess режим
        multiprocess_available = (
            prometheus_multiproc_dir
            and "prometheus_multiproc_dir" in os.environ
            and os.path.exists(prometheus_multiproc_dir)
        )

        if multiprocess_available:
            try:
                # Создаем новый registry для multiprocess
                registry = CollectorRegistry()
                multiprocess.MultiProcessCollector(registry)

                # Генерируем агрегированные метрики со всех воркеров
                metrics_data = generate_latest(registry)
                return Response(content=metrics_data, media_type="text/plain")
            except (OSError, ImportError, ValueError) as e:
                logger.warning("Не удалось собрать метрики multiprocess: %s", e)

        # Восстановление в режиме одного процесса
        try:
            metrics_data = generate_latest(REGISTRY)
            return Response(content=metrics_data, media_type="text/plain")
        except (OSError, ImportError, ValueError, RuntimeError) as e:
            logger.error("Ошибка при генерации метрик: %s", e)
            return Response(content="", media_type="text/plain")

    def track_http_request(self, method: str = None, endpoint: str = None):
        """Декоратор для отслеживания HTTP запросов."""

        def decorator(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                status_code = 200
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception:
                    status_code = 500
                    raise
                finally:
                    duration = time.time() - start_time
                    # Определяем метод и endpoint из функции
                    func_method = method or "GET"
                    func_endpoint = endpoint or func.__name__

                    self.http_request_duration.labels(
                        method=func_method,
                        endpoint=func_endpoint,
                        status_code=status_code,
                    ).observe(duration)
                    self.http_requests_total.labels(
                        method=func_method,
                        endpoint=func_endpoint,
                        status_code=status_code,
                    ).inc()

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                status_code = 200
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception:
                    status_code = 500
                    raise
                finally:
                    duration = time.time() - start_time
                    # Определяем метод и endpoint из функции
                    func_method = method or "GET"
                    func_endpoint = endpoint or func.__name__

                    self.http_request_duration.labels(
                        method=func_method,
                        endpoint=func_endpoint,
                        status_code=status_code,
                    ).observe(duration)
                    self.http_requests_total.labels(
                        method=func_method,
                        endpoint=func_endpoint,
                        status_code=status_code,
                    ).inc()

            # Возвращаем соответствующий wrapper в зависимости от типа функции
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper

        return decorator


# Глобальный экземпляр менеджера метрик
metrics_manager = MetricsManager()

# Удобные алиасы для декораторов
track_function = metrics_manager.track_function
