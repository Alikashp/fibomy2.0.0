"""SourceDigest → текст для промптов OUTLINE и CONTENT (ТЗ 4.8.2, 6.5).

Фрагменты — с id ([f12]), таблицы и ряды — наборами данных с адресами строк и
колонок ([ds2], r1…, c1…), чтобы модель ссылалась на ячейки (ds2:r1c3), а
код брал значения диаграмм из таблицы, а не из ответа модели. Материал —
внутри <source>…</source>: это данные, а не инструкции.
"""

from core.models.digest import Dataset, SourceDigest


def dataset_text(ds: Dataset) -> str:
    def head(c):
        return f"{c.id} {c.name}" + (f", {c.unit}" if c.unit else "")

    lines = [f"[{ds.id}] {ds.title or 'Таблица'}" + (" (ось — время)" if ds.axis == "time" else ""),
             "колонки: " + " | ".join(head(c) for c in ds.columns)]
    for r in ds.rows:
        cells = [r.cells[c.id].raw if c.id in r.cells else "" for c in ds.columns[1:]]
        mark = " (итого)" if r.is_total else ""
        lines.append(f"{r.id}{mark}: {r.label} | " + " | ".join(cells))
    for cid, total in ds.sums.items():
        if 90 <= total <= 110:  # похоже на доли целого — подсказка для chart_share
            lines.append(f"сумма {cid} без итога: {total:g} (доли целого)")
    return "\n".join(lines)


def digest_text(digest: SourceDigest) -> str:
    if digest.mode == "topic":
        return f"Материала нет — доклад по теме: <topic>{digest.topic}</topic>"
    src = digest.source
    head = []
    if src and src.name:
        head.append(f"Файл: {src.name}")
    if src and src.truncated:
        head.append(f"Вошло начало документа: {src.chars_used} из {src.chars_total} знаков.")
    parts = ["<source>", *head, "ТЕКСТ (фрагменты с id):"]
    parts += [f"[{f.id}] {f.text}" for f in digest.fragments]
    if digest.datasets:
        parts += ["", "ТАБЛИЦЫ И РЯДЫ ЧИСЕЛ (адрес ячейки — ds2:r1c3):"]
        for ds in digest.datasets:
            parts += [dataset_text(ds), ""]
    parts.append("</source>")
    return "\n".join(parts).strip()
