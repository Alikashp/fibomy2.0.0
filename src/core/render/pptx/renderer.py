"""RENDER: DeckSpec → PPTX (python-pptx). Рендерер рисует DeckSpec как есть и ничего не решает.

Правила из спайка (05_LAYOUTS.md, 7; docs/SPIKE_PPTX.md, 2):
1. у каждой фигуры удаляется <p:style>, цвета заливки и линии заданы явно — без теней;
2. автоподбор размера выключен, поля текстовых рамок нулевые, кегль — от fitter'а;
3. у каждого фрагмента текста атрибут lang по языку колоды;
4. метаданные: заголовок — тема, автор — пользователь, Application — «Fibonacci AI».

Колонтитул (05_LAYOUTS.md, 1): слева «Fibonacci.» — только в копии для PDF
бесплатного тарифа (watermark=True), справа номер «N / M» на содержательных слайдах.
"""

import io
import re

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from core import i18n
from core.models import bind as B
from core.models.deck import DeckSpec, Slide
from core.models.layout import Element, LayoutSpec, load_layouts
from core.models.theme import BOLD_STYLES, Theme, load_theme
from core.render.pptx.charts import add_chart
from core.render.pptx.icons import icon_png

EMU_PER_UNIT = 6350
SLIDE_W_EMU, SLIDE_H_EMU = 12_192_000, 6_858_000
APP_NAME = "Fibonacci AI"
WATERMARK = "Fibonacci."

_ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
_ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}


def u(value: float) -> Emu:
    return Emu(int(round(value * EMU_PER_UNIT)))


def _rgb(hex_color: str):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(hex_color.lstrip("#"))


def _no_style(shape) -> None:
    """<p:style> ссылается на эффекты темы (тень) — убираем, цвета заданы явно."""
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


class _Ctx:
    def __init__(self, spec: DeckSpec, theme: Theme):
        self.spec = spec
        self.theme = theme
        self.lang = i18n.lang_tag(spec.meta.language)


def _text(slide_obj, ctx: _Ctx, element: Element, text: str, style: str, color: str | None = None) -> None:
    box = element.box
    shape = slide_obj.shapes.add_textbox(u(box.x), u(box.y), u(box.w), u(box.h))
    shape.name = element.name
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = _ANCHOR.get(element.anchor, MSO_ANCHOR.TOP)
    pt = ctx.theme.pt(style)
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = _ALIGN.get(element.align, PP_ALIGN.LEFT)
        p.line_spacing = Pt(pt * 1.2)  # та же высота строки, что у fitter'а
        run = p.add_run()
        run.text = line
        font = run.font
        font.name = ctx.theme.fonts["heading" if style in BOLD_STYLES else "body"]
        font.size = Pt(pt)
        font.bold = style in BOLD_STYLES
        font.italic = False
        font.color.rgb = _rgb(ctx.theme.color(color or element.color))
        run._r.get_or_add_rPr().set("lang", ctx.lang)


def _fill_token(element: Element, index: int | None) -> str:
    token = element.fill or "surface"
    return f"chart_{(index or 0) + 1}" if token == "$chart" else token


def _rect(slide_obj, ctx: _Ctx, element: Element, index: int | None = None) -> None:
    box = element.box
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if element.radius else MSO_SHAPE.RECTANGLE
    shape = slide_obj.shapes.add_shape(kind, u(box.x), u(box.y), u(box.w), u(box.h))
    shape.name = element.name
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(ctx.theme.color(_fill_token(element, index)))
    shape.line.fill.background()
    if element.radius:
        radius = float(ctx.theme.style.get("radius", 8))
        shape.adjustments[0] = min(0.5, radius / max(1.0, min(box.w, box.h)))
    _no_style(shape)


def _icon(slide_obj, ctx: _Ctx, element: Element, name: str | None) -> None:
    box = element.box
    png = icon_png(name or "", ctx.theme.color(element.color))
    pic = slide_obj.shapes.add_picture(io.BytesIO(png), u(box.x), u(box.y), u(box.w), u(box.h))
    pic.name = f"{element.name}:{name}"


