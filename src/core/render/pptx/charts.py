"""Нативные диаграммы PPTX: column, line, donut (D-028, 05_LAYOUTS.md, 3.7–3.8, 7).

Данные — снимок Slide.data, который код взял из dataset источника. Правила спайка:
без заголовка диаграммы (заголовок — у слайда); у кольца дырка 60% через XML
и нет подписей на секторах (значения — в легенде слайда); у столбцов и линий
подписи значений цветом text, ось значений с единицей, линии сетки — border.
"""

from lxml import etree
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from core.models.theme import Theme

LABELS_MAX = 24
_TYPES = {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS,
          "donut": XL_CHART_TYPE.DOUGHNUT}


def _rgb(hex_color: str):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(hex_color.lstrip("#"))


# Код локали в формате числа: разделители по языку колоды (ru — «2 050» и «47,3»)
LOCALE_CODES = {"ru": "[$-419]", "kk": "[$-43F]", "uz": "[$-443]", "en": ""}


def number_format(data: dict, language: str = "ru") -> str:
    """Формат числа подписей: столько знаков после запятой, сколько в источнике (до 2)."""
    decimals = int(data.get("decimals") or 0)
    return LOCALE_CODES.get(language, "") + "#,##0" + ("." + "0" * min(decimals, 2) if decimals else "")


def add_chart(slide_obj, data: dict, kind: str, box, theme: Theme, u, lang: str) -> None:
    categories = [c["label"] for c in data["categories"]]
    series = data["series"]
    chart_data = CategoryChartData(number_format=number_format(data, lang.split('-')[0]))
    chart_data.categories = categories
    for s in series:
        chart_data.add_series(s["name"], [v if v is not None else None for v in s["values"]])
    frame = slide_obj.shapes.add_chart(_TYPES[kind], u(box.x), u(box.y), u(box.w), u(box.h), chart_data)
    frame.name = f"chart:{kind}"
    chart = frame.chart
    chart.font.name = theme.fonts["body"]
    chart.font.size = Pt(theme.pt("small"))
    chart.font.color.rgb = _rgb(theme.color("text_muted"))
    chart.has_title = False
    plot = chart.plots[0]

    if kind == "donut":
        chart.has_legend = False
        plot.has_data_labels = False
        plot.vary_by_categories = True
        for i, point in enumerate(plot.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = _rgb(theme.color(f"chart_{i + 1}"))
            point.format.line.color.rgb = _rgb(theme.color("bg"))
        doughnut = chart._chartSpace.find(".//" + qn("c:doughnutChart"))
        hole = doughnut.find(qn("c:holeSize"))
        if hole is None:
            hole = etree.SubElement(doughnut, qn("c:holeSize"))
        hole.set("val", "60")
        return

    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(theme.pt("small"))
    # Подписи значений — если их не больше 24 (12 точек × 2 серии): иначе слипаются, значения — по оси
    plot.has_data_labels = len(categories) * len(series) <= LABELS_MAX
    if not plot.has_data_labels:
        _axes(chart, data, theme, lang)
        return
    labels = plot.data_labels
    labels.number_format = number_format(data, lang.split('-')[0])
    labels.number_format_is_linked = False
    labels.font.size = Pt(theme.pt("small"))
    labels.font.color.rgb = _rgb(theme.color("text"))
    labels.position = XL_LABEL_POSITION.OUTSIDE_END if kind == "column" else XL_LABEL_POSITION.ABOVE
    _axes(chart, data, theme, lang)


def _axes(chart, data: dict, theme: Theme, lang: str) -> None:
    kind = "column" if chart.chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED else "line"
    plot = chart.plots[0]
    series = data["series"]
    if kind == "column":
        plot.gap_width = 60
        plot.overlap = -10
    for i, s in enumerate(plot.series):
        color = _rgb(theme.color(f"chart_{i + 1}"))
        if kind == "column":
            s.format.fill.solid()
            s.format.fill.fore_color.rgb = color
        else:
            s.format.line.color.rgb = color
            s.format.line.width = Pt(3)
            s.smooth = False
            s.marker.format.fill.solid()
            s.marker.format.fill.fore_color.rgb = color
            s.marker.format.line.color.rgb = color
    va = chart.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = _rgb(theme.color("border"))
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(theme.pt("small"))
    va.tick_labels.number_format = number_format(data, lang.split('-')[0])
    va.tick_labels.number_format_is_linked = False
    unit = data.get("value_axis_title") or (series[0].get("unit") if len(series) == 1 else None)
    if unit:
        va.has_title = True
        va.axis_title.text_frame.text = unit
        run = va.axis_title.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(theme.pt("small"))
        run.font.bold = False
        run.font.color.rgb = _rgb(theme.color("text_muted"))
    ca = chart.category_axis
    ca.format.line.color.rgb = _rgb(theme.color("border"))
    ca.tick_labels.font.size = Pt(theme.pt("small"))
    ca.tick_labels.font.color.rgb = _rgb(theme.color("text_muted"))
    # язык подписей диаграммы — как у текста слайда
    for rpr in chart._chartSpace.iter(qn("a:defRPr")):
        rpr.set("lang", lang)
