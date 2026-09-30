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
