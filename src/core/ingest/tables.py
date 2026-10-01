"""Таблицы и ряды чисел → Dataset с адресами ячеек (04_CONTRACTS.md, 3; ТЗ 4.8.2).

Таблица: первая строка — заголовки колонок (единица — после запятой или в
скобках: «Выручка, млн руб.», «Доля (%)»), первая колонка — подписи строк.
Строка «Итого / Всего» помечается is_total и не входит в ряд диаграммы.
Ряд в тексте («январь — 310, февраль — 720, …») — Dataset с origin text_series.
"""

import re

from core.ingest.numbers import parse_cell
from core.models.digest import Cell, Column, Dataset, Row

TOTAL_RE = re.compile(r"^\s*(итого|всего|total|барлығы|jami)\b", re.IGNORECASE)
_MONTHS = (r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|"
           r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
           r"қаңтар|ақпан|наурыз|сәуір|мамыр|маусым|шілде|тамыз|қыркүйек|қазан|қараша|желтоқсан|"
           r"yanvar|fevral|aprel|iyun|iyul|avgust|sentyabr|oktyabr|noyabr|dekabr")
TIME_RE = re.compile(rf"^\s*(({_MONTHS})\w*|[ivx]+\s*кв|q[1-4]|\d\s*кв|\d{{4}}(\s*г\.?|\s*год)?|"
                     rf"(i|ii|iii|iv)\s*квартал|\d\s*квартал|неделя\s*\d+|week\s*\d+)\b", re.IGNORECASE)


def split_header(name: str) -> tuple[str, str | None]:
    """«Выручка, млн руб.» → («Выручка», «млн руб.»); «Доля (%)» → («Доля», «%»)."""
    name = " ".join((name or "").split())
    m = re.match(r"^(.*?)\s*\(([^)]{1,20})\)\s*$", name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if "," in name:
        head, tail = name.rsplit(",", 1)
        tail = tail.strip()
        if 0 < len(tail) <= 20 and not re.search(r"\d{3,}", tail):
            return head.strip(), tail
    return name, None


def _numeric_share(values: list[str]) -> float:
    filled = [v for v in values if v.strip()]
    if not filled:
        return 0.0
    return sum(parse_cell(v)[0] is not None for v in filled) / len(filled)


def build_dataset(ds_id: str, rows: list[list[str]], origin: str, title: str | None = None,
                  loc: dict | None = None) -> Dataset | None:
    """Строки таблицы → Dataset; None — если чисел нет или строк меньше двух."""
    rows = [[" ".join((c or "").split()) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if width < 2:
        return None

    header_is_text = _numeric_share(rows[0][1:]) < 0.5
    header = rows[0] if header_is_text else [f"Колонка {i + 1}" for i in range(width)]
    body = rows[1:] if header_is_text else rows
    if len(body) < 1:
        return None

    columns = []
    for i in range(width):
        name, unit = split_header(header[i]) if i else (header[i] or "", None)
        values = [r[i] for r in body]
        if i == 0:
            ctype = "label"
        else:
            share = _numeric_share(values)
            pct_cells = sum(parse_cell(v)[1] for v in values)
            if share >= 0.6:
                ctype = "percent" if unit == "%" or pct_cells >= len([v for v in values if v]) / 2 else "number"
                if ctype == "percent" and unit is None:
                    unit = "%"
            else:
                ctype = "text"
        columns.append(Column(id=f"c{i + 1}", name=name or f"Колонка {i + 1}", unit=unit, type=ctype))
    if not any(c.type in ("number", "percent") for c in columns):
        return None

    out_rows = []
    for j, r in enumerate(body, start=1):
        cells = {}
        for i, raw in enumerate(r):
            if i == 0:
                continue
            value, _ = parse_cell(raw)
            cells[f"c{i + 1}"] = Cell(raw=raw, value=value)
        out_rows.append(Row(id=f"r{j}", label=r[0], is_total=bool(TOTAL_RE.match(r[0])), cells=cells))

    labels = [r.label for r in out_rows if not r.is_total]
    axis = "time" if labels and sum(bool(TIME_RE.match(x)) for x in labels) / len(labels) >= 0.6 else "category"
    sums = {}
    for c in columns:
        if c.type == "percent":
            vals = [r.cells[c.id].value for r in out_rows if not r.is_total and r.cells[c.id].value is not None]
            sums[c.id] = round(sum(vals), 4)
    return Dataset(id=ds_id, title=title, origin=origin, loc=loc or {}, columns=columns, rows=out_rows,
                   axis=axis, sums=sums)


# ── Ряды в тексте ────────────────────────────────────────────────────────────

_ITEM_RE = re.compile(r"^\s*(?P<label>[^—–:\d][^—–:]{0,60}?)\s+[—–-]\s+(?P<value>[+\-−]?\d[\d  ]*(?:[.,]\d+)?)"
                      r"\s*(?P<unit>%|[^\d,;.]{0,20})?\s*$")


def text_series(paragraph: str) -> tuple[str, list[tuple[str, str]], str | None] | None:
    """«Заголовок: подпись — число, подпись — число, …» (≥ 3 пар) →
    (заголовок, [(подпись, число как написано)], единица). Иначе None."""
    if ":" not in paragraph:
        return None
    title, _, rest = paragraph.partition(":")
    rest = rest.strip().rstrip(".")
    items = [x for x in re.split(r"[,;]\s+", rest) if x.strip()]
    if len(items) < 3:
        return None
    pairs, units = [], set()
    for item in items:
        m = _ITEM_RE.match(item)
        if not m:
            return None
        unit = (m.group("unit") or "").strip()
        pairs.append((m.group("label").strip(), m.group("value").strip() + ("%" if unit == "%" else "")))
        units.add(unit)
    unit = units.pop() if len(units) == 1 else None
    return title.strip(), pairs, (unit or None)


def series_dataset(ds_id: str, paragraph: str, loc: dict | None = None) -> Dataset | None:
    parsed = text_series(paragraph)
    if not parsed:
        return None
    title, pairs, unit = parsed
    name, header_unit = split_header(title)
    header = f"{name}, {unit}" if unit and unit != "%" else (f"{name}, %" if unit == "%" else title)
    rows = [["", header]] + [[label, value] for label, value in pairs]
    ds = build_dataset(ds_id, rows, "text_series", title=title, loc=loc)
    if ds is not None:
        ds.columns[0].name = "Подпись"
    return ds
