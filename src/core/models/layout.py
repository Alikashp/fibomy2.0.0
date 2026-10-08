"""LayoutSpec — вариант макета из layouts/<kind>/<variant>.yaml (04_CONTRACTS.md, 6.1).

Формат (сетка 1920×1080, 1 pt = 2 единицы):

    id, kind, family, version, fallback
    applies_when: {items: {min, max}, image: forbidden|required|optional, title_max_chars}
    background: bg | brand    # brand — весь слайд в градиенте brand → brand_2 (титул, финал, пауза)
    header: true | false      # оформление заголовка по теме (style.header) — у содержательных слайдов
    color_heavy: true         # «цветной» слайд: не чаще одного на 3 слайда (selection)
    decor: [{set: cover|band|content|header, region: [x, y, w, h]}]   # по умолчанию — по background
    compact: {area: [x, y, w, h], align: center|top}   # высота панелей по содержимому (fitter)
    elements:            # элементы слайда в порядке отрисовки (z-порядок)
      - {name, type, box: [x, y, w, h], ...}
    items:               # повторяющиеся элементы (пункты, выводы), если есть
      bind: content.items
      arrangements:
        <n>: {grid: {area, cols, rows, gap, center_last_row, compact: center|top}, elements: [...]}

Внутри arrangements координаты элемента — относительно ячейки; в выражениях
доступны w и h ячейки («w-64»). Типы элементов: text, rect, line, card (карточка по стилю темы:
заливка или обводка, цветная полоса), icon (plate: true — в фигуре-подложке темы), badge (номер
в фигуре темы: круг, квадрат, флажок, шеврон), chevron (стрелка процесса), image (картинка слайда,
скругление по теме), chart (нативная диаграмма: chart = column | line | donut, данные — Slide.data).
fill: токен темы; $chart — цвет серии chart_N по номеру элемента; $brand — градиент brand → brand_2.
У text: style, min_style, max_lines, color, bind, align (left|center|right),
anchor (top|middle|bottom), optional, never_truncate. when: <bind> — элемент рисуется, только
если по bind есть значение (карточка пояснения). «@имя» в fill и color — значение из style темы.

Вместимость (max_chars) в YAML не хранится — её считает код по геометрии и
формуле 05_LAYOUTS.md, 2 (capacity()). Так таблицы документа, промпт и рендер
не расходятся.
"""

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from core.models.theme import BOLD_STYLES, STYLE_STEPS
from core.paths import LAYOUTS_DIR

GRID_W, GRID_H = 1920, 1080
UNITS_PER_PT = 2
LINE_HEIGHT = 1.2
K_REGULAR, K_BOLD = 0.55, 0.59          # средняя ширина знака в долях кегля (D-035)
WRAP_LOSS = 0.9                          # потери на переносе по словам

DEFAULT_TYPESCALE = {"hero": 96, "display": 60, "h1": 36, "h2": 26, "h3": 20, "body": 18, "small": 14, "min": 12}

_EXPR_RE = re.compile(r"^[\d\s+\-*/().wh]+$")


