"""Theme — тема оформления из themes/<id>.yaml (04_CONTRACTS.md, 6.2; 05_LAYOUTS.md, 6)."""

from functools import lru_cache

import yaml
from pydantic import BaseModel

from core.paths import THEMES_DIR

# Шаги кегля fitter'а сверху вниз (05_LAYOUTS.md, 2). min — только колонтитул.
STYLE_STEPS = ("hero", "display", "h1", "h2", "h3", "body", "small", "min")
BOLD_STYLES = frozenset({"hero", "display", "h1", "h2", "h3"})
COLOR_TOKENS = ("bg", "surface", "surface_alt", "primary", "accent", "text", "text_muted",
                "on_primary", "border", "positive", "negative")


class Theme(BaseModel):
    id: str
    name: dict[str, str]
    mode: str
    tags: list[str] = []
    free: bool = True
    enabled: bool = True
    fonts: dict[str, str]
    colors: dict[str, str]
    chart: list[str]
    typescale: dict[str, int]
    style: dict
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
def load_theme(theme_id: str) -> Theme:
    path = THEMES_DIR / f"{theme_id}.yaml"
    with path.open(encoding="utf-8") as f:
        return Theme.model_validate(yaml.safe_load(f))


def all_theme_ids() -> list[str]:
    return sorted(p.stem for p in THEMES_DIR.glob("*.yaml"))


def enabled_theme_ids() -> list[str]:
    return [t for t in all_theme_ids() if load_theme(t).enabled]
