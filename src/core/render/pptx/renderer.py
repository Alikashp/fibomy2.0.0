"""RENDER: DeckSpec → PPTX (python-pptx). Рендерер рисует DeckSpec как есть и ничего не решает.

Правила из спайка (05_LAYOUTS.md, 7; docs/SPIKE_PPTX.md, 2):
1. у каждой фигуры удаляется <p:style>, цвета заливки и линии заданы явно — без теней;
2. автоподбор размера выключен, поля текстовых рамок нулевые, кегль — от fitter'а;
3. у каждого фрагмента текста атрибут lang по языку колоды;
4. метаданные: заголовок — тема, автор — пользователь, Application — «Fibonacci AI».

Оформление темы (сессия 4, 05_LAYOUTS.md, 6.3 и 8; D-064): фон слайда — bg или градиент
brand → brand_2 (титул, финал, пауза), декоративные фигуры темы, оформление заголовка
(плашка, подчёркивание, маркер, полоса), карточки (заливка или обводка, цветная полоса),
номера в фигурах темы, иконки в градиентной подложке, картинки со скруглением.
Всё — нативные фигуры, перекрашиваемые в PowerPoint.

Колонтитул (05_LAYOUTS.md, 1): слева «Fibonacci.» — только в копии для PDF
бесплатного тарифа (watermark=True), справа номер «N / M» на содержательных слайдах.
"""

import io
import re
from dataclasses import replace

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from core import i18n
from core.models import bind as B
from core.models.deck import DeckSpec, Slide
from core.models.layout import GRID_H, GRID_W, Box, Element, LayoutSpec, load_layouts
from core.models.theme import BOLD_STYLES, Theme, load_theme
from core.render.pptx import shapes as S
from core.render.pptx.charts import add_chart
from core.render.pptx.icons import icon_png
from core.render.pptx.shapes import u

SLIDE_W_EMU, SLIDE_H_EMU = 12_192_000, 6_858_000
APP_NAME = "Fibonacci AI"
WATERMARK = "Fibonacci."
SLIDE_BOX = Box(0, 0, GRID_W, GRID_H)
HEADER_PLATE = Box(0, 0, GRID_W, 264)

_ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
_ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}


class _Ctx:
    def __init__(self, spec: DeckSpec, theme: Theme, images: dict[str, bytes]):
        self.spec = spec
        self.theme = theme
        self.lang = i18n.lang_tag(spec.meta.language)
        self.images = images


def _text(slide_obj, ctx: _Ctx, element: Element, text: str, style: str, color: str | None = None,
          box: Box | None = None) -> None:
    box = box or element.box
    shape = slide_obj.shapes.add_textbox(u(box.x), u(box.y), u(box.w), u(box.h))
    shape.name = element.name
    tf = shape.text_frame
    # Однострочное число, которое нельзя резать (never_truncate): без переноса — разница метрик
    # LibreOffice и PowerPoint не уносит последнюю цифру на невидимую вторую строку
    tf.word_wrap = not (element.never_truncate and element.max_lines == 1)
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
        font.color.rgb = S.rgb(ctx.theme.color(color or element.color))
        run._r.get_or_add_rPr().set("lang", ctx.lang)


def _fill_token(element: Element, index: int | None):
    token = element.fill or "surface"
    return f"chart_{(index or 0) + 1}" if token == "$chart" else token


def _radius(ctx: _Ctx, element: Element) -> float:
    if element.radius in (True, "top"):
        return float(ctx.theme.style.get("radius", 8))
    return float(element.radius or 0)


def _rect(slide_obj, ctx: _Ctx, element: Element, box: Box, index: int | None = None) -> None:
    S.rect(slide_obj, ctx.theme, box, _fill_token(element, index), radius=_radius(ctx, element),
           alpha=element.alpha, line=element.line, name=element.name, top_only=element.radius == "top")


