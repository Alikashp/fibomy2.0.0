"""FIT v0 (сессия 1): шаги кегля и обрезка по предложению (05_LAYOUTS.md, 2; ТЗ 3.7).

Для каждого текстового слота: базовый стиль → шаги вниз до min_style. Не влезло
и на минимальном — обрезка по границе предложения (или по слову с «…») и запись
в fit.truncated и в degradations. Тема пользователя на титуле (never_truncate)
не обрезается никогда (G-05).

У повторяющихся элементов (карточки, выводы) стиль слота общий для всех
элементов — по самому длинному тексту, чтобы карточки не различались кеглем.

Сессия 5 (D-054): сокращение через LLM (1 попытка) и смена варианта перед обрезкой.
"""

import logging
from dataclasses import dataclass

from core.fitting.metrics import sentences, text_width, wrap
from core.models import bind as B
from core.models.deck import DeckSpec, Slide
from core.models.layout import Element, LayoutSpec, load_layouts, max_lines_at, style_chain
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


def fit_deck(spec: DeckSpec, theme: Theme) -> None:
    layouts = load_layouts()
    for slide in spec.slides:
        fit_slide(slide, spec, layouts[slide.variant], theme)


def width_units(text: str, style: str, theme: Theme) -> float:
    """Для тестов: ширина текста стилем style в единицах сетки."""
    return text_width(text, theme.pt(style), style in BOLD_STYLES)
