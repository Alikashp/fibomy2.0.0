"""Ключи API: формат, хэш, разбор заголовка (D-056).

Ключ — «fib_» + 43 случайных знака (256 бит). В БД — только SHA-256 ключа: соль не
нужна, ключ случайный и длинный, перебор по хэшу бесполезен. Ключ показывается один
раз при создании (python -m api.keys create) и восстановлению не подлежит —
только выпуск нового (rotate).
"""

import hashlib
import hmac
import secrets

KEY_PREFIX = "fib_"
WEBHOOK_SECRET_PREFIX = "whsec_"


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def new_webhook_secret() -> str:
    return WEBHOOK_SECRET_PREFIX + secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_prefix(key: str) -> str:
    """Начало ключа для списка клиентов: узнать ключ, не раскрывая его."""
    return key[:12]


def parse_authorization(header: str | None) -> str | None:
    """«Bearer fib_…» → ключ; иначе None."""
    if not header:
        return None
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value if value.startswith(KEY_PREFIX) and 20 <= len(value) <= 128 else None


def sign(secret: str, body: bytes) -> str:
    """Подпись тела webhook: заголовок X-Fibonacci-Signature: sha256=<hex>."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
