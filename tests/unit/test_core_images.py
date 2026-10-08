"""ИИ-картинки (core.images, сессия 4): промпт, обрезка, генерация параллельно с CONTENT,
вариант без картинки при ошибке и таймауте, стоимость, заметка «сгенерированы ИИ»."""
import asyncio
import io
import unittest
from unittest import mock

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи
from _helpers import FakeOpenAI, fake_outline

import httpx
from PIL import Image
from pptx import Presentation

from config import settings
from core import images as IMG
from core import pipeline
from core.llm import client as llm_client
from core.llm.client import LLMClient
from core.models.layout import load_layouts
from core.models.request import DeckRequest


def jpeg(w=1024, h=1024, color=(90, 140, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, format="JPEG")
    return out.getvalue()


class FakeImages:
    """Подменяет SiliconFlowClient: картинка, ошибка или «зависание» по номеру вызова."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.prompts: list[str] = []

    async def generate(self, prompt, seed, timeout):
        self.prompts.append(prompt)
        if self.mode == "error":
            raise RuntimeError("HTTP 500: provider down")
        if self.mode == "slow":
            await asyncio.sleep(60)
        return jpeg()


def outline_with_images():
    out = fake_outline(7)
    out["deck"]["cover_image_query"] = "green leaves in sunlight"
    out["deck"]["slides"][0]["image_query"] = "students in a school greenhouse"   # слайд 2 — рядом с титулом
    out["deck"]["slides"][1]["image_query"] = "a greenhouse with tomato plants"   # bullets, слайд 3
    out["deck"]["slides"][2]["image_query"] = "laboratory glassware on a table"   # process — не умеет картинку
    out["deck"]["slides"][5]["image_query"] = "forest seen from above"           # bullets, слайд 7
    return out


class Prompt(unittest.TestCase):

    def test_prompt_parts_and_appearance_by_language(self):
        p = IMG.build_prompt("students working together at a library table", "mint_coral", "ru")
        self.assertTrue(p.startswith("students working together at a library table."))
        self.assertIn("European appearance", p)
        self.assertIn("no text", p)
        self.assertIn("green and teal", p)
        self.assertIn("Central Asian", IMG.build_prompt("a market", "ember_dark", "kk"))
        self.assertIn("Central Asian", IMG.build_prompt("a market", "ember_dark", "uz"))
        self.assertIn("European", IMG.build_prompt("a market", "ember_dark", "en"))
        # одинаковый хвост стиля у всех картинок колоды
        a = IMG.build_prompt("a river", "sunny_cream", "ru").split(". ", 1)[1]
        b = IMG.build_prompt("an old bridge", "sunny_cream", "ru").split(". ", 1)[1]
        self.assertEqual(a, b)

    def test_cover_crop_to_slot(self):
        out = Image.open(io.BytesIO(IMG.cover_crop(jpeg(1024, 1024), 800 / 936)))
        self.assertAlmostEqual(out.width / out.height, 800 / 936, places=2)
        self.assertEqual(out.format, "JPEG")
        big = Image.open(io.BytesIO(IMG.cover_crop(jpeg(3000, 2000), 1.0)))
        self.assertLessEqual(max(big.size), 1600)

    def test_response_shapes(self):
        self.assertEqual(IMG._image_ref({"images": [{"url": "https://x/1.png"}], "seed": 1}), ("https://x/1.png", None))
        self.assertEqual(IMG._image_ref({"data": [{"b64_json": "QUJD"}]}), (None, "QUJD"))
        self.assertEqual(IMG._image_ref({}), (None, None))

    def test_siliconflow_request(self):
        """Запрос — по формату SiliconFlow: model, prompt, image_size; URL картинки скачивается."""
        seen = {}

        def handler(request: httpx.Request):
            if request.url.path.endswith("/images/generations"):
                import json
                seen["body"] = json.loads(request.content)
                seen["auth"] = request.headers["authorization"]
                return httpx.Response(200, json={"images": [{"url": "https://cdn.test/a.png"}], "seed": 5})
            return httpx.Response(200, content=jpeg())

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                return await IMG.SiliconFlowClient(http).generate("a bridge", 5, 10)

        with mock.patch.object(settings, "siliconflow_api_key", "sk-test"):
            data = asyncio.run(go())
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertEqual(seen["body"]["model"], "Tongyi-MAI/Z-Image-Turbo")
        self.assertEqual(seen["body"]["image_size"], "1024x1024")
        self.assertEqual(seen["auth"], "Bearer sk-test")


class DeckWithImages(unittest.TestCase):

    def setUp(self):
        self._model = settings.openai_model
        settings.openai_model = "gpt-6-luna"
        llm_client._SCHEMA_REJECTED.clear()
        self.patches = [mock.patch.object(pipeline, "pptx_to_pdf", side_effect=self._pdf),
                        mock.patch.object(settings, "siliconflow_api_key", "sk-test")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        settings.openai_model = self._model
        for p in self.patches:
            p.stop()
        IMG._client = None

    @staticmethod
    async def _pdf(pptx, **kw):
        return b"%PDF-fake"

    def run_deck(self, images: FakeImages, topic="как работает фотосинтез", **kw):
        IMG._client = images
        req = DeckRequest(input={"topic": topic}, slides_count=9, **kw)
        fake = FakeOpenAI(outline=outline_with_images())

        async def go():
            return await pipeline.generate_deck("dk_01J0000000000000000000000A", req, client=LLMClient(fake))
        return asyncio.run(go())

    def test_images_on_title_and_two_slides(self):
        images = FakeImages()
        res = self.run_deck(images)
        spec = res.spec
        L = load_layouts()
        with_img = [s for s in spec.slides if s.image]
        # титул + 2: слайд 2 — вплотную к титулу, process не умеет картинку
        self.assertEqual([s.index for s in with_img], [1, 3, 7])
        self.assertTrue(all(L[s.variant].image_slot for s in with_img))
        self.assertEqual(spec.slides[0].variant, "title.cover_split_image")
        self.assertEqual(len(spec.assets), 3)
        self.assertEqual(spec.meta.image_mode, "ai")
        self.assertEqual(spec.slides[-1].notes, "Изображения сгенерированы ИИ")
        self.assertEqual(res.usage.by_stage["images"].calls, 3)
        self.assertAlmostEqual(res.usage.by_stage["images"].cost_rub, 1.5)
        self.assertIn("images", res.durations_ms)
        pics = [sh for slide in Presentation(io.BytesIO(res.pptx)).slides for sh in slide.shapes
                if sh.name.startswith("image:")]
        self.assertEqual(len(pics), 3)
        geom = pics[0]._element.spPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom")
        self.assertEqual(geom.get("prst"), "roundRect")                     # скругление
        self.assertTrue(all("European appearance" in p for p in images.prompts))
        # титул — тема дословно, но с заглавной буквы (требование владельца)
        self.assertEqual(spec.meta.title, "Как работает фотосинтез")
        self.assertEqual(spec.slides[0].title, "Как работает фотосинтез")

    def test_provider_error_gives_variant_without_image(self):
        res = self.run_deck(FakeImages("error"))
        spec = res.spec
        L = load_layouts()
        self.assertFalse(any(s.image for s in spec.slides))
        self.assertFalse(any(L[s.variant].image_slot for s in spec.slides))
        self.assertEqual(len([d for d in spec.degradations if d.reason.startswith("image_missing")]), 3)
        self.assertEqual(spec.slides[-1].notes, "")
        self.assertEqual(spec.meta.image_mode, "none")
        self.assertEqual(res.usage.by_stage["images"].cost_rub, 0)
        self.assertEqual(len(Presentation(io.BytesIO(res.pptx)).slides), 9)

    def test_timeout_does_not_hold_the_deck(self):
        with mock.patch.dict(IMG.config(), {"timeout_seconds": 0.3}):
            res = self.run_deck(FakeImages("slow"))
        self.assertTrue(all(d.reason == "image_missing:timeout" for d in res.spec.degradations
                            if d.stage == "images"))
        self.assertLess(res.durations_ms["images"], 5000)

    def test_no_key_or_image_mode_none_means_no_images(self):
        res = self.run_deck(FakeImages(), image_mode="none")
        self.assertNotIn("images", res.usage.by_stage)
        with mock.patch.object(settings, "siliconflow_api_key", ""):
            res = self.run_deck(FakeImages())
        self.assertFalse(res.spec.assets)


if __name__ == "__main__":
    unittest.main()