def _badge(slide_obj, ctx: _Ctx, element: Element, text: str) -> None:
    """Кружок цвета fill с номером цветом color по центру."""
    box = element.box
    shape = slide_obj.shapes.add_shape(MSO_SHAPE.OVAL, u(box.x), u(box.y), u(box.w), u(box.h))
    shape.name = element.name
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(ctx.theme.color(element.fill or "primary"))
    shape.line.fill.background()
    _no_style(shape)
    tf = shape.text_frame
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    style = element.style or "h3"
    run.font.name = ctx.theme.fonts["heading"]
    run.font.size = Pt(ctx.theme.pt(style))
    run.font.bold = True
    run.font.color.rgb = _rgb(ctx.theme.color(element.color or "on_primary"))
    run._r.get_or_add_rPr().set("lang", ctx.lang)


def _draw(slide_obj, ctx: _Ctx, slide: Slide, element: Element, key: str,
          item: dict | None = None, index: int | None = None) -> None:
    if element.type in ("rect", "line"):
        _rect(slide_obj, ctx, element, index)
        return
    value = B.read(element, slide, ctx.spec, item=item, index=index)
    if element.type == "chart":
        if value:
            add_chart(slide_obj, value, element.chart or value.get("chart", "column"), element.box, ctx.theme, u,
                      ctx.lang)
        return
    if element.type == "badge":
        _badge(slide_obj, ctx, element, str(value if value is not None else ""))
        return
    if element.type == "icon":
        _icon(slide_obj, ctx, element, value)
        return
    if value in (None, ""):
        return
    style = slide.fit.styles.get(key) or element.style
    _text(slide_obj, ctx, element, str(value), style)


def _footer(slide_obj, ctx: _Ctx, slide: Slide, total: int, watermark: bool) -> None:
    small = Element(name="footer", type="text", box=_box(80, 1012, 600, 40), style="small", color="text_muted")
    if watermark:
        _text(slide_obj, ctx, small, WATERMARK, "small")
    if slide.kind not in ("title", "closing"):
        number = Element(name="page_number", type="text", box=_box(1440, 1012, 400, 40), style="small",
                         color="text_muted", align="right")
        _text(slide_obj, ctx, number, f"{slide.index} / {total}", "small")


def _box(x, y, w, h):
    from core.models.layout import Box
    return Box(x, y, w, h)


def _render_slide(prs, ctx: _Ctx, slide: Slide, layout: LayoutSpec, total: int, watermark: bool) -> None:
    slide_obj = prs.slides.add_slide(prs.slide_layouts[6])  # пустой макет
    slide_obj.background.fill.solid()
    slide_obj.background.fill.fore_color.rgb = _rgb(ctx.theme.color("bg"))
    for element in layout.elements():
        _draw(slide_obj, ctx, slide, element, element.name)
    items = B.items_of(layout.items_bind, slide)
    if items and len(items) in layout.arrangements:
        for i, (item, elements) in enumerate(zip(items, layout.item_elements(len(items)))):
            for element in elements:
                _draw(slide_obj, ctx, slide, element, f"item.{element.name}", item=item, index=i)
    _footer(slide_obj, ctx, slide, total, watermark)
    if slide.notes:
        slide_obj.notes_slide.notes_text_frame.text = slide.notes


def _metadata(prs, spec: DeckSpec) -> None:
    cp = prs.core_properties
    cp.title = spec.meta.title
    cp.author = (spec.meta.author or {}).get("name") or ""
    cp.last_modified_by = APP_NAME
    cp.comments = f"Создано {APP_NAME} · {spec.id}"
    for part in prs.part.package.iter_parts():
        if str(part.partname) == "/docProps/app.xml":
            xml = part.blob.decode("utf-8")
            xml = re.sub(r"<Application>.*?</Application>", f"<Application>{APP_NAME}</Application>", xml)
            part._blob = xml.encode("utf-8")


def render_pptx(spec: DeckSpec, watermark: bool = False) -> bytes:
    """DeckSpec → байты PPTX. watermark=True — копия для PDF бесплатного тарифа."""
    theme = load_theme(spec.meta.theme_id)
    layouts = load_layouts()
    ctx = _Ctx(spec, theme)
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W_EMU)
    prs.slide_height = Emu(SLIDE_H_EMU)
    _metadata(prs, spec)
    total = len(spec.slides)
    for slide in spec.slides:
        _render_slide(prs, ctx, slide, layouts[slide.variant], total, watermark)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()
