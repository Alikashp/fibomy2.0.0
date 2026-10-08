"""Нативные фигуры рендерера: заливки (цвет, градиент, прозрачность), фигуры номера,
декоративные фигуры темы (05_LAYOUTS.md, 6.3, 8; D-064).

Всё рисуется фигурами PPTX, которые пользователь перекрашивает в PowerPoint:
пресеты (прямоугольник, овал, пятиугольник) и custGeom (дуги, волны, многоугольники
декора). У каждой фигуры удалён <p:style> — без теней темы (спайк, находка 1).
Декор обрезается по своей области: фигуры не выходят за слайд и за цветную полосу.
"""

import math

from lxml import etree
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from core.models.layout import Box, _num
from core.models.theme import Theme

EMU_PER_UNIT = 6350
CIRCLE_STEPS = 96        # точек на полный круг у дуг и кругов декора


def u(value: float) -> Emu:
    return Emu(int(round(value * EMU_PER_UNIT)))


def rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#"))


def no_style(shape) -> None:
    """<p:style> ссылается на эффекты темы (тень) — убираем, цвета заданы явно."""
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def _alpha(color_format, alpha: float | None) -> None:
    """Прозрачность заливки: <a:alpha> внутри <a:srgbClr> (python-pptx её не умеет)."""
    if alpha is None or alpha >= 1:
        return
    srgb = color_format._color._xClr
    for old in srgb.findall(qn("a:alpha")):
        srgb.remove(old)
    el = etree.SubElement(srgb, qn("a:alpha"))
    el.set("val", str(int(round(max(0.0, alpha) * 100000))))


def gradient_tokens(fill) -> tuple[str, str] | None:
    """fill: «$brand» — градиент brand → brand_2; {gradient: [a, b]} — градиент токенов."""
    if fill == "$brand":
        return "brand", "brand_2"
    if isinstance(fill, dict) and fill.get("gradient"):
        a, b = fill["gradient"]
        return a, b
    return None


def apply_fill(shape, theme: Theme, fill, alpha: float | None = None, angle: float | None = None) -> None:
    """Заливка фигуры: токен темы, «$brand» / {gradient: [a, b]} или None (без заливки)."""
    if fill is None or fill == "none":
        shape.fill.background()
        return
    grad = gradient_tokens(fill)
    if grad:
        shape.fill.gradient()
        shape.fill.gradient_angle = float(angle if angle is not None else
                                          (fill.get("angle") if isinstance(fill, dict) and fill.get("angle") is not None
                                           else theme.style.get("gradient_angle", 0)))
        stops = shape.fill.gradient_stops
        for stop, token in zip(stops, grad):
            stop.color.rgb = rgb(theme.color(token))
            _alpha(stop.color, alpha)
        return
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(theme.color(fill))
    _alpha(shape.fill.fore_color, alpha)


def no_line(shape) -> None:
    shape.line.fill.background()


def outline(shape, theme: Theme, token: str, width_units: float) -> None:
    shape.line.color.rgb = rgb(theme.color(token))
    shape.line.width = Pt(width_units / 2)


def rect(slide_obj, theme: Theme, box: Box, fill, radius: float = 0, alpha: float | None = None,
         line: str | None = None, line_width: float = 3, name: str = "rect", angle: float | None = None,
         top_only: bool = False):
    """Прямоугольник; radius — скругление в единицах сетки; top_only — скруглены только верхние углы."""
    kind = (MSO_SHAPE.ROUND_2_SAME_RECTANGLE if top_only else MSO_SHAPE.ROUNDED_RECTANGLE) if radius \
        else MSO_SHAPE.RECTANGLE
    shape = slide_obj.shapes.add_shape(kind, u(box.x), u(box.y), u(box.w), u(box.h))
    shape.name = name
    apply_fill(shape, theme, fill, alpha, angle)
    if line:
        outline(shape, theme, line, line_width)
    else:
        no_line(shape)
    if radius:
        shape.adjustments[0] = min(0.5, radius / max(1.0, min(box.w, box.h)))
        if top_only:
            shape.adjustments[1] = 0.0
    no_style(shape)
    return shape


