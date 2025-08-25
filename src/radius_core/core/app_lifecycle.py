"""
Модуль для управления жизненным циклом FastAPI приложения.

Включает:
- Инициализацию Prometheus multiprocess режима
- Health checks для Redis и RabbitMQ
- Graceful shutdown
"""

import os
import stat
import logging
from contextlib import asynccontextmanager
from prometheus_client import multiprocess, CollectorRegistry

from ..config import PROMETHEUS_MULTIPROC_DIR
from ..clients.redis_client import redis_health_check, close_redis
from ..clients.rabbitmq_client import rabbitmq_health_check, close_rabbitmq

logger = logging.getLogger(__name__)


class AppLifecycleManager:
    """Менеджер жизненного цикла приложения."""

    def __init__(self):
        self.prometheus_multiproc_dir = PROMETHEUS_MULTIPROC_DIR

    def _setup_prometheus_multiproc(self):
        """Настройка Prometheus multiprocess режима."""
        if not self.prometheus_multiproc_dir:
            logger.info("Prometheus multiprocess mode not configured")
            return False

        try:
            logger.info(
                "Attempting to configure Prometheus multiprocess dir: %s",
                self.prometheus_multiproc_dir,
            )

            # Проверяем текущие права доступа
            parent_dir = os.path.dirname(self.prometheus_multiproc_dir)
            logger.info(
                "Parent directory: %s, exists: %s",
                parent_dir,
                os.path.exists(parent_dir),
            )

            # Создаем директорию для multiprocess метрик (НЕ очищаем существующую)
            os.makedirs(self.prometheus_multiproc_dir, exist_ok=True)

            # Устанавливаем права на запись для всех процессов
            os.chmod(
                self.prometheus_multiproc_dir,
                stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO,
            )

            # Проверяем, что директория действительно создана
            if os.path.exists(self.prometheus_multiproc_dir) and os.access(
                self.prometheus_multiproc_dir, os.W_OK
            ):
                # Устанавливаем переменную окружения для prometheus_client
                os.environ["prometheus_multiproc_dir"] = self.prometheus_multiproc_dir

                logger.info(
                    "Prometheus multiprocess dir successfully configured: %s",
                    self.prometheus_multiproc_dir,
                )
                return True
            else:
                raise OSError(
                    f"Directory {self.prometheus_multiproc_dir} is not writable"
                )

        except OSError as e:
            logger.warning(
                "Failed to create Prometheus multiprocess dir %s: %s. "
                "Metrics will work in single-process mode.",
                self.prometheus_multiproc_dir,
                e,
            )
            # Отключаем multiprocess режим, удаляем переменную окружения
            if "prometheus_multiproc_dir" in os.environ:
                del os.environ["prometheus_multiproc_dir"]
            return False

    async def _perform_health_checks(self):
        """Выполнение health checks для внешних сервисов."""
        # Health checks
        if not await redis_health_check():
            logger.error("Redis health check failed")
            raise RuntimeError("Redis unavailable")
        else:
            logger.info("Redis connection established")

        if not await rabbitmq_health_check():
            logger.error("RabbitMQ health check failed")
            raise RuntimeError("RabbitMQ unavailable")
        else:
            logger.info("RabbitMQ connection established")

    def _cleanup_prometheus_metrics(self):
        """Безопасная очистка Prometheus multiprocess метрик для текущего процесса."""
        try:
            if "prometheus_multiproc_dir" in os.environ:
                # Создаем временный registry для финальной очистки
                registry = CollectorRegistry()
                multiprocess.MultiProcessCollector(registry)

                logger.info("Prometheus multiprocess metrics cleaned up")
        except (OSError, ImportError) as e:
            logger.warning("Error cleaning up Prometheus metrics: %s", e)

    async def _cleanup_connections(self):
        """Закрытие соединений с внешними сервисами."""
        try:
            await close_redis()
            logger.info("Redis connections closed")
        except (ConnectionError, TimeoutError) as e:
            logger.warning("Error closing Redis connections: %s", e)

        try:
            await close_rabbitmq()
            logger.info("RabbitMQ connections closed")
        except (ConnectionError, TimeoutError) as e:
            logger.warning("Error closing RabbitMQ connections: %s", e)

    @asynccontextmanager
    async def lifespan(self, app=None):
        """Управление жизненным циклом приложения.

        Включает health checks, очистку метрик для multiprocess и graceful shutdown.
        """
        logger.info("Starting Radius Core service...")

        try:
            # Настройка Prometheus multiprocess режима
            self._setup_prometheus_multiproc()

            # Выполнение health checks
            await self._perform_health_checks()

            yield

        finally:
            logger.info("Shutting down Radius Core service...")

            # Безопасная очистка Prometheus multiprocess метрик для текущего процесса
            self._cleanup_prometheus_metrics()

            # Закрытие соединений с внешними сервисами
            await self._cleanup_connections()


# Глобальный экземпляр менеджера жизненного цикла
lifecycle_manager = AppLifecycleManager()
