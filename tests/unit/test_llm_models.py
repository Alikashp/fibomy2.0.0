"""Параметры запроса под модель (gpt-6-luna и старые модели), учёт токенов и стоимости."""
import asyncio
import json
import unittest

import httpx
from _helpers import deck

from config import settings
from generation import llm, llm_models
from schemas.presentation import UserRequest


class Params(unittest.TestCase):

    def setUp(self):
        self._model, self._effort = settings.openai_model, settings.openai_reasoning_effort

    def tearDown(self):
        settings.openai_model, settings.openai_reasoning_effort = self._model, self._effort

    def _params(self, model, effort="low", visible=8000):
        settings.openai_model, settings.openai_reasoning_effort = model, effort
        return llm_models.completion_params(visible)

    def test_gpt_6_luna(self):
        p = self._params("gpt-6-luna")
        self.assertNotIn("max_tokens", p)
        self.assertNotIn("temperature", p)          # при effort != none — 400
        self.assertEqual(p["response_format"], {"type": "json_object"})
        self.assertEqual(p["extra_body"], {"max_completion_tokens": 16000, "reasoning_effort": "low"})

    def test_gpt_6_luna_snapshot_name(self):
        p = self._params("gpt-6-luna-2026-09-22")
        self.assertEqual(p["extra_body"]["reasoning_effort"], "low")

    def test_luna_effort_none_allows_temperature_and_no_reserve(self):
        p = self._params("gpt-6-luna", effort="none", visible=800)
        self.assertEqual(p["temperature"], 0.7)
        self.assertEqual(p["extra_body"], {"max_completion_tokens": 800, "reasoning_effort": "none"})

    def test_reserve_grows_with_effort_and_is_capped(self):
        self.assertEqual(self._params("gpt-6-luna", "high", 100)["extra_body"]["max_completion_tokens"], 32100)
        self.assertEqual(self._params("gpt-6-luna", "max", 16000)["extra_body"]["max_completion_tokens"], 80000)
        self.assertEqual(self._params("gpt-6-luna", "max", 100000)["extra_body"]["max_completion_tokens"], 128000)

    def test_unknown_effort_is_not_sent(self):
        p = self._params("gpt-6-luna", effort="turbo")
        self.assertNotIn("reasoning_effort", p["extra_body"])
        self.assertEqual(p["extra_body"]["max_completion_tokens"], 8000 + 16000)  # запас как у default

    def test_gpt_4o_mini_unchanged(self):
        p = self._params("gpt-4o-mini")
        self.assertEqual(p, {"model": "gpt-4o-mini", "max_tokens": 8000, "temperature": 0.7,
                             "response_format": {"type": "json_object"}})

    def test_other_reasoning_family_has_no_temperature(self):
        p = self._params("gpt-6-sol")
        self.assertNotIn("temperature", p)
        self.assertIn("max_completion_tokens", p["extra_body"])


class RequestBody(unittest.TestCase):
    """Что реально уходит в HTTP: openai==1.35 кладёт extra_body в тело запроса."""

    def setUp(self):
        self._model, self._client = settings.openai_model, llm.client
        self.bodies = []

    def tearDown(self):
        settings.openai_model, llm.client = self._model, self._client

    def _handler(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        content = json.dumps(deck([{"index": 0, "layout": "bullets", "title": "Т",
                                    "bullets": [{"text": "а"}, {"text": "б"}]}]), ensure_ascii=False)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 5000, "completion_tokens": 3000, "total_tokens": 8000,
                      "prompt_tokens_details": {"cached_tokens": 1000},
                      "completion_tokens_details": {"reasoning_tokens": 1200}},
        })

    def _generate(self, model):
        from openai import AsyncOpenAI
        settings.openai_model = model
        llm.client = AsyncOpenAI(api_key="x", base_url="https://example.test/v1",
                                 http_client=httpx.AsyncClient(transport=httpx.MockTransport(self._handler)))
        request = UserRequest(topic="Итоги года", presentation_type="doklad", audience="general")

        async def run():
            with llm_models.track_usage() as usage:
                await llm.generate_presentation_structure(request)
            return usage
        return asyncio.run(run())

    def test_luna_body_and_cost(self):
        usage = self._generate("gpt-6-luna")
        body = self.bodies[0]
        self.assertEqual(body["max_completion_tokens"], 16000)
        self.assertEqual(body["reasoning_effort"], settings.openai_reasoning_effort)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)
        self.assertEqual(usage.output_tokens, 3000)      # рассуждения уже внутри completion_tokens
        self.assertEqual(usage.reasoning_tokens, 1200)
        # OpenAI напрямую (D-078): ($0.10 × 4000 свежих + $0.01 × 1000 кэш + $0.50 × 3000) / 1 млн × 90 ₽
        self.assertAlmostEqual(usage.cost_rub, (4000 * 0.10 + 1000 * 0.01 + 3000 * 0.50) / 1e6 * 90)

    def test_mini_body_without_price(self):
        usage = self._generate("gpt-4o-mini")
        body = self.bodies[0]
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(body["temperature"], 0.7)
        self.assertNotIn("reasoning_effort", body)
        self.assertIsNone(usage.cost_rub)


from core.llm import models as core_models  # noqa: E402


class Prices(unittest.TestCase):
    """Цены по провайдеру (D-078): провайдер — по хосту OPENAI_BASE_URL, доллары × USD_RUB_RATE."""

    def test_provider_by_base_url(self):
        self.assertEqual(core_models.provider("https://api.openai.com/v1"), "openai")
        self.assertEqual(core_models.provider("https://api.proxyapi.ru/openai/v1"), "proxyapi")
        self.assertEqual(core_models.provider("https://llm.example.test/v1"), "openai")    # default_provider

    def test_openai_usd_by_rate_and_proxy_rub(self):
        from unittest import mock
        with mock.patch.object(settings, "usd_rub_rate", 100.0):
            rub = llm_models.cost_rub("gpt-6-luna", 100_000, 0, 100_000, provider_name="openai")
            self.assertAlmostEqual(rub, (0.10 + 0.50) * 100 / 10)
            cached = llm_models.cost_rub("gpt-6-luna", 100_000, 100_000, 0, provider_name="openai")
            self.assertAlmostEqual(cached, 0.01 * 100 / 10)
        self.assertAlmostEqual(llm_models.cost_rub("gpt-6-luna", 100_000, 0, 100_000, provider_name="proxyapi"),
                               (30 + 150) / 10)                           # рубли прокси — без курса
        self.assertIsNone(llm_models.cost_rub("gpt-4o-mini", 1000, 0, 1000))

    def test_long_context_price(self):
        from unittest import mock
        with mock.patch.object(settings, "usd_rub_rate", 1.0):
            self.assertAlmostEqual(llm_models.cost_rub("gpt-6-luna", 300_000, 0, 0, provider_name="openai"),
                                   300_000 * 0.20 / 1e6)

    def test_default_base_url_is_openai(self):
        from config import Settings
        self.assertEqual(Settings.model_fields["openai_base_url"].default, "https://api.openai.com/v1")
        self.assertEqual(Settings.model_fields["llm_max_concurrency"].default, 8)
        self.assertEqual(Settings.model_fields["usd_rub_rate"].default, 90.0)


class EmptyResponse(unittest.TestCase):

    def test_length_without_content_is_explicit_error(self):
        from types import SimpleNamespace
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="length")])
        with self.assertRaisesRegex(ValueError, "finish_reason=length"):
            llm._response_text(resp)


if __name__ == "__main__":
    unittest.main()
