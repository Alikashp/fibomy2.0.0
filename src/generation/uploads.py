"""
Временное хранилище файлов пользователя между ботом и воркером (ТЗ 4.4, раздел 8 п. 13).

Раньше байты документа (до 20 МБ) лежали в FSM бота и уходили в аргументах
ARQ-задачи. Теперь бот кладёт файл во временный объект и передаёт в задачу
только ссылку; воркер забирает файл и удаляет его после обработки.

Где лежит файл:
- S3, если настроен (S3_ENDPOINT_URL) — ссылка "s3:<key>";
- иначе Redis с TTL — ссылка "redis:<key>". На проде S3 пока не настроен,
  поэтому сейчас работает этот вариант.

Если воркер упал посреди задачи, файл удаляет TTL (Redis). Для S3 на префикс
uploads/ нужно правило жизненного цикла бакета (например, 1 день).
"""

import asyncio
import logging
import uuid

from config import settings

logger = logging.getLogger(__name__)

UPLOAD_TTL_SECONDS = 60 * 60  # задача ждёт в очереди не дольше часа
_REDIS_PREFIX = "upload:"
_S3_PREFIX = "uploads/"

_redis = None


class UploadNotFound(Exception):
    """Файл не найден: истёк TTL или уже удалён."""


def _get_redis():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(settings.redis_url)  # bytes, без decode_responses
    return _redis


def _s3_enabled() -> bool:
    return bool(settings.s3_endpoint_url)


async def put_upload(data: bytes, mime_type: str | None = None) -> str:
    """Сохраняет файл и возвращает ссылку для передачи в задачу."""
    key = uuid.uuid4().hex
    if _s3_enabled():
        from generation.storage import _get_client

        s3_key = f"{_S3_PREFIX}{key}"
        await asyncio.to_thread(
            _get_client().put_object,
            Bucket=settings.s3_bucket,
            Key=s3_key,
            Body=data,
            ContentType=mime_type or "application/octet-stream",
        )
        return f"s3:{s3_key}"

    await _get_redis().set(f"{_REDIS_PREFIX}{key}", data, ex=UPLOAD_TTL_SECONDS)
    return f"redis:{key}"


async def get_upload(ref: str) -> bytes:
    backend, _, key = ref.partition(":")
    if backend == "s3":
        from generation.storage import _get_client

        try:
            obj = await asyncio.to_thread(_get_client().get_object, Bucket=settings.s3_bucket, Key=key)
        except Exception as e:
            raise UploadNotFound(ref) from e
        return await asyncio.to_thread(obj["Body"].read)
    if backend == "redis":
        data = await _get_redis().get(f"{_REDIS_PREFIX}{key}")
        if data is None:
            raise UploadNotFound(ref)
        return data
    raise ValueError(f"Unknown upload ref: {ref}")


async def delete_upload(ref: str) -> None:
    """Удаляет файл. Ошибки только логируются — задачу из-за них не валим."""
    backend, _, key = ref.partition(":")
    try:
        if backend == "s3":
            from generation.storage import _get_client

            await asyncio.to_thread(_get_client().delete_object, Bucket=settings.s3_bucket, Key=key)
        elif backend == "redis":
            await _get_redis().delete(f"{_REDIS_PREFIX}{key}")
    except Exception as e:
        logger.warning("Failed to delete upload", extra={"ref": ref, "error": str(e)})


async def close_uploads() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
