"""Готовые файлы колод API: PPTX и PDF (D-058).

- S3 настроен (S3_ENDPOINT_URL) — объекты decks/ГГГГ/ММ/ДД/<deck_id>.<ext>, ссылки
  живут 24 ч (срок проверяет API; удаление — правило жизненного цикла бакета на
  префикс decks/, например 2 дня);
- нет S3 — Redis с TTL 1 ч: временное решение до S3 (сессия 5).

Клиент скачивает файлы только через API с ключом (GET /v1/decks/{id}/files/{fmt});
ссылки ref наружу не отдаются. Бот файлы не хранит — отправляет сразу в Telegram.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from core.storage import s3
from core.storage.redis import get_redis
from config import settings

logger = logging.getLogger(__name__)

REDIS_TTL_SECONDS = 60 * 60
S3_TTL_SECONDS = 24 * 60 * 60
FORMATS = ("pptx", "pdf")
MIME = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}
_REDIS_PREFIX = "deckfile:"


def backend() -> str:
    return "s3" if s3.enabled() else "redis"


async def put_deck_files(deck_id: str, files: dict[str, bytes | None],
                         now: datetime | None = None) -> dict:
    """Сохраняет файлы колоды → {"pptx": ref, "pdf": ref | None, "expires_at": iso, "backend": …}
    для decks.files."""
    now = now or datetime.now(timezone.utc)
    out: dict = {"backend": backend()}
    if out["backend"] == "s3":
        ttl = S3_TTL_SECONDS
        for fmt in FORMATS:
            data = files.get(fmt)
            if data is None:
                out[fmt] = None
                continue
            key = f"decks/{now:%Y/%m/%d}/{deck_id}.{fmt}"
            await asyncio.to_thread(s3.client().put_object, Bucket=settings.s3_bucket, Key=key,
                                    Body=data, ContentType=MIME[fmt])
            out[fmt] = f"s3:{key}"
    else:
        ttl = REDIS_TTL_SECONDS
        redis = get_redis()
        for fmt in FORMATS:
            data = files.get(fmt)
            if data is None:
                out[fmt] = None
                continue
            key = f"{_REDIS_PREFIX}{deck_id}:{fmt}"
            await redis.set(key, data, ex=ttl)
            out[fmt] = f"redis:{key}"
    out["expires_at"] = (now + timedelta(seconds=ttl)).isoformat()
    return out


async def get_deck_file(ref: str) -> bytes | None:
    """Байты файла; None — файла уже нет (истёк TTL или удалён)."""
    backend_name, _, key = ref.partition(":")
    if backend_name == "redis":
        return await get_redis().get(key)
    if backend_name == "s3":
        def read():
            try:
                obj = s3.client().get_object(Bucket=settings.s3_bucket, Key=key)
            except Exception as e:  # NoSuchKey и прочие ошибки клиента
                if getattr(e, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                    return None
                raise
            return obj["Body"].read()
        return await asyncio.to_thread(read)
    raise ValueError(f"Unknown file ref: {ref}")
