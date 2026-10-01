"""Данные диаграмм из SourceDigest.datasets по ссылке плана (D-028; 04_CONTRACTS.md, 4.1, 5).

Числа в диаграмму ставит код: модель указывает только набор данных, колонку
подписей, 1–3 колонки значений и строки. Строка «Итого» в ряд не входит,
категории — в порядке источника. Доли (chart_share): колонка-процент с суммой
95–105% или положительные значения, из которых долю считает код.
"""

from core.ingest.numbers import format_number
from core.models.digest import Dataset, SourceDigest
from core.models.outline import PlannedSlide

SHARE_MIN, SHARE_MAX = 95.0, 105.0


class DatasetError(ValueError):
    pass


def _decimals(ds: Dataset, columns: list[str], rows) -> int:
    best = 0
    for r in rows:
        for c in columns:
            raw = r.cells.get(c).raw if r.cells.get(c) else ""
            if "," in raw or "." in raw:
                frac = raw.replace("%", "").strip().replace(".", ",").rsplit(",", 1)[-1]
                if frac.isdigit():
                    best = max(best, len(frac))
    return best


def resolve(planned: PlannedSlide, digest: SourceDigest, kind: str, language: str = "ru") -> dict:
    """PlannedSlide.dataset → снимок Slide.data. DatasetError — ссылка неверна или данные не годятся."""
    ref = planned.dataset
    if ref is None:
        raise DatasetError("у слайда с диаграммой нет dataset")
    ds = next((d for d in digest.datasets if d.id == ref.id), None)
    if ds is None:
        raise DatasetError(f"набора данных {ref.id} нет в источнике")
    cols = {c.id: c for c in ds.columns}
    numeric = [c.id for c in ds.columns if c.type in ("number", "percent")]
    value_cols = [c for c in (ref.value_columns or []) if c in numeric][:3] or numeric[:1]
    if not value_cols:
        raise DatasetError(f"в {ds.id} нет числовых колонок")
    if kind == "chart_share":
        value_cols = value_cols[:1]
    by_id = {r.id: r for r in ds.rows}
    rows = [by_id[r] for r in (ref.rows or []) if r in by_id] if ref.rows else []
    rows = [r for r in (rows or ds.rows) if not r.is_total]
    if len(rows) < 2:
        raise DatasetError(f"в {ds.id} меньше двух строк для диаграммы")

    series = []
    for cid in value_cols:
        values = [r.cells[cid].value if cid in r.cells else None for r in rows]
        if all(v is None for v in values):
            continue
        col = cols[cid]
        series.append({"column": cid, "name": col.name, "unit": col.unit, "values": values})
    if not series:
        raise DatasetError(f"в {ds.id} нет чисел в выбранных колонках")

    data = {"dataset_id": ds.id, "categories": [{"row": r.id, "label": r.label} for r in rows],
            "series": series, "axis": ds.axis, "decimals": _decimals(ds, value_cols, rows),
            "chart": "donut" if kind == "chart_share" else "column"}
    if kind == "chart_share":
        values = series[0]["values"]
        if any(v is None or v < 0 for v in values):
            raise DatasetError("доли: есть пустые или отрицательные значения")
        total = sum(values)
        if cols[series[0]["column"]].type == "percent":
            if not SHARE_MIN <= total <= SHARE_MAX:
                raise DatasetError(f"доли: сумма {total:g}% вне 95–105%")
            data["share_sum"] = total
        else:
            data["share_from_values"] = True
            data["source_values"] = values
            series[0]["values"] = [round(v / total * 100, 1) for v in values]
            series[0]["unit"] = "%"
            data["decimals"] = 1 if any(v != int(v) for v in series[0]["values"]) else 0
            data["share_sum"] = 100.0
    return data


def share_sum(planned: PlannedSlide, digest: SourceDigest) -> float | None:
    try:
        return resolve(planned, digest, "chart_share").get("share_sum")
    except DatasetError:
        return None


def legend(data: dict, language: str = "ru") -> list[dict]:
    """Строки легенды кольца: подпись категории и значение с «%»."""
    values = data["series"][0]["values"]
    return [{"label": c["label"], "value": format_number(v, language) + "%"}
            for c, v in zip(data["categories"], values)]
