"""Загрузка промптов prompts/v2/ (06_PROMPTS.md, 2).

.txt — цельный текст (последний перевод строки отбрасывается), .yaml — словарь
блоков. Подстановки — $имя (string.Template). Файлы читаются при первом
обращении и кэшируются; версия набора — prompts/v2/VERSION.
"""

from functools import lru_cache
from string import Template

import yaml

from core.paths import PROMPTS_V2_DIR

LANGUAGE_NAMES = {"ru": "русский", "en": "английский (English)", "uz": "узбекский (oʻzbekcha, латиница)",
                  "kk": "казахский (қазақша)"}


@lru_cache(maxsize=None)
def text(name: str) -> str:
    raw = (PROMPTS_V2_DIR / name).read_text(encoding="utf-8")
    return raw[:-1] if raw.endswith("\n") else raw


@lru_cache(maxsize=None)
def data(name: str) -> dict:
    with (PROMPTS_V2_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def render(name: str, **values) -> str:
    """Шаблон name с подстановками. Неизвестная $-переменная — ошибка (опечатка в промпте)."""
    return Template(text(name)).substitute(**values)


def render_value(template: str, **values) -> str:
    """Подстановка в строку-блок из YAML."""
    return Template(template).substitute(**values)


def version() -> str:
    return text("VERSION").strip()


def common_blocks(*, mode: str, source_mode: str | None, language: str, audience: str) -> dict:
    """Общие блоки OUTLINE и CONTENT: роль, язык, безопасность входа, данные, аудитория."""
    blocks = data("common/data_blocks.yaml")
    if mode == "topic":
        role, data_block = blocks["role_topic"], blocks["data_topic"]
    else:
        role = blocks["role_material"]
        data_block = blocks["data_extend" if source_mode == "extend" else "data_strict"]
    return {
        "role": role,
        "language": render("common/language.txt", language_name=LANGUAGE_NAMES.get(language, language)),
        "input_safety": text("common/input_safety.txt"),
        "data_block": data_block,
        "audience": data("common/audience.yaml").get(audience, ""),
    }
