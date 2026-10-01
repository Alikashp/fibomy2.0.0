"""Связь слотов LayoutSpec с содержанием слайда (поле bind элемента).

Одна функция для fitter'а и рендерера: оба видят один и тот же текст слота.

    meta.title       тема пользователя (титул)
    title            заголовок слайда (у statement — само утверждение)
    footnote         сноска, которую ставит код
    content.<поле>   поле содержания по схеме kind
    const:<текст>    постоянный текст макета (кавычка)
    <поле>           внутри повторяющегося элемента — поле пункта (heading, text, icon)
    $index           номер элемента с 1 (формат — format элемента)
"""

from core.models.deck import DeckSpec, Slide
from core.models.layout import Element


def read(element: Element, slide: Slide, spec: DeckSpec, item: dict | None = None, index: int | None = None):
    bind = element.bind
    if not bind:
        return None
    if bind.startswith("const:"):
        return bind[len("const:"):]
    if bind == "$index":
        return (element.fmt or "{}").format((index or 0) + 1)
    if item is not None and "." not in bind:
        return item.get(bind)
    if bind == "meta.title":
        return spec.meta.title
    if bind == "title":
        return slide.title
    if bind == "footnote":
        return slide.footnote
    if bind.startswith("content."):
        return slide.content.get(bind[len("content."):])
    raise ValueError(f"unknown bind {bind!r} in {slide.variant}")


def write(element: Element, slide: Slide, value: str, item: dict | None = None) -> None:
    """Записывает сокращённый fitter'ом текст обратно в содержание слайда."""
    bind = element.bind or ""
    if item is not None and "." not in bind and not bind.startswith("$"):
        item[bind] = value
    elif bind == "title":
        slide.title = value
    elif bind == "footnote":
        slide.footnote = value
    elif bind.startswith("content."):
        slide.content[bind[len("content."):]] = value
    else:
        raise ValueError(f"bind {bind!r} is read-only")


def items_of(layout_items_bind: str | None, slide: Slide) -> list[dict]:
    if not layout_items_bind:
        return []
    return list(slide.content.get(layout_items_bind.split(".", 1)[1]) or [])
