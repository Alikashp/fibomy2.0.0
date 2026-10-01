"""Измерение и перенос текста по метрикам Liberation Sans (= Arial, D-027).

Единицы — сетка 1920×1080: 1 pt = 2 единицы, строка = 1,2 × кегль (спайк, находка 4).
"""

import re
from functools import lru_cache

from PIL import ImageFont

from core.models.layout import LINE_HEIGHT, UNITS_PER_PT
from core.paths import FONTS_DIR

_SCALE = 8  # шрифт меряется в увеличенном размере — точнее округления Pillow


@lru_cache(maxsize=None)
def _font(bold: bool, pt: int) -> ImageFont.FreeTypeFont:
    name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    return ImageFont.truetype(str(FONTS_DIR / name), int(pt * UNITS_PER_PT * _SCALE))


def text_width(text: str, pt: int, bold: bool) -> float:
    """Ширина строки в единицах сетки."""
    return _font(bold, pt).getlength(text) / _SCALE


def line_height(pt: int) -> float:
    return pt * UNITS_PER_PT * LINE_HEIGHT


def _break_long_word(word: str, width: float, pt: int, bold: bool) -> list[str]:
    parts, cur = [], ""
    for ch in word:
        if cur and text_width(cur + ch, pt, bold) > width:
            parts.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def wrap(text: str, width: float, pt: int, bold: bool) -> list[str]:
    """Перенос по словам, как в PowerPoint: слово длиннее строки рвётся по знакам."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        cur = ""
        for word in words:
            candidate = f"{cur} {word}" if cur else word
            if text_width(candidate, pt, bold) <= width:
                cur = candidate
                continue
            if cur:
                lines.append(cur)
            if text_width(word, pt, bold) <= width:
                cur = word
            else:
                *full, cur = _break_long_word(word, width, pt, bold)
                lines.extend(full)
        lines.append(cur)
    return lines


_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text.strip()) if s]
