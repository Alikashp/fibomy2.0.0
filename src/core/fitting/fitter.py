"""FIT: шаги кегля и высота по содержимому (05_LAYOUTS.md, 2; ТЗ 3.7). Текст не режется никогда.

Для каждого текстового слота: базовый стиль → шаги вниз до min_style. Не влезло и на
минимальном — слот попадает в список Overflow, текст остаётся как есть. Что делать дальше,
решает core.fitting.resolve по порядку D-071: более вместительный вариант → сокращение через
LLM («сократи до N знаков») → разбить слайд на два. Многоточия и обрезки нет ни в одном слоте,
включая заголовки и подзаголовок титула (сессия 5, замечание владельца; было — обрезка по
предложению с «…»).

Не влезает: строк больше, чем вмещает слот, или слово шире слота («Непротиворечивост / ь» —
перенос по знакам не считается вписыванием).

У повторяющихся элементов (карточки, выводы) стиль слота общий для всех
элементов — по самому длинному тексту, чтобы карточки не различались кеглем.

Значение с единицей (metrics: suffix в YAML) — одна строка: «92–98 %», «4,5 млн ₽».

Высота по содержимому (05_LAYOUTS.md, 8, требование 3): после подбора кегля карточки и панели
ужимаются до «текст + поля», блок встаёт по центру зоны контента (compact в YAML); если блок
занимает меньше нижней границы заполненности зоны (FILL_MIN, 40%), ячейки растягиваются по
высоте до неё (core.fitting.fill). Результат — Fit.items_area и Fit.boxes, рендерер рисует по ним.
"""

import logging
from dataclasses import dataclass

from core.fitting.metrics import NBSP, overlong_words, text_width, wrap
from core.models import bind as B
from core.models.deck import DeckSpec, Slide
from core.models.layout import (LINE_HEIGHT, UNITS_PER_PT, Element, LayoutSpec, _element, load_layouts,
                                max_lines_at, style_chain)
from core.models.theme import BOLD_STYLES, STYLE_STEPS, Theme

logger = logging.getLogger(__name__)


@dataclass
class TextFit:
    style: str
    lines: list[str]
    fits: bool
    overlong: list[str] | None = None
    suffix_style: str | None = None


@dataclass
class Overflow:
    """Слот, который не влез на минимальном стиле. key — ключ стиля слота (title, item.text),
    item — номер повторяющегося элемента или None."""
    slide_id: str
    key: str
    element: str
    text: str
    item: int | None = None
    lines: int = 0
    max_lines: int = 0
    overlong: list[str] | None = None
    never_shorten: bool = False

    @property
    def reason(self) -> str:
        return "word" if self.overlong else "lines"


def _display(element: Element, text: str) -> str:
    """Текст так, как его нарисует рендерер (format элемента: «•  {}»)."""
    if element.fmt and element.bind and not element.bind.startswith(("$", "const:")):
        return element.fmt.format(text)
    return text


SUFFIX_MIN = "small"


def suffix_styles(element: Element, style: str) -> list[str]:
    """Стили единицы при значении стилем style: от suffix_style (не крупнее значения) до SUFFIX_MIN."""
    base = element.suffix_style or style
    if STYLE_STEPS.index(base) < STYLE_STEPS.index(style):
        base = style
    return list(STYLE_STEPS[STYLE_STEPS.index(base):STYLE_STEPS.index(SUFFIX_MIN) + 1]) or [base]


def measure(element: Element, text: str, style: str, theme: Theme, suffix: str | None = None) -> TextFit:
    text = _display(element, text)
    pt = theme.pt(style)
    bold = style in BOLD_STYLES
    if suffix:
        # Значение и единица — одна строка без переноса (рендерер — два фрагмента одного абзаца);
        # единица сначала мельчает сама, потом — вместе со значением
        value_w = text_width(text, pt, bold)
        for s_style in suffix_styles(element, style):
            width = value_w + text_width(NBSP + suffix, theme.pt(s_style), s_style in BOLD_STYLES)
            if width <= element.box.w * 0.99:
                return TextFit(style, [f"{text}{NBSP}{suffix}"], True, suffix_style=s_style)
        return TextFit(style, [f"{text}{NBSP}{suffix}"], False, [f"{text} {suffix}"], suffix_style=s_style)
    lines = wrap(text, element.box.w, pt, bold)
    overlong = overlong_words(text, element.box.w, pt, bold)
    fits = len(lines) <= max_lines_at(element, style, theme.typescale) and not overlong
    return TextFit(style, lines, fits, overlong or None)


def fit_text(element: Element, text: str, theme: Theme, suffix: str | None = None) -> TextFit:
    """Самый крупный стиль из цепочки, на котором текст влезает; иначе — минимальный."""
    result = None
    for style in style_chain(element.style, element.min_style):
        result = measure(element, text, style, theme, suffix)
        if result.fits:
            return result
    return result


def _fit_group(texts: list[tuple[Element, str, str | None]], theme: Theme) -> str:
    """Общий стиль для группы одинаковых слотов: самый крупный, на котором влезают все."""
    element = texts[0][0]
    chain = style_chain(element.style, element.min_style)
    for style in chain:
        if all(measure(e, t, style, theme, s).fits for e, t, s in texts):
            return style
    return chain[-1]


