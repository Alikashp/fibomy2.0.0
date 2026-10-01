"""Общий клиент Redis для хранилищ нового движка: файлы колод API, прогресс колоды.

Байты без decode_responses. Тесты подменяют клиент через set_client (fakeredis).
"""

from config import settings

_client = None


def get_redis():
    global _client
    if _client is None:
        import redis.asyncio as aioredis
        _client = aioredis.from_url(settings.redis_url)
    return _client


def set_client(client) -> None:
    global _client
    _client = client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
