"""Снимок собранных промптов основного вызова по всем комбинациям запроса.

Нужен для ТЗ 4.8.7: любой PR, меняющий prompts/, показывает, какие из
собранных комбинаций изменились. Сеть и ключи не нужны.

    python tests/golden/prompt_snapshot.py <папка>                  # снять снимок
    python tests/golden/prompt_snapshot.py <папка> --root <checkout>  # снимок другой версии кода
    python tests/golden/prompt_snapshot.py --diff <до> <после> [--show] [--md <файл>]

--root — корень другой копии репозитория (например, базовой ветки PR): промпты
собираются её кодом и её prompts/, материал — фикстура из текущей копии.
--show печатает unified diff изменённых файлов, --md дописывает сводку в файл
(в CI — $GITHUB_STEP_SUMMARY).

Каждая комбинация — два файла: <имя>.system.txt и <имя>.user.txt.
Дата зафиксирована, материал — фикстура G-01 (docx) и короткий текст.
"""
import asyncio
import difflib
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _arg(name: str) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


CODE_ROOT = Path(_arg("--root") or ROOT).resolve()
sys.path.insert(0, str(CODE_ROOT / "src"))
os.environ.pop("PROMPTS_DIR", None)  # промпты — из той же копии, что и код
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")

NOW = datetime(2026, 9, 30)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SHORT_TEXT = (
    "Школа №7 подвела итоги учебного года. Средний балл вырос с 4,1 до 4,3. "
    "Кружок робототехники набрал 42 ученика. План на следующий год: ремонт спортзала."
)


def _combos(docx_text: str) -> list[tuple[str, dict]]:
    combos: list[tuple[str, dict]] = []
    base = dict(topic="Итоги учебного года", language="ru")

    for vol in ("short", "medium", "long"):
        combos.append((f"pitch_deck__topic__{vol}", dict(
            base, topic="Сервис доставки корма для собак", presentation_type="pitch_deck",
            audience="investors", content_volume=vol)))
    for ptype in ("conference", "corp_report", "diploma", "educational", "sales", "roadmap"):
        combos.append((f"{ptype}__topic__medium", dict(
            base, presentation_type=ptype, audience="general", content_volume="medium")))

    for vol in ("short", "medium", "long"):
        for aud in ("general", "students"):
            for n in (None, 12):
                combos.append((f"doklad__topic__{vol}__{aud}__n{n or 9}", dict(
                    base, presentation_type="doklad", audience=aud, content_volume=vol,
                    slide_count_hint=n)))

    for src, raw in (("document", docx_text), ("text", SHORT_TEXT)):
        for mode in ("strict", "extend", None):
            for vol in ("short", "medium", "long"):
                for n in (None, 12):
                    combos.append((f"doklad__{src}__{mode}__{vol}__n{n or 9}", dict(
                        base, topic="Итоги продаж за квартал", presentation_type="doklad",
                        audience="general", content_volume=vol, slide_count_hint=n,
                        source_type=src, raw_text=raw, source_mode=mode,
                        source_name="test_prodazhi_Q3.docx" if src == "document" else None)))
    return combos


def _build(llm, request) -> tuple[str, str]:
    if hasattr(llm, "build_prompts"):
        return llm.build_prompts(request, now=NOW)
    # Сборка до рефакторинга — копия generate_presentation_structure
    months = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
              "сентябре", "октябре", "ноябре", "декабре"]
    current_date = f"Q{(NOW.month - 1) // 3 + 1} {NOW.year} ({NOW.day} {months[NOW.month - 1]})"
    block = llm._pick_structure_block(request)
    from schemas.presentation import ContentVolume
    system = llm.SYSTEM_PROMPT.format(
        schema=llm._get_json_schema(), language=request.language, current_date=current_date,
        volume_instruction=llm.VOLUME_INSTRUCTIONS.get(request.content_volume,
                                                       llm.VOLUME_INSTRUCTIONS[ContentVolume.MEDIUM]),
        structure_block=block, competition_table_block=llm._competition_table_block(block),
        market_slide_block=llm._market_slide_block(block))
    return system, llm._build_user_prompt(request)


def snapshot(out: Path) -> None:
    from generation import llm
    from generation.content_extractor import extract_from_document
    from schemas.presentation import UserRequest

    docx = (ROOT / "tests/fixtures/test_prodazhi_Q3.docx").read_bytes()
    docx_text = asyncio.run(extract_from_document(docx, DOCX_MIME))
    out.mkdir(parents=True, exist_ok=True)
    fields = set(UserRequest.model_fields)
    for name, data in _combos(docx_text):
        request = UserRequest.model_validate({k: v for k, v in data.items() if k in fields})
        system, user = _build(llm, request)
        (out / f"{name}.system.txt").write_text(system, encoding="utf-8")
        (out / f"{name}.user.txt").write_text(user, encoding="utf-8")
    print(f"{len(_combos(docx_text))} комбинаций → {out}")


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []


def diff(before: Path, after: Path, show: bool = False, md: str | None = None,
         max_lines_per_file: int = 80) -> list[str]:
    names = sorted({p.name for p in before.iterdir()} | {p.name for p in after.iterdir()})
    changed = [n for n in names if _read(before / n) != _read(after / n)]
    print(f"Изменилось {len(changed)} из {len(names)} файлов")
    for n in changed:
        print("  ", n)

    if show:
        seen: dict[str, str] = {}
        for n in changed:
            lines = list(difflib.unified_diff(_read(before / n), _read(after / n), f"до/{n}", f"после/{n}", n=1))
            key = "".join(lines[2:])
            if key in seen:  # одинаковая правка во многих комбинациях — печатаем один раз
                print(f"\n=== {n}: тот же diff, что у {seen[key]}")
                continue
            seen[key] = n
            print(f"\n=== {n}")
            print("".join(lines[:max_lines_per_file]), end="")
            if len(lines) > max_lines_per_file:
                print(f"\n... ещё {len(lines) - max_lines_per_file} строк")

    if md:
        by_type: dict[str, list[str]] = {}
        for n in changed:
            by_type.setdefault(n.split("__")[0], []).append(n)
        rows = [f"| {t} | {len(v)} |" for t, v in sorted(by_type.items())]
        with open(md, "a", encoding="utf-8") as f:
            f.write("## Снимок собранных промптов\n\n")
            f.write(f"Изменилось **{len(changed)}** из {len(names)} файлов "
                    f"({len(names) // 2} комбинаций). Diff — в логе шага.\n\n")
            if rows:
                f.write("| Тип | Изменённых файлов |\n|---|---|\n" + "\n".join(rows) + "\n\n")
    return changed


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        diff(Path(sys.argv[2]), Path(sys.argv[3]), show="--show" in sys.argv, md=_arg("--md"))
    else:
        snapshot(Path(sys.argv[1]))