def preset(slide_obj, theme: Theme, kind, box: Box, fill, name: str, alpha: float | None = None,
           adjustments: list[float] | None = None):
    shape = slide_obj.shapes.add_shape(kind, u(box.x), u(box.y), u(box.w), u(box.h))
    shape.name = name
    apply_fill(shape, theme, fill, alpha)
    no_line(shape)
    for i, value in enumerate(adjustments or []):
        shape.adjustments[i] = value
    no_style(shape)
    return shape


# ── Многоугольники (custGeom) ───────────────────────────────────────────────

def clip(points: list[tuple[float, float]], region: Box) -> list[tuple[float, float]]:
    """Отсечение многоугольника прямоугольником (Сазерленд — Ходжмен)."""
    x0, y0, x1, y1 = region.x, region.y, region.x + region.w, region.y + region.h
    edges = [
        (lambda p: p[0] >= x0, lambda a, b: _cut_x(a, b, x0)),
        (lambda p: p[0] <= x1, lambda a, b: _cut_x(a, b, x1)),
        (lambda p: p[1] >= y0, lambda a, b: _cut_y(a, b, y0)),
        (lambda p: p[1] <= y1, lambda a, b: _cut_y(a, b, y1)),
    ]
    out = list(points)
    for inside, cut in edges:
        if not out:
            break
        src, out = out, []
        prev = src[-1]
        for cur in src:
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
            prev = cur
    return out


def _cut_x(a, b, x):
    t = (x - a[0]) / ((b[0] - a[0]) or 1e-9)
    return x, a[1] + t * (b[1] - a[1])


def _cut_y(a, b, y):
    t = (y - a[1]) / ((b[1] - a[1]) or 1e-9)
    return a[0] + t * (b[0] - a[0]), y


def polygon(slide_obj, theme: Theme, points: list[tuple[float, float]], fill, name: str,
            alpha: float | None = None, region: Box | None = None):
    """Замкнутый многоугольник (точки — единицы сетки слайда). None — если после обрезки пусто."""
    if region is not None:
        points = clip(points, region)
    if len(points) < 3:
        return None
    builder = slide_obj.shapes.build_freeform(int(round(points[0][0] * EMU_PER_UNIT)),
                                              int(round(points[0][1] * EMU_PER_UNIT)), scale=1.0)
    builder.add_line_segments([(int(round(x * EMU_PER_UNIT)), int(round(y * EMU_PER_UNIT))) for x, y in points[1:]],
                              close=True)
    shape = builder.convert_to_shape()
    shape.name = name
    apply_fill(shape, theme, fill, alpha)
    no_line(shape)
    no_style(shape)
    return shape


def arc_points(cx: float, cy: float, radius: float, thickness: float, start: float, end: float):
    """Кольцевой сектор от start до end градусов (0 — вправо, по часовой — вниз)."""
    sweep = (end - start) % 360 or 360
    steps = max(8, int(CIRCLE_STEPS * sweep / 360))
    inner = max(0.0, radius - thickness)
    outer_pts, inner_pts = [], []
    for i in range(steps + 1):
        a = math.radians(start + sweep * i / steps)
        outer_pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        if inner > 0:
            inner_pts.append((cx + inner * math.cos(a), cy + inner * math.sin(a)))
    if inner <= 0:
        return outer_pts + [(cx, cy)] if sweep < 360 else outer_pts
    return outer_pts + inner_pts[::-1]


def circle_points(cx: float, cy: float, radius: float):
    return [(cx + radius * math.cos(2 * math.pi * i / CIRCLE_STEPS),
             cy + radius * math.sin(2 * math.pi * i / CIRCLE_STEPS)) for i in range(CIRCLE_STEPS)]


def wave_points(box: Box, amplitude: float, period: float, phase: float):
    """Полоса с волнистым верхним краем внутри box."""
    steps = 64
    top = []
    for i in range(steps + 1):
        x = box.x + box.w * i / steps
        top.append((x, box.y + amplitude + amplitude * math.sin(2 * math.pi * (x - box.x) / period + phase)))
    return top + [(box.x + box.w, box.y + box.h), (box.x, box.y + box.h)]


