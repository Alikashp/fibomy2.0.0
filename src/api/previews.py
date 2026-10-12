"""PNG-превью тем для GET /v1/themes (D-077): файлы assets/theme_previews/<id>.png, собранные из
стресс-галереи скриптом scripts/build_theme_previews.py при сборке, — не на лету. Читаются один раз."""

import hashlib
from dataclasses import dataclass
from functools import lru_cache

from core.paths import ROOT

PREVIEWS_DIR = ROOT / "assets" / "theme_previews"


@dataclass(frozen=True)
class Preview:
    data: bytes
    etag: str


@lru_cache(maxsize=None)
def preview(theme_id: str) -> Preview | None:
    from core.models.theme import enabled_theme_ids
    if theme_id not in enabled_theme_ids():
        return None
    path = PREVIEWS_DIR / f"{theme_id}.png"
    if not path.is_file():
        return None
    data = path.read_bytes()
    return Preview(data, hashlib.sha256(data).hexdigest()[:12])