def _card(slide_obj, ctx: _Ctx, element: Element, box: Box, index: int | None = None) -> None:
    """Карточка по стилю темы: заливка surface (на цветном фоне — paper) или обводка card_line;
    цветная полоса (stripe_fill) слева или сверху. У заливки полоса — нижний слой, тело сдвинуто;
    у обводки — тело с обводкой и поверх шапка-полоса со скруглёнными верхними углами."""
    style = ctx.theme.style
    radius = float(style.get("radius", 8))
    paper = element.tone == "paper"
    outlined = style.get("card") == "outline" and not paper
    body = "paper" if paper else ("bg" if outlined and ctx.theme.mode == "light" else "surface")
    if outlined and ctx.theme.colors["bg"] != ctx.theme.colors["paper"] and ctx.theme.mode == "light":
        body = "paper"                       # белая карточка на молочном фоне
    stripe = style.get("card_stripe", "none") if not paper else "none"
    stripe_fill = style.get("stripe_fill", "primary")
    if outlined:
        S.rect(slide_obj, ctx.theme, box, body, radius=radius, line=style.get("card_line", "primary"),
               line_width=5, name=element.name)
        if stripe == "top":
            S.rect(slide_obj, ctx.theme, Box(box.x, box.y, box.w, 14), stripe_fill, radius=radius,
                   name=f"{element.name}:stripe", angle=0, top_only=True)
        return
    if stripe in ("left", "top"):
        S.rect(slide_obj, ctx.theme, box, stripe_fill, radius=radius, name=f"{element.name}:stripe", angle=0)
        inset = 10 if stripe == "left" else 12
        box = Box(box.x + inset, box.y, box.w - inset, box.h) if stripe == "left" else \
            Box(box.x, box.y + inset, box.w, box.h - inset)
    S.rect(slide_obj, ctx.theme, box, body, radius=radius, name=element.name)


def _icon(slide_obj, ctx: _Ctx, element: Element, name: str | None, box: Box) -> None:
    color = element.color
    if element.plate:
        plate = ctx.theme.style.get("icon_plate", "gradient")
        if plate in ("gradient", "accent"):
            fill = "$brand" if plate == "gradient" else "accent"
            S.rect(slide_obj, ctx.theme, box, fill, radius=min(box.w, box.h) * 0.28, name=f"{element.name}:plate")
            color = "on_brand" if plate == "gradient" else "on_accent"
            pad = box.w * 0.22
            box = Box(box.x + pad, box.y + pad, box.w - 2 * pad, box.h - 2 * pad)
    png = icon_png(name or "", ctx.theme.color(color))
    pic = slide_obj.shapes.add_picture(io.BytesIO(png), u(box.x), u(box.y), u(box.w), u(box.h))
    pic.name = f"{element.name}:{name}"


def _badge(slide_obj, ctx: _Ctx, element: Element, text: str, box: Box) -> None:
    """Номер в фигуре темы (круг, квадрат, флажок, шеврон): заливка fill, цифра — color."""
    fill = element.fill or "primary"
    color = element.color if element.color != "text" else ("on_brand" if fill == "$brand" else "on_primary")
    if fill == "primary":
        # Номер — цветом номеров темы: у «молочной» — жёлтый квадрат с тёмной цифрой
        fill = ctx.theme.style.get("badge_fill", "primary")
        color = ctx.theme.style.get("badge_text", color)
    _, text_box = S.badge_shape(slide_obj, ctx.theme, box, fill, element.name)
    shape = slide_obj.shapes.add_textbox(u(text_box.x), u(text_box.y), u(text_box.w), u(text_box.h))
    shape.name = f"{element.name}:number"
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
    run.font.color.rgb = S.rgb(ctx.theme.color(color))
    run._r.get_or_add_rPr().set("lang", ctx.lang)


def _chevron(slide_obj, ctx: _Ctx, element: Element, box: Box, index: int | None) -> None:
    kind = MSO_SHAPE.PENTAGON if not index else MSO_SHAPE.CHEVRON
    S.preset(slide_obj, ctx.theme, kind, box, element.fill or "$brand", element.name, adjustments=[0.35])


def _image(slide_obj, ctx: _Ctx, element: Element, asset_id: str | None, box: Box) -> None:
    data = ctx.images.get(asset_id or "")
    if not data:
        return
    pic = slide_obj.shapes.add_picture(io.BytesIO(data), u(box.x), u(box.y), u(box.w), u(box.h))
    pic.name = f"{element.name}:{asset_id}"
    radius = float(ctx.theme.style.get("image_radius", 0)) if element.radius is True else float(element.radius or 0)
    if radius:
        geom = pic._element.spPr.find(qn("a:prstGeom"))
        geom.set("prst", "roundRect")
        av = geom.find(qn("a:avLst"))
        if av is None:
            av = etree.SubElement(geom, qn("a:avLst"))
        gd = etree.SubElement(av, qn("a:gd"))
        gd.set("name", "adj")
        gd.set("fmla", f"val {int(min(50000, radius / min(box.w, box.h) * 100000))}")


STYLE_DEFAULTS = {"pause_text": "on_brand", "quote_plate": "surface_alt", "quote_text": "primary",
                  "accent_fill": "$brand", "heading_color": "primary"}
DEFAULT_PANEL = Box(64, 56, 1792, 940)


