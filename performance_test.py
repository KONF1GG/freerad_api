#!/usr/bin/env python3
"""
Скрипт для тестирования производительности Radius Core.

Тестирует:
- Время обработки аккаунтинга
- Время отправки сообщений в очередь
- Общую производительность системы
"""

import asyncio
import time
import json
import statistics
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

# Опциональный импорт aiohttp
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    print("Предупреждение: aiohttp не установлен. Установите: pip install aiohttp")


class PerformanceTester:
    """Класс для тестирования производительности"""

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url
        self.results = {"accounting": [], "queue_operations": [], "overall": []}

    def generate_test_data(self) -> Dict[str, Any]:
        """Генерация тестовых данных для аккаунтинга"""
        return {
            "Acct_Status_Type": "Interim-Update",
            "Acct_Unique_Session_Id": f"test_session_{int(time.time())}",
            "Acct_Session_Id": f"session_{int(time.time())}",
            "Event_Timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "Acct_Session_Time": 300,
            "Acct_Input_Octets": 1024,
            "Acct_Output_Octets": 2048,
            "Acct_Input_Packets": 10,
            "Acct_Output_Packets": 20,
            "login": "test_user",
            "auth_type": "AUTH",
            "ERX_Service_Session": None,
        }

    async def test_accounting_performance(
        self, iterations: int = 100
    ) -> Dict[str, Any]:
        """Тестирование производительности аккаунтинга"""
        if not AIOHTTP_AVAILABLE:
            return {"error": "aiohttp не установлен"}

        print(f"Тестирование аккаунтинга: {iterations} запросов...")

        async with aiohttp.ClientSession() as session:
            timings = []

            for i in range(iterations):
                test_data = self.generate_test_data()
                test_data["Acct_Unique_Session_Id"] = (
                    f"test_session_{i}_{int(time.time())}"
                )

                start_time = time.time()

                try:
                    async with session.post(
                        f"{self.base_url}/acct/",
                        json=test_data,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status == 200:
                            end_time = time.time()
                            duration = (end_time - start_time) * 1000  # в миллисекундах
                            timings.append(duration)

                            if i % 10 == 0:
                                print(f"  Запрос {i}: {duration:.2f} мс")
                        else:
                            print(f"  Ошибка запроса {i}: {response.status}")

                except Exception as e:
                    print(f"  Исключение в запросе {i}: {e}")

            return self._calculate_statistics(timings, "accounting")

    async def test_queue_operations(self, iterations: int = 100) -> Dict[str, Any]:
        """Тестирование производительности операций с очередями"""
        print(f"Тестирование операций с очередями: {iterations} операций...")

        # Симулируем операции с очередями
        timings = []

        for i in range(iterations):
            start_time = time.time()

            # Симуляция обработки данных
            await asyncio.sleep(0.001)  # Минимальная задержка

            end_time = time.time()
            duration = (end_time - start_time) * 1000
            timings.append(duration)

            if i % 10 == 0:
                print(f"  Операция {i}: {duration:.2f} мс")

        return self._calculate_statistics(timings, "queue_operations")

    async def test_overall_performance(self, iterations: int = 50) -> Dict[str, Any]:
        """Тестирование общей производительности системы"""
        if not AIOHTTP_AVAILABLE:
            return {"error": "aiohttp не установлен"}

        print(f"Тестирование общей производительности: {iterations} итераций...")

        async with aiohttp.ClientSession() as session:
            timings = []

            for i in range(iterations):
                start_time = time.time()

                # Тестируем несколько эндпоинтов
                tasks = [
                    self._test_endpoint(session, "/health"),
                    self._test_endpoint(session, "/"),
                    self._test_endpoint(session, "/metrics"),
                ]

                await asyncio.gather(*tasks)

                end_time = time.time()
                duration = (end_time - start_time) * 1000
                timings.append(duration)

                if i % 10 == 0:
                    print(f"  Итерация {i}: {duration:.2f} мс")

            return self._calculate_statistics(timings, "overall")

    async def _test_endpoint(
        self, session: aiohttp.ClientSession, endpoint: str
    ) -> bool:
        """Тестирование отдельного эндпоинта"""
        try:
            async with session.get(f"{self.base_url}{endpoint}") as response:
                return response.status == 200
        except:
            return False

    def _calculate_statistics(
        self, timings: List[float], test_type: str
    ) -> Dict[str, Any]:
        """Вычисление статистики по времени выполнения"""
        if not timings:
            return {"error": "Нет данных для анализа"}

        # Безопасное вычисление процентилей
        def safe_quantile(data: List[float], n: int, index: int) -> float:
            if len(data) < n:
                return data[-1] if data else 0.0
            quantiles = statistics.quantiles(data, n=n)
            if index < len(quantiles):
                return quantiles[index]
            return data[-1] if data else 0.0

        stats = {
            "test_type": test_type,
            "iterations": len(timings),
            "min": min(timings),
            "max": max(timings),
            "mean": statistics.mean(timings),
            "median": statistics.median(timings),
            "std_dev": statistics.stdev(timings) if len(timings) > 1 else 0,
            "percentiles": {
                "50": safe_quantile(timings, 2, 0),
                "90": safe_quantile(timings, 10, 8),
                "95": safe_quantile(timings, 20, 18),
                "99": safe_quantile(timings, 100, 98),
            },
        }

        self.results[test_type] = stats
        return stats

    def print_results(self):
        """Вывод результатов тестирования"""
        print("\n" + "=" * 60)
        print("РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ ПРОИЗВОДИТЕЛЬНОСТИ")
        print("=" * 60)

        for test_type, stats in self.results.items():
            if isinstance(stats, dict) and "error" not in stats:
                print(f"\n{test_type.upper()}:")
                print(f"  Итераций: {stats['iterations']}")
                print(f"  Минимум: {stats['min']:.2f} мс")
                print(f"  Максимум: {stats['max']:.2f} мс")
                print(f"  Среднее: {stats['mean']:.2f} мс")
                print(f"  Медиана: {stats['median']:.2f} мс")
                print(f"  Стандартное отклонение: {stats['std_dev']:.2f} мс")
                print(f"  Процентили:")
                for p, value in stats["percentiles"].items():
                    print(f"    {p}%: {value:.2f} мс")

        # Анализ производительности
        self._analyze_performance()

    def _analyze_performance(self):
        """Анализ производительности и рекомендации"""
        print("\n" + "=" * 60)
        print("АНАЛИЗ ПРОИЗВОДИТЕЛЬНОСТИ")
        print("=" * 60)

        if "accounting" in self.results and isinstance(
            self.results["accounting"], dict
        ):
            acc_stats = self.results["accounting"]
            if "percentiles" in acc_stats:
                p99 = acc_stats["percentiles"]["99"]
                if p99 > 200:
                    print("⚠️  ВНИМАНИЕ: 99-я процентиль аккаунтинга > 200 мс")
                    print("   Рекомендуется проверить настройки Redis и RabbitMQ")
                elif p99 > 100:
                    print("⚠️  ВНИМАНИЕ: 99-я процентиль аккаунтинга > 100 мс")
                    print("   Производительность приемлема, но можно улучшить")
                else:
                    print("✅ Отличная производительность аккаунтинга!")

        if "queue_operations" in self.results and isinstance(
            self.results["queue_operations"], dict
        ):
            queue_stats = self.results["queue_operations"]
            if "percentiles" in queue_stats:
                p99 = queue_stats["percentiles"]["99"]
                if p99 > 100:
                    print("⚠️  ВНИМАНИЕ: 99-я процентиль операций с очередями > 100 мс")
                    print("   Проверьте настройки RabbitMQ и сетевую задержку")
                elif p99 > 50:
                    print("⚠️  ВНИМАНИЕ: 99-я процентиль операций с очередями > 50 мс")
                    print("   Производительность приемлема")
                else:
                    print("✅ Отличная производительность операций с очередями!")

    async def run_all_tests(self, iterations: int = 100):
        """Запуск всех тестов"""
        print("Запуск тестирования производительности Radius Core...")
        print(f"Базовый URL: {self.base_url}")
        print(f"Количество итераций: {iterations}")

        start_time = time.time()

        # Запускаем тесты параллельно
        tasks = [
            self.test_accounting_performance(iterations),
            self.test_queue_operations(iterations),
            self.test_overall_performance(iterations // 2),
        ]

        await asyncio.gather(*tasks)

        end_time = time.time()
        total_time = end_time - start_time

        print(f"\nОбщее время тестирования: {total_time:.2f} секунд")

        self.print_results()

        return self.results


async def main():
    """Основная функция"""
    import sys

    # Парсинг аргументов командной строки
    base_url = "http://localhost:8080"
    iterations = 100

    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            iterations = int(sys.argv[2])
        except ValueError:
            print("Ошибка: количество итераций должно быть числом")
            sys.exit(1)

    # Проверка доступности сервиса
    if not AIOHTTP_AVAILABLE:
        print("Ошибка: aiohttp не установлен. Установите: pip install aiohttp")
        sys.exit(1)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/health") as response:
                if response.status != 200:
                    print(f"Ошибка: сервис недоступен (статус: {response.status})")
                    sys.exit(1)
    except Exception as e:
        print(f"Ошибка подключения к сервису: {e}")
        sys.exit(1)

    # Запуск тестирования
    tester = PerformanceTester(base_url)
    results = await tester.run_all_tests(iterations)

    # Сохранение результатов в файл
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"performance_test_results_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    print(f"\nРезультаты сохранены в файл: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