def _suffix_of(element: Element, slide: Slide, spec: DeckSpec, item: dict | None, index: int | None) -> str | None:
    if not element.suffix:
        return None
    probe = Element(name=element.name, type="text", box=element.box, bind=element.suffix)
    value = B.text_of(probe, slide, spec, item=item, index=index)
    return value.strip() if value and value.strip() else None


def fit_slide(slide: Slide, spec: DeckSpec, layout: LayoutSpec, theme: Theme) -> list[Overflow]:
    """Стили слотов слайда → slide.fit.styles; слоты, которые не влезли, — список Overflow."""
    slide.fit.styles = {}
    overflows: list[Overflow] = []
    for element in layout.elements():
        if not element.is_text or element.bind is None or element.bind.startswith("const:"):
            if element.is_text:
                slide.fit.styles[element.name] = element.style
            continue
        text = B.text_of(element, slide, spec)
        if not text:
            continue
        suffix = _suffix_of(element, slide, spec, None, None)
        style = _fit_group([(element, text, suffix)], theme)
        slide.fit.styles[element.name] = style
        _check(element, element.name, text, style, slide, theme, overflows, suffix=suffix)

    items = B.items_of(layout.items_bind, slide)
    if not items or len(items) not in layout.arrangements:
        return overflows
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
                group.append((element, str(text), _suffix_of(element, slide, spec, item, i), i))
        if not group:
            continue
        style = _fit_group([(e, t, s) for e, t, s, _ in group], theme)
        key = f"item.{template.name}"
        slide.fit.styles[key] = style
        for element, text, suffix, i in group:
            _check(element, key, text, style, slide, theme, overflows, item=i, suffix=suffix)
    return overflows


def _check(element: Element, key: str, text: str, style: str, slide: Slide, theme: Theme,
           overflows: list[Overflow], item: int | None = None, suffix: str | None = None) -> None:
    result = measure(element, text, style, theme, suffix)
    if result.suffix_style:
        # Единица — самым мелким стилем, который понадобился в группе (карточки не различаются)
        skey = f"{key}:suffix"
        prev = slide.fit.styles.get(skey)
        if prev is None or STYLE_STEPS.index(result.suffix_style) > STYLE_STEPS.index(prev):
            slide.fit.styles[skey] = result.suffix_style
    if result.fits:
        return
    overflows.append(Overflow(
        slide_id=slide.id, key=key, element=element.name, text=text, item=item, lines=len(result.lines),
        max_lines=max_lines_at(element, style, theme.typescale), overlong=result.overlong,
        never_shorten=element.never_truncate or (element.bind or "").startswith("$")))


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
    need = items_need(slide, spec, layout, theme)
    cell = layout.item_cells(n)[0]
    if need >= cell.h - 1:
        return
    ax, ay, aw, ah = (float(v) for v in grid["area"])
    rows, gap = int(grid["rows"]), float(grid.get("gap", 0))
    height = rows * need + gap * (rows - 1)
    top = ay + (ah - height) / 2 if mode == "center" else ay
    slide.fit.items_area = [ax, round(top, 1), aw, round(height, 1)]


def items_need(slide: Slide, spec: DeckSpec, layout: LayoutSpec, theme: Theme) -> float:
    """Нужная высота ячейки: нижний край самого высокого содержимого + поле pad."""
    items = B.items_of(layout.items_bind, slide)
    n = len(items)
    grid = layout.arrangements[n]["grid"]
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
            if e.suffix:
                height = theme.pt(style) * UNITS_PER_PT * LINE_HEIGHT
                # значение прижато к низу своего бокса (anchor: bottom) — занимает бокс целиком
                bottom = max(bottom, e.box.y + (e.box.h if e.anchor == "bottom" else height))
                continue
            bottom = max(bottom, e.box.y + _text_height(e, text, style, theme))
        need = max(need, bottom + float(grid.get("pad", 32)))
    return need


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


def layout_slide(slide: Slide, spec: DeckSpec, layout: LayoutSpec, theme: Theme) -> list[Overflow]:
    """Кегль, высота по содержимому и заполненность зоны контента одного слайда."""
    from core.fitting.fill import ensure_fill
    overflows = fit_slide(slide, spec, layout, theme)
    compact_items(slide, spec, layout, theme)
    compact_panels(slide, spec, layout, theme)
    ensure_fill(slide, spec, layout, theme)
    return overflows


def fit_deck(spec: DeckSpec, theme: Theme) -> list[Overflow]:
    """Кегль и высота по содержимому всех слайдов (без LLM). → слоты, которые не влезли."""
    layouts = load_layouts()
    overflows = []
    for slide in spec.slides:
        overflows += layout_slide(slide, spec, layouts[slide.variant], theme)
    for o in overflows:
        logger.info("Text does not fit on min style", extra={"slide_id": o.slide_id, "slot": o.key,
                                                             "chars": len(o.text), "reason": o.reason})
    return overflows


def width_units(text: str, style: str, theme: Theme) -> float:
    """Для тестов: ширина текста стилем style в единицах сетки."""
    return text_width(text, theme.pt(style), style in BOLD_STYLES)