def panel_mode(ctx: "_Ctx", layout: LayoutSpec) -> bool:
    """Тема разрешает целиком цветными только титул и финал (style.full_color: cover): у остальных
    «цветных» слайдов (пауза, утверждение с картинкой) — обычный фон и цветная панель (D-070)."""
    return (layout.background in ("brand", "pause") and layout.kind not in ("title", "closing")
            and ctx.theme.style.get("full_color") == "cover")


def _panel_fill(ctx: "_Ctx", layout: LayoutSpec) -> str:
    return "accent" if layout.background == "pause" and ctx.theme.style.get("pause") == "accent" else "$brand"


def _style_ref(ctx: _Ctx, element: Element) -> Element:
    """«@имя» в fill и color — значение из style темы (у каждой темы своя плашка цитаты и т. п.)."""
    changes = {}
    for attr in ("fill", "color"):
        value = getattr(element, attr)
        if isinstance(value, str) and value.startswith("@"):
            changes[attr] = ctx.theme.style.get(value[1:], STYLE_DEFAULTS.get(value[1:]))
    return replace(element, **changes) if changes else element


def _draw(slide_obj, ctx: _Ctx, slide: Slide, element: Element, key: str,
          item: dict | None = None, index: int | None = None, color: str | None = None,
          count: int | None = None) -> None:
    element = _style_ref(ctx, element)
    if element.skip_last and index is not None and count is not None and index == count - 1:
        return
    if element.when and B.read(replace(element, bind=element.when, fmt=None), slide, ctx.spec,
                               item=item, index=index) in (None, ""):
        return
    box = Box(*slide.fit.boxes[element.name]) if item is None and element.name in slide.fit.boxes else element.box
    if element.type in ("rect", "line"):
        _rect(slide_obj, ctx, element, box, index)
        return
    if element.type == "card":
        _card(slide_obj, ctx, element, box, index)
        return
    if element.type == "chevron":
        _chevron(slide_obj, ctx, element, box, index)
        return
    value = B.read(element, slide, ctx.spec, item=item, index=index)
    if element.type == "image":
        _image(slide_obj, ctx, element, value, box)
        return
    if element.type == "chart":
        if value:
            add_chart(slide_obj, value, element.chart or value.get("chart", "column"), box, ctx.theme, u,
                      ctx.lang)
        return
    if element.type == "badge":
        _badge(slide_obj, ctx, element, str(value if value is not None else ""), box)
        return
    if element.type == "icon":
        _icon(slide_obj, ctx, element, value, box)
        return
    if value in (None, ""):
        return
    style = slide.fit.styles.get(key) or element.style
    _text(slide_obj, ctx, element, str(value), style, color, box=box)


def _header(slide_obj, ctx: _Ctx, layout: LayoutSpec) -> str | None:
    """Оформление заголовка содержательного слайда по теме → цвет заголовка (или None — как в макете).
    Заголовок слайдов с картинкой занимает свою половину: плашка — только над ним."""
    kind = ctx.theme.style.get("header", "underline")
    title = next((e for e in layout.elements() if e.name == "title"), None)
    x0 = 0 if title is None or title.box.x <= 80 else title.box.x - 80
    x1 = GRID_W if title is None or title.box.x + title.box.w >= 1840 else title.box.x + title.box.w + 40
    tx = x0 + 80 if x0 == 0 else title.box.x
    if kind == "plate":
        plate = Box(x0, 0, x1 - x0, HEADER_PLATE.h)
        S.rect(slide_obj, ctx.theme, plate, "$brand", name="header:plate", angle=0)
        S.draw_decor(slide_obj, ctx.theme, ctx.theme.decor.get("header", []), plate, "header")
        return "on_brand"
    title_color = ctx.theme.style.get("title_color")
    if kind == "underline":
        S.rect(slide_obj, ctx.theme, Box(tx, 262, 180, 8), "$brand", radius=4, name="header:underline", angle=0)
    elif kind == "marker":
        S.rect(slide_obj, ctx.theme, Box(tx - 14, 214, 380, 44), "accent", radius=22, name="header:marker")
    elif kind == "bar":
        S.rect(slide_obj, ctx.theme, Box(tx - 40, 104, 14, 128), "$brand", radius=7, name="header:bar", angle=90)
    return title_color


