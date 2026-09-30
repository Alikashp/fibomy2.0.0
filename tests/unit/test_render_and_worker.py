"""Сноска и таймлайн в шаблоне доклада; сообщение «материала хватило на X»."""
import unittest

from _helpers import deck

from generation.template_engine import render_presentation
from schemas.presentation import PresentationSchema, UserRequest


class Template(unittest.TestCase):

    def test_footnote_rendered(self):
        p = PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "bullets", "title": "Т", "bullets": [{"text": "42 ученика"}],
             "footnote": "Оценочные данные — проверьте перед показом"},
        ]))
        html = render_presentation(p, image_urls={}, watermark=False, color_scheme="light")
        self.assertIn('<div class="ftnote">Оценочные данные — проверьте перед показом</div>', html)

    def test_timeline_without_date_does_not_print_none(self):
        p = PresentationSchema.model_validate(deck([
            {"index": 0, "layout": "timeline", "title": "Т", "timeline_items": [{"title": "Этап"}]},
        ]))
        html = render_presentation(p, image_urls={}, watermark=False, color_scheme="light")
        self.assertNotIn(">None<", html)


class MaterialShortage(unittest.TestCase):

    def setUp(self):
        import os
        os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
        from worker import _material_shortage_note
        self.note = _material_shortage_note

    def _req(self, **kw):
        base = dict(topic="Итоги", presentation_type="doklad", audience="general",
                    source_type="document", raw_text="x" * 30, slide_count_hint=12)
        base.update(kw)
        return UserRequest(**base)

    def test_strict_fewer_slides(self):
        self.assertEqual(self.note(self._req(source_mode="strict"), 7),
                         "В вашем материале хватило на 7 слайдов из 12 — добавьте текст, если нужно больше.")

    def test_no_note(self):
        self.assertIsNone(self.note(self._req(source_mode="strict"), 12))
        self.assertIsNone(self.note(self._req(source_mode="extend"), 7))
        self.assertIsNone(self.note(self._req(source_type="topic", raw_text=None), 7))

    def test_material_without_mode_counts_as_strict(self):
        self.assertIsNotNone(self.note(self._req(), 7))


if __name__ == "__main__":
    unittest.main()
