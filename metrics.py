import time
import logging
from typing import Dict, Any, Optional
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Сборщик метрик для RADIUS сервиса"""

    def __init__(self):
        self.counters = defaultdict(int)
        self.timers = defaultdict(list)
        self.gauges = defaultdict(float)
        self.histograms = defaultdict(list)

    def increment_counter(self, name: str, tags: Optional[Dict[str, str]] = None):
        """Увеличить счетчик"""
        if tags is None:
            tags = {}
        key = self._make_key(name, tags)
        self.counters[key] += 1

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Установить значение gauge"""
        if tags is None:
            tags = {}
        key = self._make_key(name, tags)
        self.gauges[key] = value

    def record_timer(
        self, name: str, duration: float, tags: Optional[Dict[str, str]] = None
    ):
        """Записать время выполнения"""
        if tags is None:
            tags = {}
        key = self._make_key(name, tags)
        self.timers[key].append(duration)

        # Ограничиваем историю
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

        # Ограничиваем историю
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
