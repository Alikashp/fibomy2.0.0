"""Лимиты частоты OpenAI (D-078): 429 — ожидание по retry-after и повтор, колода не падает; insufficient_quota —
без повторов; общий семафор LLM_MAX_CONCURRENCY ограничивает одновременные вызовы процесса."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи

import httpx
from openai import RateLimitError
from pydantic import BaseModel

from config import settings
from core.llm import client as llm_client
from core.llm.client import LLMClient, LLMError, retry_after_seconds


class Answer(BaseModel):
    ok: bool


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


def rate_limit(headers=None, code="rate_limit_exceeded") -> RateLimitError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(429, request=req, headers=headers or {})
    return RateLimitError(f"Rate limit reached ({code})", response=resp,
                          body={"code": code, "message": "rate limit"})


def ok_response():
    return SimpleNamespace(model="gpt-6-luna", usage=None, choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({"ok": True})), finish_reason="stop")])


class FakeCompletions:
    def __init__(self, errors=(), delay=0.0):
        self.errors = list(errors)
        self.calls = 0
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **params):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.errors:
                raise self.errors.pop(0)
            return ok_response()
        finally:
            self.active -= 1


def call(fake, attempts=1):
    return LLMClient(fake).structured(stage="content", system="s", user="u", schema_name="answer", schema=SCHEMA,
                                      model_cls=Answer, visible_tokens=100, timeout=5, attempts=attempts)


class RateLimit(unittest.TestCase):

    def setUp(self):
        self._model, settings.openai_model = settings.openai_model, "gpt-6-luna"
        llm_client._semaphore = None

    def tearDown(self):
        settings.openai_model = self._model
        llm_client._semaphore = None

    def test_waits_by_retry_after_and_does_not_burn_attempts(self):
        fake = FakeCompletions([rate_limit({"retry-after": "2"}), rate_limit({"retry-after-ms": "350"})])
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)
        with mock.patch.object(llm_client.asyncio, "sleep", fake_sleep):
            result = asyncio.run(call(fake, attempts=1))        # одна попытка модели — 429 её не тратят
        self.assertTrue(result.ok)
        self.assertEqual(fake.calls, 3)
        self.assertEqual(sleeps, [2.0, 0.35])

    def test_reset_headers_and_exponential_backoff(self):
        self.assertEqual(retry_after_seconds(rate_limit({"x-ratelimit-reset-tokens": "1m30s",
                                                         "x-ratelimit-reset-requests": "6s"}), 0), 30.0)
        self.assertEqual(retry_after_seconds(rate_limit({"x-ratelimit-reset-requests": "6s"}), 0), 6.0)
        self.assertEqual(retry_after_seconds(rate_limit({"x-ratelimit-reset-requests": "20ms"}), 0), 0.2)
        no_header = [retry_after_seconds(rate_limit(), n) for n in range(4)]
        for n, wait in enumerate(no_header):
            self.assertGreaterEqual(wait, 2 ** n)
            self.assertLessEqual(wait, 2 ** n + 0.5)

    def test_gives_up_after_limit_with_clear_error(self):
        fake = FakeCompletions([rate_limit({"retry-after": "1"}) for _ in range(10)])

        async def fake_sleep(s):
            return None
        with mock.patch.object(llm_client.asyncio, "sleep", fake_sleep):
            with self.assertRaisesRegex(LLMError, "API 429"):
                asyncio.run(call(fake, attempts=1))
        self.assertEqual(fake.calls, llm_client.RATE_LIMIT_RETRIES + 1)

    def test_insufficient_quota_is_not_retried(self):
        fake = FakeCompletions([rate_limit(code="insufficient_quota")])
        with self.assertRaisesRegex(LLMError, "429"):
            asyncio.run(call(fake, attempts=1))
        self.assertEqual(fake.calls, 1)

    def test_shared_semaphore_limits_concurrency(self):
        fake = FakeCompletions(delay=0.05)

        async def many():
            return await asyncio.gather(*(call(fake) for _ in range(20)))
        with mock.patch.object(settings, "llm_max_concurrency", 3):
            llm_client._semaphore = None
            results = asyncio.run(many())
        self.assertEqual(len(results), 20)
        self.assertEqual(fake.peak, 3)


if __name__ == "__main__":
    unittest.main()
