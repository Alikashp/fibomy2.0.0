"""Связь слотов LayoutSpec с содержанием слайда (поле bind элемента).

Одна функция для fitter'а и рендерера: оба видят один и тот же текст слота.

    meta.title            тема пользователя (титул)
    title                 заголовок слайда (у statement — само утверждение)
    footnote              сноска, которую ставит код
    data                  снимок данных диаграммы (Slide.data)
    content.<путь>        поле содержания по схеме kind; путь через точку,
                          индексы списков — числами: content.left.points.0
    const:<текст>         постоянный текст макета (кавычка)
    <поле>                внутри повторяющегося элемента — поле пункта (heading, text, icon)
    $index                номер элемента с 1

format элемента применяется к тексту: «{:02d}» для номера, «•  {}» для пункта.
"""

from core.models.deck import DeckSpec, Slide
from core.models.layout import Element


def _get(obj, path: list[str]):
    for key in path:
        if obj is None:
            return None
        if isinstance(obj, list):
            i = int(key) if key.isdigit() else -1
            obj = obj[i] if 0 <= i < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _raw(element: Element, slide: Slide, spec: DeckSpec, item: dict | None, index: int | None):
    bind = element.bind
    if not bind:
        return None
    if bind.startswith("const:"):
        return bind[len("const:"):]
    if bind == "$index":
        return (index or 0) + 1
    if item is not None and "." not in bind:
        return item.get(bind)
    if bind == "meta.title":
        return spec.meta.title
    if bind == "title":
        return slide.title
    if bind == "footnote":
        return slide.footnote
    if bind == "data":
        return slide.data
    if bind.startswith("content."):
        return _get(slide.content, bind.split(".")[1:])
    raise ValueError(f"unknown bind {bind!r} in {slide.variant}")


def read(element: Element, slide: Slide, spec: DeckSpec, item: dict | None = None, index: int | None = None):
    value = _raw(element, slide, spec, item, index)
    if value is None or value == "" or not element.fmt or element.type == "chart":
        return value
    return element.fmt.format(value)


def text_of(element: Element, slide: Slide, spec: DeckSpec, item: dict | None = None, index: int | None = None):
    """Текст слота без format — то, что можно сократить и записать обратно."""
    value = _raw(element, slide, spec, item, index)
    return value if isinstance(value, str) else (None if value is None else str(value))


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
        path = bind.split(".")[1:]
        parent = _get(slide.content, path[:-1]) if len(path) > 1 else slide.content
        key = path[-1]
        if isinstance(parent, list) and key.isdigit():
            parent[int(key)] = value
        elif isinstance(parent, dict):
            parent[key] = value
        else:
            raise ValueError(f"cannot write {bind!r}")
    else:
        raise ValueError(f"bind {bind!r} is read-only")


def items_of(layout_items_bind: str | None, slide: Slide) -> list[dict]:
    if not layout_items_bind:
        return []
    return list(_get(slide.content, layout_items_bind.split(".")[1:]) or [])
