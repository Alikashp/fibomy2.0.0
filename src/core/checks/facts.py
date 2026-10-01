"""Проверка «число + ячейка» по источнику (ТЗ 3.3.3, 4.8.2; 09_PLAN.md, сессия 2).

Режим «по материалу»: каждое число на слайде должно найтись в SourceDigest —
в тексте фрагментов или в ячейках таблиц. У числа метрики с source_ref значение
должно совпасть с этой ячейкой или встречаться в этом фрагменте.

Не проверяются: числа диаграмм (их ставит код из таблицы), номера шагов и
маленькие целые без процента (0–12: «3 шага», «5 систем» — часто написаны в
источнике словами).
"""

import re

from core.ingest.numbers import find_numbers, same_number
from core.models.deck import Slide
from core.models.digest import SourceDigest

SMALL_INT = 12


def source_numbers(digest: SourceDigest) -> list[float]:
    values = []
    for f in digest.fragments:
        values += [v for v, _, _ in find_numbers(f.text)]
    for ds in digest.datasets:
        for r in ds.rows:
            values += [v for v, _, _ in find_numbers(r.label)]
            values += [c.value for c in r.cells.values() if c.value is not None]
    return values


def _cell(digest: SourceDigest, ref: str):
    m = re.match(r"^(ds\d+):(r\d+)(c\d+)$", ref or "")
    if not m:
        return None
    ds = next((d for d in digest.datasets if d.id == m.group(1)), None)
    row = next((r for r in ds.rows if r.id == m.group(2)), None) if ds else None
    return row.cells.get(m.group(3)) if row else None


def _texts(slide: Slide) -> list[tuple[str, str]]:
    """(путь, текст) всех текстов содержания слайда, кроме данных диаграмм и легенды."""
    out = [("title", slide.title)]

    def walk(value, path):
        if isinstance(value, str):
            out.append((path, value))
        elif isinstance(value, dict):
            for k, v in value.items():
                if k not in ("legend", "category_labels", "source_ref", "status", "tag", "icon"):
                    walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}.{i}")

    walk(slide.content, "content")
    return out


def _checked(value: float, pct: bool) -> bool:
    return pct or not (value == int(value) and 0 <= abs(value) <= SMALL_INT)


def slide_errors(slide: Slide, digest: SourceDigest, known: list[float] | None = None) -> list[str]:
    """Ошибки проверки чисел слайда — текстом для задания «исправь факт»."""
    known = known if known is not None else source_numbers(digest)
    errors = []
    for path, text in _texts(slide):
        for value, pct, raw in find_numbers(text):
            if not _checked(value, pct):
                continue
            if not any(same_number(value, k) for k in known):
                errors.append(f"число «{raw}» ({path}) не найдено в источнике")
    if slide.kind == "metrics":
        for i, item in enumerate(slide.content.get("items") or [], start=1):
            ref = item.get("source_ref")
            cell = _cell(digest, ref) if ref else None
            if cell is None or cell.value is None:
                continue
            values = [v for v, _, _ in find_numbers(str(item.get("value", "")))]
            if values and not any(same_number(v, cell.value) for v in values):
                errors.append(f"число {i} «{item.get('value')}» не совпадает с ячейкой {ref} («{cell.raw}»)")
    return errors


_SENT = re.compile(r"(?<=[.!?…])\s+")


def strip_unknown(slide: Slide, digest: SourceDigest, known: list[float] | None = None) -> bool:
    """Убирает числа, которых нет в источнике: предложения с ними, метрики с ними.
    True — слайд изменился. Пустой после чистки текст остаётся пустым (пункт удаляется)."""
    known = known if known is not None else source_numbers(digest)

    def bad(text: str) -> bool:
        return any(_checked(v, p) and not any(same_number(v, k) for k in known) for v, p, _ in find_numbers(text))

    changed = False

    def clean(text: str) -> str:
        nonlocal changed
        if not bad(text):
            return text
        changed = True
        return " ".join(s for s in _SENT.split(text) if not bad(s)).strip()

    c = slide.content
    if slide.kind == "metrics":
        items = [it for it in c.get("items") or [] if not bad(str(it.get("value", "")))]
        if len(items) != len(c.get("items") or []):
            changed = True
        for it in items:
            it["label"] = clean(it.get("label") or "") or it.get("label")
        c["items"] = items
        if c.get("body"):
            c["body"] = clean(c["body"]) or None
    for key in ("body", "insight"):
        if isinstance(c.get(key), str) and slide.kind != "metrics":
            c[key] = clean(c[key]) or (None if key == "body" else c[key])
    for key, field in (("items", "text"), ("steps", "text")):
        if slide.kind in ("bullets", "conclusion", "process") and isinstance(c.get(key), list):
            kept = []
            for it in c[key]:
                it[field] = clean(it.get(field) or "")
                if it[field] or "heading" in it or "label" in it:
                    kept.append(it)
                else:
                    changed = True
            c[key] = kept
    if slide.kind == "comparison":
        for side in ("left", "right"):
            points = [clean(p) for p in (c.get(side) or {}).get("points") or []]
            c[side]["points"] = [p for p in points if p]
    return changed
