"""Проверки слайда стресс-галереи (07_TESTING.md, 2; ТЗ 7.1, 7.3; 05_LAYOUTS.md, 8).

По готовому PPTX (python-pptx) и PNG страницы PDF:
- фигуры в пределах слайда, текст — в полях (декор и картинки «в край» — исключение);
- шрифт Arial, кегль из типошкалы темы, у фигур нет <p:style>, автоподбор выключен;
- текст не обрезан fitter'ом на заполнениях min и typical;
- заполненность зоны контента 40–75% (typical, слайды с пунктами и панелями);
- доля цвета темы на слайде: титул и финал ≥ 35%, с диаграммой ≥ 10%, остальные ≥ 6%;
- у диаграмм столбцов и линий — подписи значений.
"""

import math
from functools import lru_cache

from pptx.oxml.ns import qn
from pptx.util import Emu

from core.fitting.metrics import line_height, text_width, wrap
from core.models.theme import NEUTRAL_TOKENS, Theme

EMU = 6350
ZONE = (80, 280, 1760, 640)        # зона контента [x, y, w, h]
# Пороги 05_LAYOUTS.md, 8; «остальные контентные» — 4% вместо предложенных 6% по галерее
# сессии 4 (D-065): списки и выводы в светлых темах держат цвет на номерах и декоре, медиана — ≥ 8%
COLOR_THRESHOLDS = {"cover": 35.0, "chart": 10.0, "content": 4.0}
DECK_MEDIAN = 8.0
FILL_RANGE = (40.0, 75.0)
FILL_KINDS = {"bullets", "process", "conclusion", "metrics", "comparison"}
FILL_EXEMPT = {"metrics.big_number"}       # градиентная панель на всю зону — так задумано
DECOR_PREFIXES = ("decor", "header:")


def units(v) -> float:
    return int(v) / EMU


def box_of(shape) -> tuple[float, float, float, float]:
    return units(shape.left), units(shape.top), units(shape.width), units(shape.height)


def is_decor(shape) -> bool:
    return shape.name.startswith(DECOR_PREFIXES)


# ── Геометрия и шрифты ──────────────────────────────────────────────────────

def geometry_errors(slide_obj, theme: Theme) -> list[str]:
    errors = []
    sizes = {pt for pt in theme.typescale.values()}
    for shape in slide_obj.shapes:
        if shape._element.find(qn("p:style")) is not None:
            errors.append(f"{shape.name}: <p:style> (тень темы)")
        if is_decor(shape) or shape.name == "gallery_label":
            continue
        x, y, w, h = box_of(shape)
        edge = shape.name.startswith(("image", "band", "header", "panel")) or shape.shape_type == 13
        if x < -0.5 or y < -0.5 or x + w > 1920.5 or y + h > 1080.5:
            errors.append(f"{shape.name}: за границей слайда {x:.0f},{y:.0f},{w:.0f},{h:.0f}")
        if shape.has_text_frame and shape.text_frame.text.strip() and not edge:
            if x < 79.5 or x + w > 1840.5 or y < 39.5 or y + h > 1060.5:
                errors.append(f"{shape.name}: текст вне полей {x:.0f},{y:.0f},{w:.0f},{h:.0f}")
            tf = shape.text_frame
            body_pr = tf._txBody.find(qn("a:bodyPr"))
            if body_pr is not None and (body_pr.find(qn("a:normAutofit")) is not None
                                        or body_pr.find(qn("a:spAutoFit")) is not None):
                errors.append(f"{shape.name}: автоподбор размера")
            for p in tf.paragraphs:
                for run in p.runs:
                    if run.font.name != "Arial":
                        errors.append(f"{shape.name}: шрифт {run.font.name}")
                    if run.font.size is not None and round(run.font.size.pt) not in sizes:
                        errors.append(f"{shape.name}: кегль {run.font.size.pt} не из типошкалы")
    return errors


def chart_errors(slide_obj) -> list[str]:
    errors = []
    for shape in slide_obj.shapes:
        if getattr(shape, "has_chart", False) and shape.has_chart:
            chart = shape.chart
            kind = shape.name.split(":")[-1]
            points = len(chart.plots[0].categories) * len(chart.plots[0].series)
            if kind in ("column", "line") and points <= 24 and not chart.plots[0].has_data_labels:
                errors.append(f"{shape.name}: нет подписей значений")
    return errors


# ── Заполненность зоны контента ─────────────────────────────────────────────

CELL = 10


def _text_extent(shape) -> list[tuple[float, float, float, float]]:
    """Прямоугольники, которые реально занимает текст фигуры (по метрикам fitter'а)."""
    x, y, w, h = box_of(shape)
    tf = shape.text_frame
    lines = []
    for p in tf.paragraphs:
        run = p.runs[0] if p.runs else None
        if not run or not p.text.strip():
            continue
        pt = round(run.font.size.pt) if run.font.size else 18
        bold = bool(run.font.bold)
        for line in wrap(p.text, w, pt, bold):
            lines.append((text_width(line, pt, bold), line_height(pt), p.alignment))
    total = sum(lh for _, lh, _ in lines)
    anchor = tf.vertical_anchor
    top = y + {None: 0, 1: 0, 3: (h - total) / 2, 4: h - total}.get(anchor, 0) if anchor else y
    out = []
    for lw, lh, align in lines:
        lx = x + (w - lw) / 2 if align == 2 else x + w - lw if align == 3 else x
        out.append((lx, top, lw, lh))
        top += lh
    return out


