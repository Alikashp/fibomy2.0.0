"""
Спайк PPTX (ТЗ, раздел 9, фаза 0).

Собирает вручную на python-pptx колоду из 5 слайдов — только нативные объекты:
  1. chart_series / column_chart   — нативная column-диаграмма + карточка-вывод
  2. table / native_table          — нативная таблица с выделенной строкой
  3. bullets / cards_grid          — карточки-фигуры с иконками (PNG) и текстом
  4. chart_share / donut           — нативная кольцевая диаграмма
  5. process / chevrons            — шаги процесса фигурами (замена Mermaid)
Затем конвертирует PPTX → PDF через LibreOffice (3 прогона, замер времени),
рендерит PNG-превью и пишет сводку в out/pptx_report.json.

Запуск из корня репозитория:
    python spikes/pptx_spike.py
Нужны: python-pptx, Pillow, fonttools, brotli, pymupdf, LibreOffice.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, THEME, icon_png, pdf_fonts, pdf_to_pngs, pptx_to_pdf, rgb, u  # noqa: E402

T = THEME
LANG = "ru-RU"
TOTAL_SLIDES = 5


# ── Текст ────────────────────────────────────────────────────────────────────

def text_box(slide, box, text, *, style="body", color="text", bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=None):
    """Текстовое поле без автоподбора (ТЗ 3.7: autofit = none, размеры задаём сами)."""
    x, y, w, h = box
    shape = slide.shapes.add_textbox(u(x), u(y), u(w), u(h))
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        f = run.font
        f.name = font or (T["font_heading"] if style in ("display", "h1", "h2", "h3") else T["font_body"])
        f.size = Pt(T["size"][style])
        f.bold = bold
        f.color.rgb = rgb(T[color])
        run._r.get_or_add_rPr().set("lang", LANG)  # язык текста: проверка орфографии, выбор шрифта
    return shape


def shape_fill(shape, color, line=None):
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(T[color])
    if line:
        shape.line.color.rgb = rgb(T[line])
        shape.line.width = Pt(1)
    else:
        shape.line.fill.background()
    # add_shape пишет <p:style> со ссылкой на эффекты темы (effectRef idx=2 —
    # тень). Все цвета заданы явно, поэтому стиль убираем целиком: иначе
    # тень появляется в LibreOffice и по-разному в других приложениях.
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def new_slide(prs, number, title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # пустой макет
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(T["bg"])
    # Заголовок-вывод (ТЗ 3.3.3). Поля слайда 80 единиц (ТЗ 3.4.2).
    # 36 pt × 2 строки × 1,2 интерлиньяжа ≈ 86 pt ≈ 173 единицы сетки (1 pt = 2 единицы)
    text_box(slide, (80, 64, 1760, 180), title, style="h1", bold=True, anchor=MSO_ANCHOR.MIDDLE)
    # Зарезервированная зона колонтитула: водяной знак (бесплатный тариф) и номер
    text_box(slide, (80, 1000, 600, 40), "Fibonacci.", style="small", color="text_muted", bold=True)
    text_box(slide, (1640, 1000, 200, 40), f"{number} / {TOTAL_SLIDES}", style="small",
             color="text_muted", align=PP_ALIGN.RIGHT)
    return slide


def footnote(slide, text):
    text_box(slide, (80, 950, 1760, 40), text, style="small", color="text_muted")


# ── Диаграммы ────────────────────────────────────────────────────────────────

def style_chart_text(chart, size="small"):
    chart.font.name = T["font_body"]
    chart.font.size = Pt(T["size"][size])
    chart.font.color.rgb = rgb(T["text_muted"])


def slide_column_chart(prs):
    s = new_slide(prs, 1, "Выручка выросла на 40% за год за счёт B2B-сегмента")
    data = CategoryChartData(number_format="0")
    data.categories = ["I кв. 2025", "II кв. 2025", "III кв. 2025", "IV кв. 2025"]
    data.add_series("B2B", (42, 51, 63, 78))
    data.add_series("B2C", (38, 40, 41, 43))
    gframe = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, u(80), u(280), u(1180), u(660), data)
    chart = gframe.chart
    style_chart_text(chart)
    chart.has_title = False
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    plot = chart.plots[0]
    plot.gap_width = 60
    plot.overlap = -10
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = "0"
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size = Pt(T["size"]["small"])
    for series, color in zip(plot.series, T["chart"]):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = rgb(color)
    va = chart.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb(T["border"])
    va.format.line.fill.background()
    va.has_title = True
    va.axis_title.text_frame.text = "млн ₽"  # ТЗ 7.1: у диаграммы есть единицы
    va.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(T["size"]["small"])
    ca = chart.category_axis
    ca.format.line.color.rgb = rgb(T["border"])
    ca.tick_labels.font.size = Pt(T["size"]["small"])

    card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, u(1320), u(280), u(520), u(660))
    shape_fill(card, "surface")
    card.adjustments[0] = 0.04
    # display 60 pt → строка ≈ 72 pt ≈ 144 единицы
    text_box(s, (1370, 320, 420, 150), "+40%", style="display", color="primary", bold=True)
    text_box(s, (1370, 480, 420, 60), "рост выручки год к году", style="body", color="text_muted")
    text_box(s, (1370, 580, 420, 320),
             "B2B вырос почти вдвое: с 42 до 78 млн ₽ за квартал. B2C почти не изменился.",
             style="body")
    footnote(s, "Источник: управленческая отчётность, 2025 (данные условные, для спайка)")


def slide_table(prs):
    s = new_slide(prs, 2, "B2B-клиенты приносят 64% выручки при 18% клиентской базы")
    rows = [
        ["Сегмент", "Клиенты", "Выручка, млн ₽", "Доля выручки"],
        ["B2B — крупный бизнес", "120", "154", "44%"],
        ["B2B — малый бизнес", "420", "81", "20%"],
        ["B2C — подписка", "2 100", "68", "19%"],
        ["B2C — разовые покупки", "3 300", "61", "17%"],
    ]
    highlight = {1}
    gframe = s.shapes.add_table(len(rows), len(rows[0]), u(80), u(290), u(1760), u(80 * len(rows)))
    table = gframe.table
    table.first_row = True
    table.horz_banding = False
    widths = [760, 300, 350, 350]
    for i, w in enumerate(widths):
        table.columns[i].width = u(w)
    for r, row in enumerate(rows):
        table.rows[r].height = u(80)
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = rgb(T["primary"])
            elif r in highlight:
                cell.fill.fore_color.rgb = rgb(T["surface_alt"])
            else:
                cell.fill.fore_color.rgb = rgb(T["bg"])
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = u(24)
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.RIGHT  # числа — вправо
            run = p.add_run()
            run.text = value
            run.font.name = T["font_body"]
            run.font.size = Pt(T["size"]["body"])
            run.font.bold = r == 0 or r in highlight
            run.font.color.rgb = rgb(T["on_primary"] if r == 0 else T["text"])
            run._r.get_or_add_rPr().set("lang", LANG)
            _cell_bottom_border(cell, T["border"])
    footnote(s, "Таблица нативная: её можно редактировать в PowerPoint, Keynote и Google Slides")


def _cell_bottom_border(cell, color):
    """В python-pptx нет API границ ячейки — пишем a:lnB в XML."""
    tcPr = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        for el in tcPr.findall(qn(tag)):
            tcPr.remove(el)
    for tag, visible in (("a:lnL", False), ("a:lnR", False), ("a:lnT", False), ("a:lnB", True)):
        ln = etree.SubElement(tcPr, qn(tag), w="12700" if visible else "0")
        if visible:
            fill = etree.SubElement(ln, qn("a:solidFill"))
            etree.SubElement(fill, qn("a:srgbClr"), val=color.lstrip("#"))
        else:
            etree.SubElement(ln, qn("a:noFill"))
    # a:ln* должны идти перед заливкой ячейки — иначе PowerPoint просит восстановить файл
    fills = [el for el in tcPr if el.tag in (qn("a:solidFill"), qn("a:noFill"))]
    for el in fills:
        tcPr.remove(el)
        tcPr.append(el)


def slide_cards(prs):
    s = new_slide(prs, 3, "Три фактора роста: продукт, продажи и сервис")
    cards = [
        ("target", "Продукт", "Запустили тариф для команд — средний чек вырос на 22%."),
        ("users", "Продажи", "Отдел B2B вырос с 3 до 8 человек, цикл сделки сократился до 21 дня."),
        ("shield", "Сервис", "Поддержка 24/7 снизила отток B2B-клиентов с 9% до 4% в год."),
    ]
    gap, top, height = 40, 290, 630
    width = (1760 - gap * (len(cards) - 1)) / len(cards)
    for i, (icon, head, body) in enumerate(cards):
        x = 80 + i * (width + gap)
        card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, u(x), u(top), u(width), u(height))
        shape_fill(card, "surface")
        card.adjustments[0] = 0.05
        badge = s.shapes.add_shape(MSO_SHAPE.OVAL, u(x + 48), u(top + 48), u(120), u(120))
        shape_fill(badge, "primary")
        s.shapes.add_picture(icon_png(icon, T["on_primary"]), u(x + 72), u(top + 72), u(72), u(72))
        text_box(s, (x + 48, top + 210, width - 96, 70), head, style="h2", bold=True)
        text_box(s, (x + 48, top + 300, width - 96, 300), body, style="body", color="text_muted")


def slide_donut(prs):
    s = new_slide(prs, 4, "Две трети бюджета проекта уходит на команду")
    data = CategoryChartData(number_format="0%")
    data.categories = ["Команда", "Инфраструктура", "Маркетинг", "Прочее"]
    data.add_series("Доля бюджета", (0.66, 0.16, 0.12, 0.06))
    gframe = s.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, u(80), u(280), u(1100), u(660), data)
    chart = gframe.chart
    style_chart_text(chart, "body")
    chart.has_title = False  # у диаграммы с одной серией иначе появляется автозаголовок
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.RIGHT
    chart.legend.include_in_layout = False
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.show_value = True  # для кольца python-pptx по умолчанию пишет showVal=0
    plot.data_labels.number_format = "0%"
    plot.data_labels.number_format_is_linked = False
    plot.data_labels.font.size = Pt(T["size"]["body"])
    plot.data_labels.font.color.rgb = rgb(T["on_primary"])
    plot.vary_by_categories = True
    for point, color in zip(plot.series[0].points, T["chart"]):
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = rgb(color)
    # Размер «дырки» кольца — через XML (в python-pptx нет API)
    doughnut = chart._chartSpace.find(".//" + qn("c:doughnutChart"))
    hole = doughnut.find(qn("c:holeSize"))
    if hole is None:
        hole = etree.SubElement(doughnut, qn("c:holeSize"))
    hole.set("val", "60")
    text_box(s, (1260, 320, 580, 150), "66%", style="display", color="primary", bold=True)
    text_box(s, (1260, 490, 580, 300),
             "Зарплаты и подрядчики — основная статья. Инфраструктура и маркетинг вместе — 28%.",
             style="body")


def slide_process(prs):
    s = new_slide(prs, 5, "Внедрение занимает четыре шага и около 10 недель")
    steps = [
        ("Аудит", "2 недели", "Собираем требования и данные"),
        ("Пилот", "3 недели", "Запуск на одной команде"),
        ("Масштаб", "4 недели", "Подключаем остальные отделы"),
        ("Поддержка", "1 неделя", "Обучение и передача дел"),
    ]
    gap, top, height = 16, 330, 160
    width = (1760 - gap * (len(steps) - 1)) / len(steps)
    for i, (name, dur, desc) in enumerate(steps):
        x = 80 + i * (width + gap)
        kind = MSO_SHAPE.PENTAGON if i == 0 else MSO_SHAPE.CHEVRON
        sh = s.shapes.add_shape(kind, u(x), u(top), u(width), u(height))
        shape_fill(sh, "primary" if i % 2 == 0 else "accent")
        tf = sh.text_frame
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = f"{i + 1}. {name}"
        run.font.name = T["font_heading"]
        run.font.size = Pt(T["size"]["h3"])
        run.font.bold = True
        run.font.color.rgb = rgb(T["on_primary"])
        run._r.get_or_add_rPr().set("lang", LANG)
        text_box(s, (x + 24, top + height + 40, width - 48, 50), dur, style="h3", color="primary", bold=True)
        text_box(s, (x + 24, top + height + 100, width - 48, 200), desc, style="body", color="text_muted")
    s.notes_slide.notes_text_frame.text = (
        "Источники изображений: в спайке фотографий нет. "
        "В проде здесь будет список «Фото: Имя / Pexels» (ТЗ 3.6)."
    )


# ── Метаданные ───────────────────────────────────────────────────────────────

def set_metadata(prs, title, author):
    """ТЗ 3.8: title, author = пользователь, creator = Fibonacci AI."""
    cp = prs.core_properties
    cp.title = title
    cp.author = author
    cp.last_modified_by = "Fibonacci AI"
    cp.comments = "Создано Fibonacci AI"
    for part in prs.part.package.iter_parts():
        if str(part.partname) == "/docProps/app.xml":
            xml = part.blob.decode("utf-8")
            xml = re.sub(r"<Application>.*?</Application>", "<Application>Fibonacci AI</Application>", xml)
            part._blob = xml.encode("utf-8")


# ── Проверки ─────────────────────────────────────────────────────────────────

def inspect_pptx(path: Path) -> list[dict]:
    """Читаем файл обратно: что за объекты на каждом слайде (ТЗ 7.1 — нет слайдов-растров)."""
    prs = Presentation(str(path))
    report = []
    for i, slide in enumerate(prs.slides, start=1):
        kinds = {}
        out_of_bounds = 0
        autofit_on = 0
        for sh in slide.shapes:
            kind = ("chart" if sh.has_chart else "table" if sh.has_table
                    else "picture" if sh.shape_type == 13 else "text" if sh.has_text_frame and not sh.is_placeholder and sh.shape_type == 17
                    else "shape")
            kinds[kind] = kinds.get(kind, 0) + 1
            if sh.left < 0 or sh.top < 0 or sh.left + sh.width > prs.slide_width or sh.top + sh.height > prs.slide_height:
                out_of_bounds += 1
            if sh.has_text_frame:
                body = sh.text_frame._txBody.find(qn("a:bodyPr"))
                if body is not None and (body.find(qn("a:normAutofit")) is not None or body.find(qn("a:spAutoFit")) is not None):
                    autofit_on += 1
        report.append({"slide": i, "objects": kinds, "out_of_bounds": out_of_bounds, "autofit_on": autofit_on})
    return report


BUILDERS = (slide_column_chart, slide_table, slide_cards, slide_donut, slide_process)


def build(repeat: int = 1) -> Presentation:
    prs = Presentation()
    prs.slide_width = Emu(12_192_000)   # 16:9, ТЗ 3.4.2
    prs.slide_height = Emu(6_858_000)
    set_metadata(prs, "Итоги 2025: рост за счёт B2B", "Иван Петров")
    for _ in range(repeat):
        for builder in BUILDERS:
            builder(prs)
    return prs


def main():
    OUT.mkdir(exist_ok=True)
    pptx_path = OUT / "spike_deck.pptx"
    build().save(pptx_path)

    # Замер на 20 слайдах (4 копии) — бюджет этапа CONVERT 10 с (ТЗ 4.5).
    # Файл во временной папке: в репозиторий его не кладём.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        big = Path(tmp) / "spike_deck_20.pptx"
        build(repeat=4).save(big)
        timings_20 = [round(pptx_to_pdf(big, Path(tmp))[1], 2) for _ in range(3)]

    timings = []
    for _ in range(3):
        pdf_path, elapsed = pptx_to_pdf(pptx_path, OUT)
        timings.append(round(elapsed, 2))
    pngs = pdf_to_pngs(pdf_path, OUT / "previews")

    report = {
        "pptx": pptx_path.name,
        "pptx_kb": round(pptx_path.stat().st_size / 1024, 1),
        "pdf_kb": round(pdf_path.stat().st_size / 1024, 1),
        "libreoffice_convert_sec": timings,
        "libreoffice_convert_median_sec": statistics.median(timings),
        "libreoffice_convert_20_slides_sec": timings_20,
        "slides": inspect_pptx(pptx_path),
        "pdf_fonts": pdf_fonts(pdf_path),
        "previews": [p.relative_to(OUT).as_posix() for p in pngs],
    }
    (OUT / "pptx_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
