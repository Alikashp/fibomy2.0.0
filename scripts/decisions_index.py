"""Индекс решений в начале docs/DECISIONS.md — генерируется из заголовков, руками не правится (D-080).

Для каждой записи «### D-NNN — название» берутся номер, название, статус (строка «**Статус:**» до первого
«;», «: » или «. ») и дата раздела «## ГГГГ-ММ-ДД — …», в котором запись стоит. Записи без номера
(«Восстановленные решения») — в конце. Повтор номера без строки статуса (дополнение к решению) в индекс
не попадает.

    python scripts/decisions_index.py           # переписать индекс между маркерами
    python scripts/decisions_index.py --check   # код 1, если индекс устарел (то же проверяет check_docs)
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "DECISIONS.md"
START, END = "<!-- decisions-index:start -->", "<!-- decisions-index:end -->"

_SECTION = re.compile(r"^## (\d{4}-\d{2}(?:-\d{2})?) — ")
_ENTRY = re.compile(r"^### (?:(D-\d{3})|(\d{4}-\d{2})) — (.+)$")
_STATUS = re.compile(r"^\*\*Статус:\*\*\s*(.+)$")


def _short_status(text: str) -> str:
    cut = re.split(r";|: |\. ", text, maxsplit=1)[0].strip().rstrip(".")
    if cut.count("(") > cut.count(")"):
        cut = cut[: cut.rfind("(")].rstrip()        # пояснение в скобках разрезано — без него
    return cut if len(cut) <= 70 else cut[:69].rstrip() + "…"


def entries(text: str) -> list[dict]:
    body = text.split(END, 1)[-1] if END in text else text
    found, current, section = [], None, ""
    for line in body.splitlines():
        if line.startswith("## "):
            m = _SECTION.match(line)
            section = m.group(1) if m else ""
        elif m := _ENTRY.match(line):
            current = {"id": m.group(1) or "—", "title": m.group(3).strip(), "status": "",
                       "date": section or m.group(2) or ""}
            found.append(current)
        elif current is not None and (m := _STATUS.match(line)) and not current["status"]:
            current["status"] = _short_status(m.group(1))
    by_id: dict[str, dict] = {}
    result = []
    for e in found:
        if e["id"] == "—":
            result.append(e)
            continue
        kept = by_id.get(e["id"])
        if kept is None:
            by_id[e["id"]] = e
        elif not kept["status"] and e["status"]:
            by_id[e["id"]] = e
    numbered = sorted(by_id.values(), key=lambda e: int(e["id"][2:]), reverse=True)
    return numbered + result


def render(text: str) -> str:
    rows = ["| № | Решение | Статус | Раздел |", "|---|---|---|---|"]
    for e in entries(text):
        title = e["title"].replace("|", "\\|")
        rows.append(f"| {e['id']} | {title} | {e['status'] or '—'} | {e['date']} |")
    note = ("Сгенерировано `python scripts/decisions_index.py` из заголовков ниже — руками не править. "
            "Полный текст решения — поиском по номеру.")
    return "\n".join([START, "", note, "", *rows, "", END])


def updated(text: str) -> str:
    block = render(text)
    if START in text and END in text:
        head, rest = text.split(START, 1)
        return head + block + rest.split(END, 1)[1]
    anchor = "## Шаблон записи"
    return text.replace(anchor, "## Индекс решений\n\n" + block + "\n\n" + anchor, 1)


def is_fresh() -> bool:
    text = DOC.read_text(encoding="utf-8")
    return updated(text) == text


if __name__ == "__main__":
    if "--check" in sys.argv:
        fresh = is_fresh()
        print("Индекс решений актуален" if fresh else "Индекс решений устарел: python scripts/decisions_index.py")
        sys.exit(0 if fresh else 1)
    DOC.write_text(updated(DOC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"Индекс решений: {len(entries(DOC.read_text(encoding='utf-8')))} записей")
