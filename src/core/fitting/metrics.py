"""Измерение и перенос текста по метрикам Liberation Sans (= Arial, D-027).

Единицы — сетка 1920×1080: 1 pt = 2 единицы, строка = 1,2 × кегль (спайк, находка 4).

Перенос повторяет LibreOffice и PowerPoint (сверено на 300 текстах, сессия 5, D-071):
- пробелы между словами — как в тексте («•  пункт» — два пробела, не один);
- неразрывный пробел (U+00A0, U+202F) и word joiner (U+2060) — часть слова: «92 %»,
  «60⁠–⁠90» не рвутся; word joiner нулевой ширины;
- после дефиса и тире внутри слова перенос возможен («ИТ-|стартап»);
- строка считается влезшей с запасом WIDTH_MARGIN: у рендеров расхождение метрик до 0,3%.

Слово, которое не помещается в ширину слота целиком, перенос по знакам не спасает
(«Непротиворечивост / ь»): wrap() рвёт его, а overlong_words() сообщает fitter'у, что
стиль не подходит.
"""

import re
from functools import lru_cache

from PIL import ImageFont

from core.models.layout import LINE_HEIGHT, UNITS_PER_PT
from core.paths import FONTS_DIR

_SCALE = 8  # шрифт меряется в увеличенном размере — точнее округления Pillow
WIDTH_MARGIN = 0.99
WJ = "⁠"                    # word joiner: в шрифте нет глифа, рендеры его не рисуют
NBSP = " "
_ZERO_WIDTH = str.maketrans("", "", WJ + "​﻿")
_SPACES_RE = re.compile(r"( +)")     # разрыв строки — только на обычном пробеле
_HYPHEN_BREAK_RE = re.compile(r"(?<=[^\s\-–—" + WJ + r"][\-–—])(?=[^\s\-–—" + WJ + r"\d])")


@lru_cache(maxsize=None)
def _font(bold: bool, pt: int) -> ImageFont.FreeTypeFont:
    name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    return ImageFont.truetype(str(FONTS_DIR / name), int(pt * UNITS_PER_PT * _SCALE))


def text_width(text: str, pt: int, bold: bool) -> float:
    """Ширина строки в единицах сетки."""
    return _font(bold, pt).getlength(text.translate(_ZERO_WIDTH)) / _SCALE


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


def _tokens(paragraph: str) -> list[tuple[str, str]]:
    """(разделитель перед куском, кусок): куски — слова и их части после дефиса."""
    out: list[tuple[str, str]] = []
    parts = _SPACES_RE.split(paragraph.strip(" "))
    sep = ""
    for part in parts:
        if not part:
            continue
        if part.startswith(" "):
            sep = part
            continue
        pieces = _HYPHEN_BREAK_RE.split(part)
        for k, piece in enumerate(pieces):
            out.append((sep if k == 0 else "", piece))
        sep = ""
    return out


def words(text: str) -> list[str]:
    """Неразрывные слова текста (дефис внутри слова — точка переноса)."""
    return [piece for paragraph in text.split("\n") for _, piece in _tokens(paragraph)]


def overlong_words(text: str, width: float, pt: int, bold: bool) -> list[str]:
    """Слова, которые не помещаются в ширину слота целиком."""
    limit = width * WIDTH_MARGIN
    return [w for w in words(text) if text_width(w, pt, bold) > limit]


def wrap(text: str, width: float, pt: int, bold: bool) -> list[str]:
    """Перенос по словам, как в PowerPoint и LibreOffice: слово длиннее строки рвётся по знакам."""
    limit = width * WIDTH_MARGIN
    lines: list[str] = []
    for paragraph in text.split("\n"):
        tokens = _tokens(paragraph)
        if not tokens:
            lines.append("")
            continue
        cur = ""
        for sep, piece in tokens:
            candidate = f"{cur}{sep}{piece}" if cur else piece
            if text_width(candidate, pt, bold) <= limit:
                cur = candidate
                continue
            if cur:
                lines.append(cur)
            if text_width(piece, pt, bold) <= limit:
                cur = piece
            else:
                *full, cur = _break_long_word(piece, limit, pt, bold)
                lines.extend(full)
        lines.append(cur)
    return lines


_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text.strip()) if s]
