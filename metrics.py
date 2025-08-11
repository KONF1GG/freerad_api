import time
import logging
from typing import Dict, Any, Optional
from collections import defaultdict
from datetime import datetime, timezone
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Расширенный сборщик метрик для RADIUS сервиса"""

    def __init__(self):
        self.counters = defaultdict(int)
        self.timers = defaultdict(list)
        self.gauges = defaultdict(float)
        self.histograms = defaultdict(list)
        self.registry = CollectorRegistry()
        self.prom_counters = {}
        self.prom_gauges = {}
        self.prom_histograms = {}
        self.start_time = time.time()

    def increment_counter(self, name: str, tags: Optional[Dict[str, str]] = None):
        """Увеличить счетчик"""
        if tags is None:
            tags = {}

        # Обработка специального тега для декремента (для активных запросов)
        is_decrement = tags.pop("_dec", None) == "true"

        key = self._make_key(name, tags)
        if is_decrement:
            self.counters[key] = max(0, self.counters[key] - 1)
        else:
            self.counters[key] += 1

        lbls = tags or {}

        if is_decrement:
            # Для декремента используем gauge
            prom = self._get_or_create_gauge(f"{name}_active", sorted(lbls.keys()))
            prom.labels(**lbls).dec()
        else:
            if name.endswith("_active"):
                # Для активных метрик используем gauge
                prom = self._get_or_create_gauge(name, sorted(lbls.keys()))
                prom.labels(**lbls).inc()
            else:
                # Для обычных счетчиков
                prom = self._get_or_create_counter(name, sorted(lbls.keys()))
                prom.labels(**lbls).inc()

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Установить значение gauge"""
        if tags is None:
            tags = {}
        key = self._make_key(name, tags)
        self.gauges[key] = value
        lbls = tags or {}
        prom = self._get_or_create_gauge(name, sorted(lbls.keys()))
        prom.labels(**lbls).set(value)

    def record_timer(
        self, name: str, duration: float, tags: Optional[Dict[str, str]] = None
    ):
        """Записать время выполнения"""
        if tags is None:
            tags = {}
        key = self._make_key(name, tags)
        self.timers[key].append(duration)
        lbls = tags or {}
        prom = self._get_or_create_histogram(name, sorted(lbls.keys()))
        prom.labels(**lbls).observe(duration)

        if len(self.timers[key]) > 1000:
            self.timers[key] = self.timers[key][-1000:]

    def record_histogram(
        self, name: str, value: float, tags: Optional[Dict[str, str]] = None
    ):
        """Записать значение в гистограмму"""
        if tags is None:
            tags = {}
        key = self._make_key(name, tags)
        self.histograms[key].append(value)
        lbls = tags or {}
        prom = self._get_or_create_histogram(name, sorted(lbls.keys()))
        prom.labels(**lbls).observe(value)

        if len(self.histograms[key]) > 1000:
            self.histograms[key] = self.histograms[key][-1000:]

    def _make_key(self, name: str, tags: Optional[Dict[str, str]] = None) -> str:
        """Создать ключ метрики"""
        if not tags:
            return name
        return f"{name}_" + "_".join(f"{k}_{v}" for k, v in sorted(tags.items()))

    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику"""
        stats = {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "timers": {},
            "histograms": {},
        }

        # Агрегируем таймеры
        for key, values in self.timers.items():
            if values:
                stats["timers"][key] = {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "p95": self._percentile(values, 95),
                    "p99": self._percentile(values, 99),
                }

        # Агрегируем гистограммы
        for key, values in self.histograms.items():
            if values:
                stats["histograms"][key] = {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "p50": self._percentile(values, 50),
                    "p95": self._percentile(values, 95),
                    "p99": self._percentile(values, 99),
                }

        return stats

    def prom_exposition(self) -> tuple[bytes, str]:
        """Render Prometheus metrics payload and content-type."""
        if self.registry is None:
            return generate_latest(None), CONTENT_TYPE_LATEST  # type: ignore
        return generate_latest(self.registry), CONTENT_TYPE_LATEST

    def _get_or_create_counter(self, name: str, label_names: list[str]) -> Counter:
        if name not in self.prom_counters:
            # type: ignore[arg-type]
            self.prom_counters[name] = Counter(
                name, name, labelnames=label_names, registry=self.registry
            )
        return self.prom_counters[name]

    def _get_or_create_gauge(self, name: str, label_names: list[str]) -> Gauge:
        if name not in self.prom_gauges:
            # type: ignore[arg-type]
            self.prom_gauges[name] = Gauge(
                name, name, labelnames=label_names, registry=self.registry
            )
        return self.prom_gauges[name]

    def _get_or_create_histogram(self, name: str, label_names: list[str]) -> Histogram:
        if name not in self.prom_histograms:
            # type: ignore[arg-type]
            self.prom_histograms[name] = Histogram(
                name, name, labelnames=label_names, registry=self.registry
            )
        return self.prom_histograms[name]

    def _percentile(self, values: list, p: float) -> float:
        """Вычислить перцентиль"""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        k = (len(sorted_values) - 1) * p / 100.0
        f = int(k)
        c = k - f
        if f + 1 < len(sorted_values):
            return sorted_values[f] * (1 - c) + sorted_values[f + 1] * c
        return sorted_values[f]

    def reset(self):
        """Сброс метрик"""
        self.counters.clear()
        self.timers.clear()
        self.gauges.clear()
        self.histograms.clear()


# Глобальный экземпляр сборщика метрик
metrics = MetricsCollector()


def get_prometheus_metrics() -> tuple[bytes, str]:
    return metrics.prom_exposition()


def record_packet(operation: str, direction: str, auth_type: str = "UNKNOWN"):
    """Записать метрику пакета"""
    metrics.increment_counter(
        "radius_packets_total",
        {"operation": operation, "direction": direction, "auth_type": auth_type},
    )


def record_accounting_packet(packet_type: str, auth_type: str = "UNKNOWN"):
    """Записать метрику аккаунтинг пакета"""
    metrics.increment_counter(
        "radius_accounting_packets_total",
        {"packet_type": packet_type, "auth_type": auth_type},
    )


def record_error(error_type: str, operation: str):
    """Записать метрику ошибки"""
    metrics.increment_counter(
        "radius_errors_total", {"error_type": error_type, "operation": operation}
    )


def record_external_call(service: str, operation: str, status: str, duration: float):
    """Записать метрику внешнего вызова"""
    metrics.increment_counter(
        "radius_external_calls_total",
        {"service": service, "operation": operation, "status": status},
    )
    metrics.record_timer(
        "radius_external_call_duration",
        duration,
        {"service": service, "operation": operation},
    )


def record_operation_duration(operation: str, duration: float, status: str = "success"):
    """Записать длительность операции"""
    metrics.record_timer(
        "radius_operation_duration",
        duration,
        {"operation": operation, "status": status},
    )


def set_active_sessions(auth_type: str, count: int):
    """Установить количество активных сессий"""
    metrics.set_gauge("radius_active_sessions", count, {"auth_type": auth_type})


def get_metrics_summary() -> Dict[str, Any]:
    """Получить сводку метрик"""
    stats = metrics.get_stats()

    # Добавляем системную информацию
    stats["system"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": time.time() - start_time,
    }

    return stats


# Время запуска для подсчета uptime
start_time = time.time()


def record_radius_request_rate():
    """Записать метрику скорости обработки RADIUS запросов"""
    metrics.increment_counter("radius_requests_per_second_total")


def record_database_operation(
    operation: str, table: str, duration: float, status: str = "success"
):
    """Записать метрику операции с базой данных"""
    metrics.increment_counter(
        "radius_database_operations_total",
        {"operation": operation, "table": table, "status": status},
    )
    metrics.record_timer(
        "radius_database_operation_duration_seconds",
        duration,
        {"operation": operation, "table": table},
    )


def record_session_lifecycle(event: str, auth_type: str = "UNKNOWN"):
    """Записать событие жизненного цикла сессии"""
    metrics.increment_counter(
        "radius_session_lifecycle_total", {"event": event, "auth_type": auth_type}
    )


def record_traffic_volume(direction: str, bytes_count: int, auth_type: str = "UNKNOWN"):
    """Записать объем трафика"""
    metrics.record_histogram(
        "radius_traffic_bytes",
        bytes_count,
        {"direction": direction, "auth_type": auth_type},
    )


def record_connection_pool_usage(
    service: str, active: int, max_connections: int, waiting: int = 0
):
    """Записать использование пула соединений"""
    metrics.set_gauge(f"{service}_pool_active_connections", active)
    metrics.set_gauge(f"{service}_pool_max_connections", max_connections)
    if waiting > 0:
        metrics.set_gauge(f"{service}_pool_waiting_tasks", waiting)


def record_cache_operation(operation: str, result: str, duration: float):
    """Записать операцию с кешем"""
    metrics.increment_counter(
        "radius_cache_operations_total", {"operation": operation, "result": result}
    )
    metrics.record_timer(
        "radius_cache_operation_duration_seconds", duration, {"operation": operation}
    )


def record_business_metric(
    metric_name: str, value: float, tags: Optional[Dict[str, str]] = None
):
    """Записать бизнес-метрику"""
    if tags is None:
        tags = {}
    metrics.record_histogram(f"radius_business_{metric_name}", value, tags)
