"""Проверка документации: то, что можно проверить кодом, не должно устаревать молча (D-080).

1. Пути к файлам репозитория в документах, которые описывают код как он есть (CHECKED_DOCS), существуют.
   Путь — текст в `обратных кавычках` или ссылка [текст](путь) с «/»;
   ищется от корня репозитория, от src/ и от src/core/. Шаблоны (<kind>, {id}, *, …) и адреса
   API (/v1/…) не проверяются. «модуль.функция» (`bot/delivery.send_files`) — проверяется модуль.
2. Каждый роут REST API из снимка контракта (tests/unit/api_contract_v1.json) упомянут в docs/API.md.
3. Каждый документ docs/*.md и docs/design/*.md есть в своей карте: в карте документации CLAUDE.md
   и в таблице docs/design/README.md.
4. Документы читаются как UTF-8.

    python scripts/check_docs.py        # код 1 — есть ошибки, список в выводе

Запускается юнит-тестом tests/unit/test_docs.py, то есть в каждом PR (workflow «tests»).
Документы-проекты (docs/design/01…10) по пунктам 1 не проверяются: они описывают и то, что
ещё не сделано, — статус каждого указан в docs/design/README.md.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Документы «как есть»: каждый путь в них обязан существовать
# (docs/design/README.md не входит: в нём намеренно названы задуманные, но не созданные файлы)
CHECKED_DOCS = ["README.md", "CLAUDE.md", "docs/API.md"]
SEARCH_ROOTS = [ROOT, ROOT / "src", ROOT / "src" / "core"]
EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".ini", ".png", ".docx", ".pdf")
# Каталоги кода и данных: путь без расширения (`core/ingest/`, `bot/delivery.send_files`) проверяется,
# только если начинается с одного из них
CODE_DIRS = {"src", "core", "api", "bot", "db", "generation", "schemas", "tests", "scripts", "prompts", "layouts",
             "themes", "fonts", "assets", "spikes", ".github", "ingest", "planning", "selection", "content",
             "images", "fitting", "checks", "render", "llm", "models", "storage"}
# Пути, которые создаются при запуске и в репозитории не лежат
GENERATED = {".env", "out/gallery", "out/golden", "gallery_out", "probe_out"}

_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_TEMPLATE = re.compile(r"[<>{}*…|$]|\s")


def _candidates(token: str) -> list[str]:
    token = token.strip().rstrip(",.;:")
    if token.startswith(("/", "http://", "https://", "#", "-", "~")) or "://" in token:
        return []
    token = token.split("#", 1)[0].split(":", 1)[0]
    if not token or _TEMPLATE.search(token) or token in GENERATED:
        return []
    if "/" not in token:
        return []          # голое имя файла (`title.py` рядом с полным путём) — сокращение, не ссылка
    if not token.endswith(EXTENSIONS) and token.split("/", 1)[0] not in CODE_DIRS:
        return []          # без расширения проверяются только пути кода: не ветки (`fix/x`) и не MIME (`image/png`)
    variants = [token.rstrip("/")]
    tail = token.rsplit("/", 1)[-1]
    if "." in tail and not token.endswith(EXTENSIONS):
        variants.append(token[: token.rfind(".")] + ".py")      # модуль.функция → модуль.py
    if not token.endswith(EXTENSIONS):
        variants.append(token.rstrip("/") + ".py")
    return variants


def _exists(variants: list[str], doc: Path) -> bool:
    roots = [doc.parent, *SEARCH_ROOTS]
    return any((root / v).exists() for root in roots for v in variants)


def check_paths(errors: list[str]) -> None:
    for name in CHECKED_DOCS:
        doc = ROOT / name
        text = doc.read_text(encoding="utf-8")
        tokens = _CODE.findall(text) + _LINK.findall(text)
        for token in tokens:
            variants = _candidates(token)
            if variants and not _exists(variants, doc):
                errors.append(f"{name}: путь `{token}` не найден в репозитории")


def _route_regex(path: str) -> re.Pattern:
    parts = [r"[^/\s`\"]+" if p.startswith("{") else re.escape(p) for p in path.strip("/").split("/")]
    return re.compile(r"(?<![\w/])/" + "/".join(parts) + r"(?![\w/])")


def check_api_routes(errors: list[str]) -> None:
    contract = json.loads((ROOT / "tests/unit/api_contract_v1.json").read_text(encoding="utf-8"))
    api_doc = (ROOT / "docs/API.md").read_text(encoding="utf-8")
    for path in contract["paths"]:
        if not _route_regex(path).search(api_doc):
            errors.append(f"docs/API.md: роут {path} из снимка контракта не описан")


def check_maps(errors: list[str]) -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for doc in sorted((ROOT / "docs").glob("*.md")):
        if f"docs/{doc.name}" not in claude:
            errors.append(f"CLAUDE.md: docs/{doc.name} нет в карте документации")
    design_map = (ROOT / "docs/design/README.md").read_text(encoding="utf-8")
    for doc in sorted((ROOT / "docs/design").glob("*.md")):
        if doc.name != "README.md" and f"{doc.name}" not in design_map:
            errors.append(f"docs/design/README.md: {doc.name} нет в таблице документов")


def check_encoding(errors: list[str]) -> None:
    files = [ROOT / "README.md", ROOT / "CLAUDE.md", *ROOT.glob("docs/**/*.md")]
    for f in files:
        try:
            f.read_bytes().decode("utf-8")
        except UnicodeDecodeError as e:
            errors.append(f"{f.relative_to(ROOT)}: не UTF-8 ({e})")


def run() -> list[str]:
    errors: list[str] = []
    check_encoding(errors)
    check_paths(errors)
    check_api_routes(errors)
    check_maps(errors)
    return errors


if __name__ == "__main__":
    found = run()
    for line in found:
        print(line)
    print(f"Проверка документации: {'ошибок ' + str(len(found)) if found else 'всё в порядке'}")
    sys.exit(1 if found else 0)
