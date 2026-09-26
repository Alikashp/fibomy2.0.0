"""
Загрузка промптов из папки prompts/ в корне репозитория (правило CLAUDE.md, ТЗ 4.2).

- *.txt — один промпт целиком. Последний перевод строки файла отбрасывается,
  остальное — байт в байт.
- *.yaml — словари промптов (ключ — значение enum'а: pitch_deck, short, bullets…).

Файлы читаются один раз при импорте модуля: опечатка в имени файла роняет
процесс на старте, а не посреди генерации.
Версия набора промптов — prompts/VERSION (пишется в логи генерации).
"""

import os
from enum import Enum
from pathlib import Path
from string import Template
from typing import TypeVar

import yaml

# src/generation/prompts.py → корень репозитория. В Docker-образе это /app.
PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR") or Path(__file__).resolve().parents[2] / "prompts")

E = TypeVar("E", bound=Enum)


def load_text(name: str) -> str:
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def load_template(name: str) -> Template:
    return Template(load_text(name))


def load_yaml(name: str) -> dict:
    with (PROMPTS_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_template_map(name: str) -> dict[str, Template]:
    """YAML {ключ: шаблон с $подстановками} → {ключ: Template}."""
    return {key: Template(value) for key, value in load_yaml(name).items()}


def load_enum_map(name: str, enum_cls: type[E]) -> dict[E, str]:
    """YAML {значение enum: текст} → {Enum: текст}. Неизвестный ключ — ошибка."""
    return {enum_cls(key): value for key, value in load_yaml(name).items()}


PROMPTS_VERSION = load_text("VERSION").strip()