def fill_share(slide_obj) -> float:
    """Доля зоны контента (сетка 10×10), занятой фигурами, картинками, диаграммами и текстом."""
    zx, zy, zw, zh = ZONE
    cols, rows = int(zw // CELL), int(zh // CELL)
    covered = set()

    def mark(x, y, w, h):
        c0, c1 = max(0, int((x - zx) // CELL)), min(cols, int(math.ceil((x + w - zx) / CELL)))
        r0, r1 = max(0, int((y - zy) // CELL)), min(rows, int(math.ceil((y + h - zy) / CELL)))
        for r in range(r0, r1):
            for c in range(c0, c1):
                covered.add((r, c))

    for shape in slide_obj.shapes:
        if is_decor(shape) or shape.name in ("title", "footnote", "page_number", "footer", "gallery_label"):
            continue
        if shape.has_text_frame and not shape.text_frame.text.strip() and shape.shape_type == 17:
            continue
        if shape.shape_type == 17 or (shape.has_text_frame and shape.name.endswith((":number",))):
            for ext in _text_extent(shape):
                mark(*ext)
            continue
        mark(*box_of(shape))
    return 100.0 * len(covered) / (cols * rows)


# ── Доля цвета темы (PNG) ───────────────────────────────────────────────────

def _lab(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # noqa: E731
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _hex(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _segment(a, b, steps=8):
    return [tuple(a[i] + (b[i] - a[i]) * k / steps for i in range(3)) for k in range(steps + 1)]


@lru_cache(maxsize=16)
def _references(theme_id: str):
    from core.models.theme import load_theme
    t = load_theme(theme_id)
    neutral_hex = {t.colors[k] for k in NEUTRAL_TOKENS}
    color_tokens = [k for k in ("primary", "brand", "brand_2", "accent", "positive", "negative", "on_brand",
                                "on_primary") if t.colors[k] not in neutral_hex]
    color_hex = [t.colors[k] for k in color_tokens] + [c for c in t.chart if c not in neutral_hex]
    neutral = [_lab(_hex(h)) for h in neutral_hex]
    nh = sorted(neutral_hex)
    for i in range(len(nh)):          # сглаживание текста — смесь нейтральных цветов
        for j in range(i + 1, len(nh)):
            neutral += [_lab(c) for c in _segment(_hex(nh[i]), _hex(nh[j]))]
    colored = [_lab(_hex(h)) for h in color_hex]
    colored += [_lab(c) for c in _segment(_hex(t.colors["brand"]), _hex(t.colors["brand_2"]))]
    return neutral, colored


_memo: dict = {}


def _is_color(theme_id: str, rgb) -> bool:
    key = (theme_id, rgb)
    if key in _memo:
        return _memo[key]
    neutral, colored = _references(theme_id)
    lab = _lab(rgb)
    dn = min(math.dist(lab, n) for n in neutral)
    dc = min(math.dist(lab, c) for c in colored)
    result = dc < dn and dc < 30
    _memo[key] = result
    return result


MASK = (1, 2, 3)
SHARE_W, SHARE_H = 480, 270      # 4 единицы сетки на пиксель: линии обводок не растворяются


def color_share(png, theme_id: str, exclude=()) -> float:
    """Доля пикселей цветов темы (primary, brand-градиент, accent, диаграммы и их оттенки) на
    слайде без площади картинок (exclude — их боксы в единицах сетки). png — PIL.Image страницы."""
    from PIL import ImageDraw
    img = png.convert("RGB").resize((SHARE_W, SHARE_H))
    draw = ImageDraw.Draw(img)
    k = SHARE_W / 1920
    for x, y, w, h in exclude:
        draw.rectangle([x * k, y * k, (x + w) * k - 1, (y + h) * k - 1], fill=MASK)
    total = colored = 0
    for count, rgb in img.getcolors(SHARE_W * SHARE_H):
        if rgb == MASK:
            continue
        total += count
        if _is_color(theme_id, tuple(v // 4 * 4 for v in rgb)):
            colored += count
    return 100.0 * colored / max(1, total)


def color_threshold(kind: str, variant: str, theme: Theme | None = None) -> float:
    """Порог доли цвета слайда. Тема может задать свои (gallery: в YAML) — спокойные темы с одним
    акцентом и «воздухом» по решению владельца (D-070)."""
    own = (theme.gallery if theme is not None else {}) or {}
    if kind in ("title", "closing"):
        return float(own.get("cover_min", COLOR_THRESHOLDS["cover"]))
    if kind in ("chart_series", "chart_share"):
        return float(own.get("chart_min", COLOR_THRESHOLDS["chart"]))
    return float(own.get("content_min", COLOR_THRESHOLDS["content"]))


def deck_median(theme: Theme) -> float:
    return float((theme.gallery or {}).get("median_min", DECK_MEDIAN))
