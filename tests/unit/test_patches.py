"""Правки кода 5 и 6: дозапрос заголовка и дозапрос пустого слайда (llm.py)."""
import asyncio
import unittest

from _helpers import FakeLLM, deck

from generation import llm
from schemas.presentation import PresentationSchema, SlideLayout, UserRequest

LONG_MATERIAL = "Начало материала. " + "Абзац без чисел. " * 250 + "ХВОСТ: [Таблица 3] Склад Юг | 310 | −12"


def _request(**kw):
    base = dict(topic="Итоги года", presentation_type="doklad", audience="general")
    base.update(kw)
    return UserRequest(**base)


def _run(coro):
    return asyncio.run(coro)


class TitleOnlyPatch(unittest.TestCase):
    """Правка 5: содержательный слайд без title — дозапрос только заголовка."""

    def setUp(self):
        self.fake = FakeLLM(lambda prompt: {"title": "Средний балл вырос до 4,3", "bullets": [{"text": "лишнее"}]})
        llm.client = self.fake

    def test_untitled_slide_gets_title_and_nothing_else_changes(self):
        p = PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "bullets", "title": None, "bullets": [{"text": "Балл 4,3"}, {"text": "Кружок 42"}]},
            {"index": 0, "layout": "quote", "title": "Главное", "body_text": "Тезис."},
        ]))
        out = _run(llm._patch_missing_titles(_request(), p))
        slide = out.slides[1]
        self.assertEqual(slide.title, "Средний балл вырос до 4,3")
        self.assertEqual([b.text for b in slide.bullets], ["Балл 4,3", "Кружок 42"])
        self.assertEqual(len(self.fake.prompts), 1)
        prompt = self.fake.prompts[0]
        self.assertIn("- Балл 4,3", prompt)          # содержание слайда
        self.assertIn("- Главное", prompt)            # заголовки остальных слайдов
        self.assertIn('{"title": "..."}', prompt)

    def test_titled_and_service_slides_are_not_patched(self):
        p = PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "bullets", "title": "Есть", "bullets": [{"text": "а"}]},
        ]))
        out = _run(llm._patch_missing_titles(_request(), p))
        self.assertEqual(self.fake.prompts, [])
        self.assertEqual(out.slides[1].title, "Есть")

    def test_failed_title_patch_keeps_slide(self):
        llm.client = FakeLLM(lambda prompt: {"title": ""})
        p = PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "bullets", "title": None, "bullets": [{"text": "а"}]},
        ]))
        out = _run(llm._patch_missing_titles(_request(), p))
        self.assertIsNone(out.slides[1].title)


class EmptySlidePatch(unittest.TestCase):
    """Правка 6: дозапрос видит весь материал, остальные слайды и требует заголовок."""

    def _deck(self):
        return PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "metrics", "title": "Баллы по классам", "metrics": [
                {"value": "4,3", "label": "средний балл школы"}]},
            {"index": 0, "layout": "metrics", "title": None, "subtitle": None},  # пустой, без заголовка
            {"index": 0, "layout": "quote", "title": "Главное", "body_text": "Школа растёт."},
        ]))

    def test_prompt_has_full_material_other_slides_and_title_requirement(self):
        fake = FakeLLM(lambda prompt: {"title": "Кружок робототехники набрал 42 ученика",
                                       "subtitle": "Новый кружок", "bullets": [{"text": "42 ученика"}, {"text": "2 группы"}]})
        llm.client = fake
        req = _request(source_type="text", raw_text=LONG_MATERIAL, source_mode="strict")
        out = _run(llm._patch_empty_slides(req, self._deck()))

        self.assertEqual(len(fake.prompts), 1)
        prompt = fake.prompts[0]
        self.assertIn("ХВОСТ: [Таблица 3]", prompt)                 # весь материал, не [:3000]
        self.assertGreater(len(LONG_MATERIAL), 3000)
        self.assertIn("«Баллы по классам»: 4,3 — средний балл школы", prompt)  # факты соседей
        self.assertIn("«Главное»: Школа растёт.", prompt)
        self.assertIn("НЕ повторяй", prompt)
        self.assertIn("У слайда нет заголовка", prompt)
        self.assertIn('{"title": "...", "subtitle": "...", "bullets"', prompt)
        self.assertIn("ТОЛЬКО из МАТЕРИАЛА", prompt)                # правило чисел strict

        slide = out.slides[2]
        self.assertEqual(slide.layout, SlideLayout.BULLETS)          # metrics без чисел → bullets
        self.assertEqual(slide.title, "Кружок робототехники набрал 42 ученика")
        self.assertEqual(len(slide.bullets), 2)

    def test_existing_title_is_not_overwritten(self):
        llm.client = FakeLLM(lambda prompt: {"title": "Другой", "bullets": [{"text": "а"}, {"text": "б"}]})
        p = PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "bullets", "title": "Свой заголовок"},
        ]))
        out = _run(llm._patch_empty_slides(_request(), p))
        self.assertEqual(out.slides[1].title, "Свой заголовок")
        self.assertEqual(len(out.slides[1].bullets), 2)

    def test_topic_mode_prompt_has_estimate_rule_and_no_material(self):
        fake = FakeLLM(lambda prompt: {"bullets": [{"text": "а"}, {"text": "б"}]})
        llm.client = fake
        _run(llm._patch_empty_slides(_request(), self._deck()))
        self.assertIn("только как оценка", fake.prompts[0])
        self.assertNotIn("МАТЕРИАЛ (используй", fake.prompts[0])

    def test_failed_patch_leaves_slide_unchanged(self):
        llm.client = FakeLLM(lambda prompt: {"bullets": []})
        out = _run(llm._patch_empty_slides(_request(), self._deck()))
        self.assertEqual(out.slides[2].layout, SlideLayout.METRICS)


class Pipeline(unittest.TestCase):
    """Сквозной прогон постобработки на JSON колоды хакатона из фикстур."""

    def test_hakaton_spec(self):
        import json
        from _helpers import FIXTURES
        from generation import postprocess

        llm.client = FakeLLM(lambda prompt: {"title": "Сервис должен выполнять четыре функции"})
        data = postprocess.prepare_doklad_json(json.loads((FIXTURES / "specs/hakaton.json").read_text()))
        p = PresentationSchema.model_validate(data)
        req = _request(source_type="document", raw_text="x" * 30, source_name="tz.pdf", source_mode="strict")
        out = _run(llm.postprocess_presentation(req, p))

        layouts = [s.layout.value for s in out.slides]
        self.assertNotIn("problem", layouts)
        self.assertNotIn("solution", layouts)
        self.assertTrue(all(s.title for s in out.slides))                    # слайд 5 получил заголовок
        self.assertEqual({m.source for s in out.slides for m in s.metrics}, {"по данным: tz.pdf"})


if __name__ == "__main__":
    unittest.main()
