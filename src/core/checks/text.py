"""Проверка текста слайдов после CONTENT: мусор генерации (замечание владельца, сессия 5; D-074).

Что считается ошибкой (кроме того, что есть в источнике или теме пользователя):
- script — знак вне письменностей языка колоды: иероглифы, хангыль, арабица, «�», управляющие;
- mixed — слово из букв двух алфавитов («Iн», «рынкe» с латинской e) — обрывок или подмена;
- foreign — слово чужого алфавита строчными в тексте колоды («liver», «earnings» в русском
  тексте); названия компаний, продуктов и тикеров пишутся с заглавной или капсом и не трогаются,
  как и термины, которые есть в источнике;
- glued — склейка названия, которое в источнике написано раздельно («BostonScientific» при
  «Boston Scientific» в источнике) — исправляется кодом сразу.

Порядок (core.pipeline): склейки — кодом; остальное — перегенерация слайда с перечнем ошибок
(один вызов); не помогло — фрагмент убирается (знаки — из слова, слово — из текста).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from core.text.typography import plain

CYRILLIC_LANGS = {"ru", "kk"}
LATIN_LANGS = {"en", "uz"}
# Общепринятые слова латиницей строчными, которые не считаются «английскими вставками»
ALLOWED_LATIN = {"e-mail", "email", "online", "offline", "web", "wi-fi", "wifi", "on-line", "vs", "etc", "pdf",
                 "pptx", "docx", "xlsx", "csv", "html", "url", "http", "https", "www"}
_WORD_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['’ʻ\-])*", re.UNICODE)
_CAMEL_RE = re.compile(r"\b(?:[A-Z][a-z]{2,}){2,}\b")
_ALLOWED_PUNCT = set("%‰₽$€£¥₸№°×·•–—-‐‑«»„“”‘’'\"()[]{}/\\+=<>≈≤≥~^*&@#!?.,;:…|±§©®™_")  # «_» — в именах продуктов и файлов (sechenov.ai_nephro)


def _script(ch: str) -> str:
    """Письменность буквы: latin, cyrillic, greek или other."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "other"
    for script in ("LATIN", "CYRILLIC", "GREEK"):
        if name.startswith(script) or f" {script} " in name:
            return script.lower()
    return "other"


def _char_ok(ch: str) -> bool:
    if ch.isspace() or ch in "⁠  ":
        return True
    if ch == "�":
        return False
    cat = unicodedata.category(ch)
    if cat.startswith("C"):
        return False                       # управляющие, частные, несуществующие
    if cat.startswith("N"):
        return ch.isascii() or cat == "No" or unicodedata.name(ch, "").startswith(("SUPERSCRIPT", "SUBSCRIPT"))
    if cat.startswith("L") or cat.startswith("M"):
        return _script(ch) in ("latin", "cyrillic", "greek")
    return ch in _ALLOWED_PUNCT or cat in ("Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sm", "Sc", "So", "Sk")


@dataclass
class Known:
    """Текст источника и темы пользователя: что из него на слайде — не ошибка."""
    text: str = ""
    words: set[str] = field(default_factory=set)
    chars: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, *texts: str) -> "Known":
        joined = plain("\n".join(t for t in texts if t))
        return cls(text=joined.lower(), words={w.lower() for w in _WORD_RE.findall(joined)}, chars=set(joined))


@dataclass
class Problem:
    code: str           # script | mixed | foreign | glued
    fragment: str
    fix: str | None = None      # замена (glued) — применяется кодом без перегенерации

    def message(self) -> str:
        return {
            "script": f"знак «{self.fragment}» не из алфавита языка колоды — убери",
            "mixed": f"слово «{self.fragment}» смешивает латиницу и кириллицу — напиши одним алфавитом",
            "foreign": f"слово «{self.fragment}» на чужом языке — переведи на язык колоды",
            "glued": f"склейка «{self.fragment}» — пиши раздельно: «{self.fix}»",
        }[self.code]


