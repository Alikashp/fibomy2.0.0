"""Идентификаторы (04_CONTRACTS.md, 1): колода dk_<ULID>, слайд s01…s20."""

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(now_ms: int | None = None) -> str:
    """ULID: 48 бит времени в мс + 80 случайных бит, Crockford base32, 26 знаков."""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def new_deck_id() -> str:
    return f"dk_{ulid()}"


def slide_id(index: int) -> str:
    return f"s{index:02d}"
