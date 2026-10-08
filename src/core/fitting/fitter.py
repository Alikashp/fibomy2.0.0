"""FIT v0 (сессия 1): шаги кегля и обрезка по предложению (05_LAYOUTS.md, 2; ТЗ 3.7).

Для каждого текстового слота: базовый стиль → шаги вниз до min_style. Не влезло
и на минимальном — обрезка по границе предложения (или по слову с «…») и запись
в fit.truncated и в degradations. Тема пользователя на титуле (never_truncate)
не обрезается никогда (G-05).

У повторяющихся элементов (карточки, выводы) стиль слота общий для всех
элементов — по самому длинному тексту, чтобы карточки не различались кеглем.

Высота по содержимому (05_LAYOUTS.md, 8, требование 3; сессия 4): после подбора кегля
карточки и панели ужимаются до «текст + поля», блок встаёт по центру зоны контента
(compact в YAML). Результат — Fit.items_area и Fit.boxes, рендерер рисует по ним.

Сессия 5 (D-054): сокращение через LLM (1 попытка) и смена варианта перед обрезкой.
"""

import logging
from dataclasses import dataclass

from core.fitting.metrics import sentences, text_width, wrap
from core.models import bind as B
from core.models.deck import DeckSpec, Slide
from core.models.layout import (LINE_HEIGHT, UNITS_PER_PT, Element, LayoutSpec, _element, load_layouts,
                                max_lines_at, style_chain)
from core.models.theme import BOLD_STYLES, Theme

logger = logging.getLogger(__name__)

ELLIPSIS = "…"


@dataclass
class TextFit:
    style: str
    lines: list[str]
    fits: bool


def _display(element: Element, text: str) -> str:
    """Текст так, как его нарисует рендерер (format элемента: «•  {}»)."""
    if element.fmt and element.bind and not element.bind.startswith(("$", "const:")):
        return element.fmt.format(text)
    return text


def measure(element: Element, text: str, style: str, theme: Theme) -> TextFit:
    text = _display(element, text)
    pt = theme.pt(style)
    lines = wrap(text, element.box.w, pt, style in BOLD_STYLES)
    return TextFit(style, lines, len(lines) <= max_lines_at(element, style, theme.typescale))


def fit_text(element: Element, text: str, theme: Theme) -> TextFit:
    """Самый крупный стиль из цепочки, на котором текст влезает; иначе — минимальный."""
    result = None
    for style in style_chain(element.style, element.min_style):
        result = measure(element, text, style, theme)
        if result.fits:
            return result
    return result


def truncate(element: Element, text: str, style: str, theme: Theme) -> str:
    """Самое длинное начало текста, которое влезает стилем style: целые предложения,
    иначе слова с многоточием."""
    kept = ""
    for sentence in sentences(text):
        candidate = f"{kept} {sentence}".strip()
        if not measure(element, candidate, style, theme).fits:
            break
        kept = candidate
    if kept:
        return kept
    words = text.split()
    kept = ""
    for word in words:
        candidate = f"{kept} {word}".strip()
        if not measure(element, candidate.rstrip(" ,;:—-") + ELLIPSIS, style, theme).fits:
            break
        kept = candidate
    return (kept.rstrip(" ,;:—-") + ELLIPSIS) if kept else ""


def _fit_group(texts: list[tuple[Element, str]], theme: Theme) -> str:
    """Общий стиль для группы одинаковых слотов: самый крупный, на котором влезают все."""
    element = texts[0][0]
    chain = style_chain(element.style, element.min_style)
    for style in chain:
        if all(measure(e, t, style, theme).fits for e, t in texts):
            return style
    return chain[-1]


def fit_slide(slide: Slide, spec: DeckSpec, layout: LayoutSpec, theme: Theme) -> None:
    slide.fit.styles = {}
    for element in layout.elements():
        if not element.is_text or element.bind is None or element.bind.startswith("const:"):
            if element.is_text:
                slide.fit.styles[element.name] = element.style
            continue
        text = B.text_of(element, slide, spec)
        if not text:
            continue
        style = _fit_group([(element, text)], theme)
        slide.fit.styles[element.name] = style
        _maybe_truncate(element, text, style, slide, spec, theme)

    items = B.items_of(layout.items_bind, slide)
    if not items or len(items) not in layout.arrangements:
        return
    per_item = layout.item_elements(len(items))
    for pos, template in enumerate(per_item[0]):
        if not template.is_text:
            continue
        group = []
        for i, item in enumerate(items):
            element = per_item[i][pos]
            if (element.bind or "").startswith("$"):
                text = B.read(element, slide, spec, item=item, index=i)
            else:
                text = B.text_of(element, slide, spec, item=item, index=i)
            if text:
                group.append((element, str(text), item))
        if not group:
            continue
        style = _fit_group([(e, t) for e, t, _ in group], theme)
        slide.fit.styles[f"item.{template.name}"] = style
        for element, text, item in group:
            _maybe_truncate(element, text, style, slide, spec, theme, item=item)


