"""Theme — тема оформления из themes/<id>.yaml (04_CONTRACTS.md, 6.2; 05_LAYOUTS.md, 6)."""

from functools import lru_cache

import yaml
from pydantic import BaseModel

from core.paths import THEMES_DIR

# Шаги кегля fitter'а сверху вниз (05_LAYOUTS.md, 2). min — только колонтитул.
STYLE_STEPS = ("hero", "display", "h1", "h2", "h3", "body", "small", "min")
BOLD_STYLES = frozenset({"hero", "display", "h1", "h2", "h3"})
COLOR_TOKENS = ("bg", "surface", "surface_alt", "primary", "on_primary", "brand", "brand_2", "on_brand",
                "accent", "on_accent", "paper", "on_paper", "text", "text_muted", "border", "on_status", "positive",
                "negative")
# Нейтральные токены: фон, подложки, линии, текст — не «цвет темы» в проверке доли цвета
NEUTRAL_TOKENS = ("bg", "surface", "surface_alt", "paper", "border", "text", "text_muted", "on_paper")
DEFAULT_THEME = "business_slate"


class Theme(BaseModel):
    id: str
    name: dict[str, str]
    mode: str
    tags: list[str] = []
    free: bool = True
    enabled: bool = True
    replaces: list[str] = []
    fonts: dict[str, str]
    colors: dict[str, str]
    chart: list[str]
    typescale: dict[str, int]
    style: dict
    decor: dict[str, list[dict]] = {}
    gallery: dict = {}          # свои пороги доли цвета в стресс-галерее (tests/gallery, D-070)
    contrast_pairs: list[tuple[str, str]]

    def color(self, token: str) -> str:
        """Токен темы → #RRGGBB. chart_1…chart_6 — цвета серий."""
        if token.startswith("chart_"):
            return self.chart[(int(token.split("_")[1]) - 1) % len(self.chart)]
        return self.colors[token]

    def pt(self, style: str) -> int:
        return self.typescale[style]


def contrast_ratio(a: str, b: str) -> float:
    """Контраст WCAG 2.x двух цветов #RRGGBB."""
    def lum(hex_color: str) -> float:
        h = hex_color.lstrip("#")
        chans = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255
            chans.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        r, g, b_ = chans
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@lru_cache(maxsize=None)
def _load(theme_id: str) -> Theme:
    path = THEMES_DIR / f"{theme_id}.yaml"
    with path.open(encoding="utf-8") as f:
        return Theme.model_validate(yaml.safe_load(f))


@lru_cache(maxsize=None)
def legacy_theme_ids() -> dict[str, str]:
    """Старые id тем (сессии 1–3) → новые: поле replaces в themes/*.yaml (D-063).
    Старые id принимают бот (профиль), API (запрос) и рендерер (DeckSpec прошлых колод)."""
    out = {}
    for theme_id in all_theme_ids():
        for old in _load(theme_id).replaces:
            out[old] = theme_id
    return out


def resolve_theme_id(theme_id: str | None) -> str:
    """Id темы → действующий id: новые как есть, старые — по соответствию, неизвестные — тема по умолчанию."""
    if theme_id in all_theme_ids():
        return theme_id
    return legacy_theme_ids().get(theme_id or "", DEFAULT_THEME)


def load_theme(theme_id: str) -> Theme:
    return _load(resolve_theme_id(theme_id))


@lru_cache(maxsize=1)
def all_theme_ids() -> list[str]:
    """Порядок — как в сводке бота и в GET /v1/themes: тема по умолчанию первой."""
    ids = sorted(p.stem for p in THEMES_DIR.glob("*.yaml"))
    return sorted(ids, key=lambda t: (t != DEFAULT_THEME, THEME_ORDER.index(t) if t in THEME_ORDER else 99, t))


THEME_ORDER = ["business_slate", "ember_dark", "sunny_cream", "mint_coral"]


def enabled_theme_ids() -> list[str]:
    return [t for t in all_theme_ids() if load_theme(t).enabled]
