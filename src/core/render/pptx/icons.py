"""Иконки фазы 1: PNG-маска из assets/icons/ → PNG цвета токена темы."""

import io
from functools import lru_cache

import yaml
from PIL import Image

from core.paths import ICONS_DIR, PROMPTS_V2_DIR

DEFAULT_ICON = "bulb"


@lru_cache(maxsize=1)
def _icons() -> dict:
    with (PROMPTS_V2_DIR / "icons.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def icon_names() -> tuple[str, ...]:
    """Иконки, из которых выбирает модель (enum схемы CONTENT)."""
    return tuple(_icons()["icons"])


def system_icons() -> tuple[str, ...]:
    """Иконки макетов (const:x в YAML) — модель их не выбирает."""
    return tuple(_icons().get("system", []))


@lru_cache(maxsize=256)
def icon_png(name: str, color: str) -> bytes:
    """Иконка name цветом #RRGGBB, прозрачный фон. Неизвестное имя — иконка по умолчанию."""
    if name not in icon_names() and name not in system_icons():
        name = DEFAULT_ICON
    mask = Image.open(ICONS_DIR / f"{name}.png").convert("LA").getchannel("A")
    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGBA", mask.size, rgb + (0,))
    img.putalpha(mask)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
