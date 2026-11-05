"""ИИ-картинки (core.images, сессия 4): промпт, срез маркировки SiliconFlow и обрезка под слот (D-079),
генерация параллельно с CONTENT, вариант без картинки при ошибке и таймауте, стоимость в ₽ по курсу."""
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

    def test_marking_band_is_cut_and_slot_aspect_kept(self):
        """Надпись «AI 生成» — 37–67 px от низа, 39–142 px от правого края при любом разрешении (зонд
        15.10.2026): после среза и cover-crop под слот её пикселей нет, пропорции — как у слота."""
        for (w, h), aspect in (((1024, 1120), 864 / 936), ((1024, 1120), 800 / 936), ((1328, 1328), 856 / 936),
                               ((768, 1024), 1.0), ((1024, 1120), 16 / 9)):
            img = Image.new("RGB", (w, h), (90, 140, 200))
            img.paste((255, 0, 0), (w - 142, h - 67, w - 39, h - 37))            # «надпись» красным
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out = Image.open(io.BytesIO(IMG.cover_crop(buf.getvalue(), aspect))).convert("RGB")
            self.assertAlmostEqual(out.width / out.height, aspect, places=2)
            reds = sum(1 for r, g, b in out.getdata() if r > 180 and g < 90 and b < 90)
            self.assertEqual(reds, 0, ((w, h), aspect))

    def test_strip_marking_cuts_bottom_band_from_config(self):
        img = Image.new("RGB", (1024, 1120))
        out = IMG.strip_marking(img)
        self.assertEqual(out.size, (1024, 1120 - IMG.config()["marking"]["crop_px"]["bottom"]))
        self.assertEqual(IMG.config()["marking"]["crop_px"]["bottom"], 80)
        self.assertEqual(IMG.config()["image_size"], "1024x1120")
        self.assertEqual(IMG.strip_marking(Image.new("RGB", (100, 100))).size, (100, 100))   # слишком мал

    def test_price_in_rub_by_rate(self):
        with mock.patch.object(settings, "usd_rub_rate", 100.0):
            self.assertAlmostEqual(IMG.price_rub(), 0.5)

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
        self.assertEqual(seen["body"]["image_size"], "1024x1120")
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
        self.assertEqual(spec.slides[-1].notes, "")                         # пометки «сгенерированы ИИ» нет (D-079)
        self.assertEqual(res.usage.by_stage["images"].calls, 3)
        self.assertAlmostEqual(res.usage.by_stage["images"].cost_rub, 3 * 0.005 * settings.usd_rub_rate)
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
