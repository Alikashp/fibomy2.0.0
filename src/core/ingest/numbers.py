"""Числа в тексте и ячейках: разбор и нормализация (ТЗ 3.3.3, 4.8.2).

«47,3» = «47.3»; «1 150» и «1 150» (неразрывный пробел) = 1150; «−6» = «-6»;
«+41%» → 41 с признаком процента. Единица измерения (млн руб., минуты) в
значение не входит — она остаётся в подписи или в заголовке колонки.
"""

import re

_MINUS = "−–‒"
_SPACES = "   "

# Число целиком: знак, группы разрядов через пробел, дробная часть, процент
_CELL_RE = re.compile(r"^\s*([+\-−–]?)\s?(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?\s*(%?)\s*$")
_FIND_RE = re.compile(r"(?<![\d.,])([+\-−–]?)(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?(?![\d])(\s?%)?")


def _to_float(sign: str, whole: str, frac: str | None) -> float:
    digits = re.sub(r"\D", "", whole)
    value = float(f"{digits}.{frac}" if frac else digits)
    return -value if sign and sign in "-" + _MINUS else value


def parse_cell(raw: str) -> tuple[float | None, bool]:
    """(значение, это процент). Ячейка не число («новый канал») — (None, False)."""
    m = _CELL_RE.match(raw or "")
    if not m:
        return None, False
    sign, whole, frac, pct = m.groups()
    return _to_float(sign, whole, frac), bool(pct)


def find_numbers(text: str) -> list[tuple[float, bool, str]]:
    """Все числа в тексте: (значение, процент, как написано)."""
    out = []
    for m in _FIND_RE.finditer(text or ""):
        sign, whole, frac, pct = m.groups()
        # «2026–2027»: дефис между числами — диапазон, а не минус
        start = m.start()
        if sign and start > 0 and (text[start - 1].isdigit() or text[start - 1].isalpha()):
            sign = ""
        out.append((_to_float(sign, whole, frac), bool(pct), m.group(0).strip()))
    return out


def format_number(value: float, language: str = "ru") -> str:
    """Число для подписей диаграмм: запятая в ru/kk/uz, группы разрядов пробелом."""
    if value == int(value):
        text = f"{int(value):,}".replace(",", " ")
    else:
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
        whole, _, frac = text.partition(".")
        text = whole.replace(",", " ") + ("," if language in ("ru", "kk", "uz") else ".") + frac
    return text.replace("-", "−")


def same_number(a: float, b: float) -> bool:
    return abs(abs(a) - abs(b)) < 1e-6 * max(1.0, abs(a))
