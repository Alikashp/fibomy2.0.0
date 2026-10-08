"""
Таблицы слотов для docs/design/05_LAYOUTS.md, раздел 3, — из YAML layouts/ (правда — YAML, D-044).

Для каждого варианта: бокс, стиль (база → минимум), строк, знаков в строке и вместимость слотов
по формуле раздела 2 (core.models.layout.capacity). У повторяющихся элементов — первый элемент
каждой раскладки. --write подставляет таблицы в 05_LAYOUTS.md под заголовки «#### `kind.variant`».

Запуск из корня: python scripts/layout_tables.py [--write]
"""
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core.models.layout import K_BOLD, K_REGULAR, UNITS_PER_PT, DEFAULT_TYPESCALE, capacity, load_layouts, max_lines_at  # noqa: E402
from core.models.theme import BOLD_STYLES  # noqa: E402

DOC = ROOT / "docs/design/05_LAYOUTS.md"
SHOWN = {"card": "card", "icon": "icon", "badge": "badge", "image": "img", "chart": "chart", "chevron": "chevron",
         "line": "line"}


def _row(e) -> str | None:
    b = e.box
    box = f"[{b.x:g}, {b.y:g}, {b.w:g}, {b.h:g}]"
    if e.type == "rect" and e.name in ("card", "swatch", "panel", "insight_card", "body_card"):
        return f"| `{e.name}` | {box} | {'swatch' if e.name == 'swatch' else 'card'} | — | — | — |"
    if e.type in SHOWN:
        return f"| `{e.name}` | {box} | {SHOWN[e.type]} | — | — | — |"
    if e.type != "text" or (e.bind or "").startswith("const:"):
        return None
    pt = DEFAULT_TYPESCALE[e.style]
    per_line = math.floor(b.w / (pt * UNITS_PER_PT * (K_BOLD if e.style in BOLD_STYLES else K_REGULAR)))
    style = f"{e.style} {pt}" + (f" → {e.min_style} {DEFAULT_TYPESCALE[e.min_style]}" if e.min_style != e.style else "")
    low = f" (на {e.min_style}: {capacity(e, e.min_style)})" if e.min_style != e.style else ""
    return (f"| `{e.name}` | {box} | {style} | {max_lines_at(e, e.style)} | {per_line} | "
            f"**{capacity(e)}**{low} |")


def table(spec) -> str:
    lines = ["| Слот | Бокс [x, y, w, h] | Стиль (база → минимум) | Строк | Знаков в строке | Вместимость, знаков |",
             "|---|---|---|---|---|---|"]
    lines += [r for r in (_row(e) for e in spec.elements()) if r]
    for n in sorted(spec.arrangements):
        lines.append(f"| *n = {n}, элемент 1 из {n}* | | | | | |")
        lines += [r for r in (_row(e) for e in spec.item_elements(n)[0]) if r]
    return "\n".join(lines)


def main() -> None:
    specs = load_layouts()
    if "--write" not in sys.argv:
        for vid, spec in specs.items():
            print(f"#### `{vid}`\n\n{table(spec)}\n")
        return
    text = DOC.read_text(encoding="utf-8")
    for vid, spec in specs.items():
        m = re.search(rf"^#### `{re.escape(vid)}`.*$", text, re.M)
        if not m:
            print(f"нет раздела для {vid}")
            continue
        start = text.index("\n| Слот |", m.end())
        end = start + 1
        while True:
            nl = text.find("\n", end)
            if nl == -1 or not text[nl + 1:].startswith("|"):
                break
            end = nl + 1
        end = text.find("\n", end)
        text = text[:start + 1] + table(spec) + text[end:]
    DOC.write_text(text, encoding="utf-8")
    print(DOC)


if __name__ == "__main__":
    main()