def draw_decor(slide_obj, theme: Theme, items: list[dict], region: Box, prefix: str = "decor") -> int:
    """Декоративные фигуры темы в области region (координаты — относительно области,
    в выражениях доступны w и h области). → сколько фигур нарисовано."""
    w, h = region.w, region.h

    def pt(xy):
        return region.x + _num(xy[0], w, h), region.y + _num(xy[1], w, h)

    count = 0
    for i, item in enumerate(items):
        name = f"{prefix}:{item['shape']}:{i}"
        fill, alpha = item.get("fill", "accent"), item.get("alpha")
        shape = item["shape"]
        if shape == "polygon":
            pts = [pt(p) for p in item["points"]]
        elif shape in ("circle", "arc"):
            cx, cy = pt(item["center"])
            radius = _num(item["radius"], w, h)
            pts = (circle_points(cx, cy, radius) if shape == "circle" else
                   arc_points(cx, cy, radius, _num(item.get("thickness", 8), w, h),
                              float(item.get("start", 0)), float(item.get("end", 360))))
        elif shape == "wave":
            x, y = pt(item["box"][:2])
            box = Box(x, y, _num(item["box"][2], w, h), _num(item["box"][3], w, h))
            pts = wave_points(box, float(item.get("amplitude", 20)), float(item.get("period", 800)),
                              float(item.get("phase", 0)))
        elif shape == "dots":
            x, y = pt(item["box"][:2])
            bw, bh = _num(item["box"][2], w, h), _num(item["box"][3], w, h)
            rows, cols, size = int(item.get("rows", 3)), int(item.get("cols", 3)), float(item.get("size", 10))
            for r in range(rows):
                for c in range(cols):
                    dx = x + (bw - size) * (c / max(1, cols - 1))
                    dy = y + (bh - size) * (r / max(1, rows - 1))
                    if polygon(slide_obj, theme, circle_points(dx + size / 2, dy + size / 2, size / 2), fill,
                               f"{name}:{r}.{c}", alpha, region):
                        count += 1
            continue
        else:
            raise ValueError(f"unknown decor shape {shape!r}")
        if polygon(slide_obj, theme, pts, fill, name, alpha, region) is not None:
            count += 1
    return count


# ── Фигура номера (badge) ───────────────────────────────────────────────────

def flag_points(box: Box):
    """Флажок: прямоугольник с вырезом-«ласточкиным хвостом» справа."""
    notch = box.w * 0.22
    return [(box.x, box.y), (box.x + box.w, box.y), (box.x + box.w - notch, box.y + box.h / 2),
            (box.x + box.w, box.y + box.h), (box.x, box.y + box.h)]


def pennant_points(box: Box):
    """Вымпел: вертикальный флажок с вырезом снизу (как закладка)."""
    w = box.w * 0.78
    x = box.x + (box.w - w) / 2
    notch = box.h * 0.2
    return [(x, box.y), (x + w, box.y), (x + w, box.y + box.h), (x + w / 2, box.y + box.h - notch),
            (x, box.y + box.h)]


def badge_shape(slide_obj, theme: Theme, box: Box, fill, name: str, kind: str | None = None):
    """Фигура под номер по стилю темы: circle / square / flag / pennant / chevron. → (фигура, текстовая область)."""
    kind = kind or theme.style.get("badge", "circle")
    if kind == "pennant":
        shape = polygon(slide_obj, theme, pennant_points(box), fill, name)
        return shape, Box(box.x, box.y, box.w, box.h * 0.8)
    if kind == "flag":
        shape = polygon(slide_obj, theme, flag_points(box), fill, name)
        return shape, Box(box.x, box.y, box.w * 0.8, box.h)
    if kind == "chevron":
        shape = preset(slide_obj, theme, MSO_SHAPE.PENTAGON, box, fill, name, adjustments=[0.3])
        return shape, Box(box.x, box.y, box.w * 0.85, box.h)
    if kind == "square":
        shape = rect(slide_obj, theme, box, fill, radius=min(box.w, box.h) * 0.22, name=name)
        return shape, box
    shape = preset(slide_obj, theme, MSO_SHAPE.OVAL, box, fill, name)
    return shape, box