def _decor(slide_obj, ctx: _Ctx, layout: LayoutSpec) -> None:
    sets = layout.decor
    panel = panel_mode(ctx, layout)
    if panel:
        box = Box(*layout.panel) if layout.panel else DEFAULT_PANEL
        S.rect(slide_obj, ctx.theme, box, _panel_fill(ctx, layout), radius=float(ctx.theme.style.get("radius", 8)),
               name="panel:color")
        sets = [{"set": "pause", "region": [box.x, box.y, box.w, box.h]}]
    elif sets is None:
        sets = [{"set": {"brand": "cover", "pause": "pause"}.get(layout.background, "content")}]
    for d in sets:
        region = Box(*d["region"]) if d.get("region") else SLIDE_BOX
        items = ctx.theme.decor.get(d["set"])
        if items is None and d["set"] == "pause":
            items = ctx.theme.decor.get("cover", [])     # пауза в цвете brand — декор титула
        S.draw_decor(slide_obj, ctx.theme, items or [], region, f"decor:{d['set']}")


def _footer(slide_obj, ctx: _Ctx, slide: Slide, total: int, watermark: bool, layout: LayoutSpec) -> None:
    color = {"brand": "on_brand", "pause": ctx.theme.style.get("pause_text", "on_brand")}.get(layout.background,
                                                                                         "text_muted")
    if panel_mode(ctx, layout):
        color = "text_muted"                        # колонтитул — под панелью, на фоне слайда
    small = Element(name="footer", type="text", box=Box(80, 1012, 600, 40), style="small", color=color)
    if watermark:
        _text(slide_obj, ctx, small, WATERMARK, "small")
    if slide.kind not in ("title", "closing"):
        number = Element(name="page_number", type="text", box=Box(1440, 1012, 400, 40), style="small",
                         color=color, align="right")
        _text(slide_obj, ctx, number, f"{slide.index} / {total}", "small")


def _background(slide_obj, ctx: _Ctx, layout: LayoutSpec) -> None:
    fill = slide_obj.background.fill
    background = "bg" if panel_mode(ctx, layout) else layout.background
    if background == "pause":
        # Слайд-пауза: в цвете brand или (тема так решила) сплошным accent
        background = "accent" if ctx.theme.style.get("pause") == "accent" else "brand"
    if background == "accent":
        fill.solid()
        fill.fore_color.rgb = S.rgb(ctx.theme.color("accent"))
    elif background == "brand" and ctx.theme.colors["brand"].upper() == ctx.theme.colors["brand_2"].upper():
        fill.solid()                                # плоская тема — сплошная заливка
        fill.fore_color.rgb = S.rgb(ctx.theme.color("brand"))
    elif background == "brand":
        fill.gradient()
        fill.gradient_angle = float(ctx.theme.style.get("gradient_angle", 0))
        for stop, token in zip(fill.gradient_stops, ("brand", "brand_2")):
            stop.color.rgb = S.rgb(ctx.theme.color(token))
    else:
        fill.solid()
        fill.fore_color.rgb = S.rgb(ctx.theme.color("bg"))


def _render_slide(prs, ctx: _Ctx, slide: Slide, layout: LayoutSpec, total: int, watermark: bool) -> None:
    slide_obj = prs.slides.add_slide(prs.slide_layouts[6])  # пустой макет
    _background(slide_obj, ctx, layout)
    _decor(slide_obj, ctx, layout)
    title_color, header = None, ctx.theme.style.get("header", "underline")
    if layout.header:
        title_color = _header(slide_obj, ctx, layout)
    for element in layout.elements():
        color = None
        if element.name == "title" and layout.header:
            color = title_color
            # Подчёркивание и маркер — у нижней строки заголовка; плашка — заголовок по её центру
            if header in ("underline", "marker"):
                element = replace(element, anchor="bottom")
            elif header == "plate":
                element = replace(element, box=Box(element.box.x, 44, element.box.w, element.box.h))
        _draw(slide_obj, ctx, slide, element, element.name, color=color)
    items = B.items_of(layout.items_bind, slide)
    if items and len(items) in layout.arrangements:
        cells = layout.item_elements(len(items), slide.fit.items_area)
        for i, (item, elements) in enumerate(zip(items, cells)):
            for element in elements:
                _draw(slide_obj, ctx, slide, element, f"item.{element.name}", item=item, index=i,
                      count=len(items))
    _footer(slide_obj, ctx, slide, total, watermark, layout)
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


def render_pptx(spec: DeckSpec, watermark: bool = False, images: dict[str, bytes] | None = None) -> bytes:
    """DeckSpec → байты PPTX. watermark=True — копия для PDF бесплатного тарифа.
    images — файлы картинок по id из spec.assets (JPEG, уже обрезаны под слот)."""
    theme = load_theme(spec.meta.theme_id)
    layouts = load_layouts()
    ctx = _Ctx(spec, theme, images or {})
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