def problems(text: str, language: str, known: Known) -> list[Problem]:
    if not text:
        return []
    text = plain(text)
    out: list[Problem] = []
    bad = sorted({ch for ch in text if not _char_ok(ch) and ch not in known.chars})
    for ch in bad:
        out.append(Problem("script", ch))
    for m in _CAMEL_RE.finditer(text):
        word = m.group(0)
        parts = re.findall(r"[A-Z][a-z]+", word)
        spaced = " ".join(parts)
        if word.lower() not in known.words and spaced.lower() in known.text:
            out.append(Problem("glued", word, spaced))
    native = "cyrillic" if language in CYRILLIC_LANGS else "latin" if language in LATIN_LANGS else None
    for m in _WORD_RE.finditer(text):
        word = m.group(0).strip("-'’ʻ")
        letters = [c for c in word if c.isalpha()]
        if not letters:
            continue
        low = word.lower()
        if low in known.words or low in ALLOWED_LATIN:
            continue
        # Части через дефис — отдельно: «AI-навигация», «IT-компания» — нормальные слова
        parts = [p for p in re.split(r"-", word) if any(c.isalpha() for c in p)]
        mixed = [p for p in parts if {"latin", "cyrillic"} <= {_script(c) for c in p if c.isalpha()}]
        if mixed:
            out += [Problem("mixed", p) for p in mixed if p.lower() not in known.words]
            continue
        scripts = {_script(c) for c in letters} - {"greek"}
        if native and scripts and native not in scripts and scripts <= {"latin", "cyrillic"}:
            # чужой алфавит: строчными (не имя, не тикер, не аббревиатура) и длиннее двух букв
            if word == low and len(letters) >= 3:
                out.append(Problem("foreign", word))
    return out


def fix_glued(text: str, found: list[Problem]) -> str:
    for p in found:
        if p.code == "glued" and p.fix:
            text = text.replace(p.fragment, p.fix)
    return text


_SPACES_RE = re.compile(r"[ \t]{2,}")
_SPACE_PUNCT_RE = re.compile(r"\s+([,.;:!?)»])")


def remove_fragments(text: str, found: list[Problem]) -> str:
    """Последний шаг: чужие знаки — из текста, чужие и смешанные слова — целиком."""
    for p in found:
        if p.code == "script":
            text = text.replace(p.fragment, "")
        elif p.code in ("mixed", "foreign"):
            text = re.sub(r"(?<![\w])" + re.escape(p.fragment) + r"(?![\w])", "", text)
        elif p.code == "glued" and p.fix:
            text = text.replace(p.fragment, p.fix)
    text = _SPACES_RE.sub(" ", text)
    text = _SPACE_PUNCT_RE.sub(r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip(" ,;:—–-")


SKIP_KEYS = {"icon", "status", "tag", "source_ref", "polarity", "row", "legend"}


def walk_texts(content, path: str = "content"):
    """(путь, текст) всех строк содержания слайда, кроме служебных полей."""
    if isinstance(content, str):
        yield path, content
    elif isinstance(content, dict):
        for k, v in content.items():
            if k not in SKIP_KEYS:
                yield from walk_texts(v, f"{path}.{k}")
    elif isinstance(content, list):
        for i, v in enumerate(content):
            yield from walk_texts(v, f"{path}.{i}")


def map_texts(content, fn, path: str = "content"):
    """Копия content, где каждая строка заменена fn(путь, строка)."""
    if isinstance(content, str):
        return fn(path, content)
    if isinstance(content, dict):
        return {k: (v if k in SKIP_KEYS else map_texts(v, fn, f"{path}.{k}")) for k, v in content.items()}
    if isinstance(content, list):
        return [map_texts(v, fn, f"{path}.{i}") for i, v in enumerate(content)]
    return content


def slide_problems(title: str, content: dict, language: str, known: Known) -> dict[str, list[Problem]]:
    """{путь: ошибки} по заголовку и всем текстам содержания."""
    out = {}
    for path, text in [("title", title), *walk_texts(content)]:
        found = problems(text, language, known)
        if found:
            out[path] = found
    return out
