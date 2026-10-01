"""Общее для юнит-тестов: путь к src/, фиктивные ключи, подменный клиент LLM."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test")
os.environ.setdefault("OPENAI_API_KEY", "x")

FIXTURES = ROOT / "tests" / "fixtures"


class FakeLLM:
    """Подменяет llm.client: отвечает функцией responder(system_prompt) -> dict
    и запоминает все промпты."""

    def __init__(self, responder):
        self.responder = responder
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        content = json.dumps(self.responder(prompt), ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])


def deck(slides: list[dict], ptype: str = "doklad") -> dict:
    """Колода: титул + slides + (заполнители до минимума схемы) + closing.
    Индексы проставит схема; slides[0] всегда слайд №2."""
    body = [{"index": 0, "layout": "title", "title": "Титул"}] + slides
    while len(body) < 4:
        body.append({"index": 0, "layout": "quote", "title": "Заполнитель", "body_text": "Тезис без чисел."})
    body.append({"index": 0, "layout": "closing", "title": "Спасибо за внимание"})
    return {"meta": {"title": "Т", "presentation_type": ptype, "audience": "general"}, "slides": body}


# ── Новый движок (src/core) ─────────────────────────────────────────────────

def fake_outline(n: int = 7, subtitle: str = "Как свет превращается в сахар", asks=None, kinds=None) -> dict:
    """Ответ OUTLINE: n содержательных слайдов, последний — выводы."""
    kinds = kinds or (["statement"] + ["bullets"] * (n - 2) + ["conclusion"])
    slides = []
    for i, kind in enumerate(kinds[:n]):
        slides.append({
            "kind": kind, "role": "conclusion" if kind == "conclusion" else "details",
            "title": f"Заголовок-вывод «{'абвгдежзиклмнопрс'[i]}» о теме доклада",
            "key_message": f"Мысль слайда {i + 1}",
            "items_planned": {"bullets": 4, "conclusion": 3}.get(kind), "refs": [], "dataset": None,
            "image_query": None,
        })
    return {"analysis": {"genre": "topic", "theses": [], "asks": asks or [], "missing": []},
            "deck": {"subtitle": subtitle, "slides": slides}}


def fake_content(schema_name: str, user: str = "") -> dict:
    """Ответ CONTENT по имени схемы slide_<kind>."""
    kind = schema_name.split("_", 1)[1]
    if kind == "statement":
        return {"body": "Пояснение к утверждению: одно предложение с механизмом и примером."}
    if kind == "bullets":
        return {"items": [{"heading": f"Пункт {i}", "text": "Короткое предложение с фактом о 12 листьях.",
                           "icon": "bulb"} for i in range(1, 5)]}
    if kind == "conclusion":
        return {"items": [{"text": f"Вывод {i}: что следует из фактов колоды."} for i in range(1, 4)]}
    raise ValueError(schema_name)


class FakeOpenAI:
    """Подменяет AsyncOpenAI для core.llm.client.LLMClient: отвечает по имени схемы
    (response_format json_schema) или по маркеру в системном промпте (json_object).

    outline — dict или callable(params) -> dict; reject_json_schema — 400 на json_schema,
    как провайдер без structured outputs."""

    def __init__(self, outline=None, content=fake_content, reject_json_schema=False, usage=None):
        self.outline = outline if outline is not None else fake_outline()
        self.content = content
        self.reject_json_schema = reject_json_schema
        self.usage = usage or {"prompt_tokens": 1000, "completion_tokens": 200,
                               "prompt_tokens_details": {"cached_tokens": 500}}
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **params):
        self.calls.append(params)
        fmt = params.get("response_format", {})
        if fmt.get("type") == "json_schema" and self.reject_json_schema:
            import httpx
            from openai import BadRequestError
            req = httpx.Request("POST", "https://example.test/v1/chat/completions")
            raise BadRequestError("Invalid parameter: response_format json_schema is not supported",
                                  response=httpx.Response(400, request=req), body=None)
        if fmt.get("type") == "json_schema":
            name = fmt["json_schema"]["name"]
        else:
            system = params["messages"][0]["content"]
            name = "outline" if "план презентации" in system else "slide_" + self._kind_from_task(params)
        user = params["messages"][1]["content"]
        if name == "outline":
            data = self.outline(params) if callable(self.outline) else self.outline
        else:
            data = self.content(name, user)
        content = json.dumps(data, ensure_ascii=False)
        return SimpleNamespace(
            model=params["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
            usage=SimpleNamespace(**{k: (SimpleNamespace(**v) if isinstance(v, dict) else v)
                                     for k, v in self.usage.items()}),
        )

    @staticmethod
    def _kind_from_task(params) -> str:
        import re
        return re.search(r"kind=(\w+)", params["messages"][1]["content"]).group(1)
