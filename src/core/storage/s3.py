"""Клиент S3-совместимого хранилища (R2, Yandex Object Storage, Selectel, MinIO — D-042).

boto3 синхронный: вызовы — через asyncio.to_thread. Без S3_ENDPOINT_URL хранилище
не используется (enabled() — False).
"""

from config import settings

_client = None


def enabled() -> bool:
    return bool(settings.s3_endpoint_url)


def client():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
    return _client