def _maybe_truncate(element: Element, text: str, style: str, slide: Slide, spec: DeckSpec,
                    theme: Theme, item: dict | None = None) -> None:
    if measure(element, text, style, theme).fits:
        return
    if element.never_truncate or (element.bind or "").startswith("$"):
        logger.warning("Text does not fit and must not be truncated",
                       extra={"slide_id": slide.id, "slot": element.name, "chars": len(text)})
        spec.degrade("fit", f"overflow:{element.name}", slide.id)
        return
    cut = truncate(element, text, style, theme)
    B.write(element, slide, cut, item=item)
    slide.fit.truncated = True
    slide.fit.shortened.append(element.name)
    logger.warning("Text truncated by fitter", extra={
        "slide_id": slide.id, "slot": element.name, "chars_before": len(text), "chars_after": len(cut)})
    spec.degrade("fit", f"truncated:{element.name}", slide.id)


def _text_height(element: Element, text: str, style: str, theme: Theme) -> float:
    return len(measure(element, text, style, theme).lines) * theme.pt(style) * UNITS_PER_PT * LINE_HEIGHT


def compact_items(slide: Slide, spec: DeckSpec, layout: LayoutSpec, theme: Theme) -> None:
    """Высота ячеек повторяющихся элементов — по самому высокому содержимому + поле pad;
    блок ячеек — по центру области (compact: center) или у её верха (top)."""
    slide.fit.items_area = None
    items = B.items_of(layout.items_bind, slide)
    if not items or len(items) not in layout.arrangements:
        return
    n = len(items)
    grid = layout.arrangements[n]["grid"]
    mode = grid.get("compact")
    if not mode:
        return
    cell = layout.item_cells(n)[0]
    raw = layout.arrangements[n]["elements"]
    need = 0.0
    for i, item in enumerate(items):
        bottom = 0.0
        for r in raw:
            e = _element(r, cell.w, cell.h)
            if e.type in ("card", "rect", "line"):
                continue                                       # подложки и соединители растягиваются
            if _element(r, cell.w, cell.h + 100).box.y != e.box.y:
                continue                                       # привязан к низу ячейки
            if e.type in ("icon", "badge", "image"):
                bottom = max(bottom, e.box.y + e.box.h)
                continue
            text = B.text_of(e, slide, spec, item=item, index=i)
            if not text:
                continue
            style = slide.fit.styles.get(f"item.{e.name}") or e.style
            bottom = max(bottom, e.box.y + _text_height(e, text, style, theme))
        need = max(need, bottom + float(grid.get("pad", 32)))
    if need >= cell.h - 1:
        return
    ax, ay, aw, ah = (float(v) for v in grid["area"])
    rows, gap = int(grid["rows"]), float(grid.get("gap", 0))
    height = rows * need + gap * (rows - 1)
    top = ay + (ah - height) / 2 if mode == "center" else ay
    slide.fit.items_area = [ax, round(top, 1), aw, round(height, 1)]


def compact_panels(slide: Slide, spec: DeckSpec, layout: LayoutSpec, theme: Theme) -> None:
    """Панели (сравнение): пункты идут друг за другом, панели — по самому длинному столбцу,
    всё содержимое области — по центру (compact в YAML макета)."""
    slide.fit.boxes = {}
    c = layout.compact
    if not c:
        return
    els = {e.name: e for e in layout.elements()}
    ax, ay, aw, ah = (float(v) for v in c["area"])
    gap, pad = float(c.get("gap", 20)), float(c.get("pad", 40))
    boxes: dict[str, list[float]] = {}
    bottom = 0.0
    for stack in c.get("stacks", []):
        y = els[stack[0]].box.y
        for name in stack:
            e = els[name]
            text = B.text_of(e, slide, spec)
            if not text:
                continue
            style = slide.fit.styles.get(name) or e.style
            h = _text_height(e, text, style, theme)
            boxes[name] = [e.box.x, y, e.box.w, h]
            y += h + gap
            bottom = max(bottom, y - gap)
    if not bottom:
        return
    content_bottom = bottom + pad
    for name in c.get("panels", []):
        e = els[name]
        boxes[name] = [e.box.x, e.box.y, e.box.w, min(e.box.h, content_bottom - e.box.y)]
    used = min(ah, content_bottom - ay)
    dy = (ah - used) / 2 if c.get("align", "center") == "center" else 0.0
    for name, e in els.items():
        b = boxes.get(name, [e.box.x, e.box.y, e.box.w, e.box.h])
        if e.box.y >= ay and e.box.y + e.box.h <= ay + ah + 0.01:
            boxes[name] = [b[0], round(b[1] + dy, 1), b[2], round(b[3], 1)]
    slide.fit.boxes = boxes


def fit_deck(spec: DeckSpec, theme: Theme) -> None:
    layouts = load_layouts()
    for slide in spec.slides:
        layout = layouts[slide.variant]
        fit_slide(slide, spec, layout, theme)
        compact_items(slide, spec, layout, theme)
        compact_panels(slide, spec, layout, theme)


def width_units(text: str, style: str, theme: Theme) -> float:
    """Для тестов: ширина текста стилем style в единицах сетки."""
    return text_width(text, theme.pt(style), style in BOLD_STYLES)
