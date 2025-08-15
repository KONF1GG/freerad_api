#!/usr/bin/env python3
"""
Тест для проверки зависимостей Redis и RabbitMQ
"""

import asyncio


async def test_dependencies():
    """Простой тест для проверки зависимостей"""
    from dependencies import get_redis_connection, get_rabbitmq_connection

    print("Тестирование Redis подключения...")
    async for redis in get_redis_connection():
        await redis.ping()
        print("✓ Redis подключение работает")
        break

    print("Тестирование RabbitMQ подключения...")
    async for channel in get_rabbitmq_connection():
        print("✓ RabbitMQ подключение работает")
        break

    print("Все зависимости работают корректно!")


if __name__ == "__main__":
    asyncio.run(test_dependencies())