def _num(value: Any, w: float = 0, h: float = 0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    expr = str(value)
    if not _EXPR_RE.match(expr):
        raise ValueError(f"bad layout expression: {expr!r}")
    return float(eval(expr, {"__builtins__": {}}, {"w": w, "h": h}))  # noqa: S307 — только числа, w, h


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    def shift(self, dx: float, dy: float) -> "Box":
        return Box(self.x + dx, self.y + dy, self.w, self.h)


@dataclass(frozen=True)
class Element:
    name: str
    type: str
    box: Box
    style: str | None = None
    min_style: str | None = None
    max_lines: int = 1
    color: str = "text"
    fill: str | None = None
    bind: str | None = None
    align: str = "left"
    anchor: str = "top"
    optional: bool = False
    never_truncate: bool = False
    fmt: str | None = None
    radius: Any = False
    chart: str | None = None
    alpha: float | None = None
    line: str | None = None
    plate: bool = False
    tone: str | None = None
    when: str | None = None        # рисовать, только если по этому bind есть значение
    skip_last: bool = False        # у последнего из повторяющихся элементов не рисовать (соединитель)

    @property
    def is_text(self) -> bool:
        return self.type in ("text", "badge")


def _element(raw: dict, w: float = 0, h: float = 0, dx: float = 0, dy: float = 0) -> Element:
    x, y, bw, bh = (_num(v, w, h) for v in raw["box"])
    return Element(
        name=raw["name"], type=raw.get("type", "text"), box=Box(x + dx, y + dy, bw, bh),
        style=raw.get("style"), min_style=raw.get("min_style", raw.get("style")),
        max_lines=int(raw.get("max_lines", 1)), color=raw.get("color", "text"), fill=raw.get("fill"),
        bind=raw.get("bind"), align=raw.get("align", "left"), anchor=raw.get("anchor", "top"),
        optional=bool(raw.get("optional", False)), never_truncate=bool(raw.get("never_truncate", False)),
        fmt=raw.get("format"), radius=raw.get("radius", False), chart=raw.get("chart"),
        alpha=raw.get("alpha"), line=raw.get("line"), plate=bool(raw.get("plate", False)), tone=raw.get("tone"),
        when=raw.get("when"), skip_last=bool(raw.get("skip_last", False)),
    )


@dataclass
class LayoutSpec:
    id: str
    kind: str
    family: str
    version: int
    fallback: bool
    applies_when: dict
    raw_elements: list[dict]
    items_bind: str | None = None
    arrangements: dict[int, dict] = field(default_factory=dict)
    background: str = "bg"
    header: bool = False
    color_heavy: bool = False
    decor: list[dict] | None = None
    compact: dict | None = None

    @property
    def variant(self) -> str:
        return self.id.split(".", 1)[1]

    @property
    def item_range(self) -> tuple[int, int] | None:
        if not self.arrangements:
            return None
        return min(self.arrangements), max(self.arrangements)

    def elements(self) -> list[Element]:
        return [_element(e) for e in self.raw_elements]

    @property
    def image_slot(self) -> bool:
        return any(e.get("type") == "image" for e in self.raw_elements)

    def item_cells(self, n: int, area: list[float] | None = None) -> list[Box]:
        """Ячейки раскладки для n элементов. area — область сетки после подгонки высоты (Fit.items_area)."""
        grid = self.arrangements[n]["grid"]
        ax, ay, aw, ah = (float(v) for v in (area or grid["area"]))
        cols, rows, gap = int(grid["cols"]), int(grid["rows"]), float(grid.get("gap", 0))
        cw = (aw - gap * (cols - 1)) / cols
        ch = (ah - gap * (rows - 1)) / rows
        cells = []
        for i in range(n):
            r, c = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            offset = (cols - in_row) * (cw + gap) / 2 if grid.get("center_last_row") and in_row < cols else 0
            cells.append(Box(ax + offset + c * (cw + gap), ay + r * (ch + gap), cw, ch))
        return cells

    def item_elements(self, n: int, area: list[float] | None = None) -> list[list[Element]]:
        """Элементы каждого из n повторяющихся элементов в абсолютных координатах."""
        raw = self.arrangements[n]["elements"]
        return [[_element(e, cell.w, cell.h, cell.x, cell.y) for e in raw] for cell in self.item_cells(n, area)]

    def text_slots(self, n: int | None = None) -> dict[str, Element]:
        """Текстовые слоты: общие и (для n) первого элемента — для вместимости."""
        slots = {e.name: e for e in self.elements() if e.is_text}
        if n is not None and n in self.arrangements:
            for e in self.item_elements(n)[0]:
                if e.is_text:
                    slots[f"item.{e.name}"] = e
        return slots


def capacity(element: Element, style: str | None = None, typescale: dict | None = None) -> int:
    """Вместимость слота в знаках по формуле 05_LAYOUTS.md, 2."""
    style = style or element.style
    pt = (typescale or DEFAULT_TYPESCALE)[style]
    k = K_BOLD if style in BOLD_STYLES else K_REGULAR
    per_line = math.floor(element.box.w / (pt * UNITS_PER_PT * k))
    lines = max_lines_at(element, style, typescale)
    return max(0, math.floor(per_line * lines * (WRAP_LOSS if lines > 1 else 1)))


def max_lines_at(element: Element, style: str, typescale: dict | None = None) -> int:
    """Сколько строк слот вмещает стилем style. max_lines ограничивает базовый стиль;
    на шагах кегля вниз строк столько, сколько помещается по высоте бокса."""
    pt = (typescale or DEFAULT_TYPESCALE)[style]
    by_height = math.floor(element.box.h / (pt * UNITS_PER_PT * LINE_HEIGHT) + 1e-9)
    return min(element.max_lines, by_height) if style == element.style else max(element.max_lines, by_height)


def style_chain(base: str, minimum: str | None) -> list[str]:
    """Шаги кегля от base до minimum включительно."""
    i = STYLE_STEPS.index(base)
    j = STYLE_STEPS.index(minimum or base)
    return list(STYLE_STEPS[i:j + 1])


def _load(path) -> LayoutSpec:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    items = raw.get("items") or {}
    return LayoutSpec(
        id=raw["id"], kind=raw["kind"], family=raw["family"], version=int(raw.get("version", 1)),
        fallback=bool(raw.get("fallback", False)), applies_when=raw.get("applies_when") or {},
        raw_elements=raw.get("elements") or [], items_bind=items.get("bind"),
        arrangements={int(k): v for k, v in (items.get("arrangements") or {}).items()},
        background=raw.get("background", "bg"), header=bool(raw.get("header", False)),
        color_heavy=bool(raw.get("color_heavy", False)), decor=raw.get("decor"), compact=raw.get("compact"),
    )


@lru_cache(maxsize=None)
def load_layouts() -> dict[str, LayoutSpec]:
    """Все варианты из layouts/: id → LayoutSpec. Порядок — по пути файла (порядок каталога)."""
    specs = {}
    for path in sorted(LAYOUTS_DIR.glob("*/*.yaml")):
        spec = _load(path)
        if spec.id != f"{path.parent.name}.{path.stem}":
            raise ValueError(f"{path}: id {spec.id} does not match file path")
        specs[spec.id] = spec
    return specs


def variants_of(kind: str) -> list[LayoutSpec]:
    return [s for s in load_layouts().values() if s.kind == kind]


def layouts_version() -> str:
    """Версия набора макетов для meta.versions: число вариантов и хэш их id и версий."""
    import hashlib

    specs = load_layouts().values()
    digest = hashlib.sha1(",".join(f"{s.id}@{s.version}" for s in specs).encode()).hexdigest()[:8]
    return f"{len(specs)}v-{digest}"
