"""Правки кода 2, 3, 4 и лимит метрик доклада (generation/postprocess.py)."""
import unittest

from _helpers import deck

from generation import postprocess
from schemas.presentation import PresentationSchema, SlideLayout, UserRequest


def _p(slides, ptype="doklad"):
    return PresentationSchema.model_validate(deck(slides, ptype))


class TimelineWithoutDates(unittest.TestCase):
    """Правка 3: если хотя бы у одного этапа нет даты — список."""

    def test_one_missing_date_becomes_bullets(self):
        p = _p([{"index": 0, "layout": "timeline", "title": "Планы", "timeline_items": [
            {"date": "Март", "title": "Ремонт спортзала", "description": "Подрядчик выбран."},
            {"date": None, "title": "Закупка ноутбуков"},
            {"title": "Отчёт родителям", "description": "На собрании."},
        ]}])
        s = postprocess.timelines_without_dates_to_bullets(p).slides[1]
        self.assertEqual(s.layout, SlideLayout.BULLETS)
        self.assertEqual(s.timeline_items, [])
        self.assertEqual(len(s.bullets), 3)  # этапы не теряются
        self.assertEqual(s.bullets[0].subtitle, "Март · Ремонт спортзала")
        self.assertEqual(s.bullets[0].text, "Подрядчик выбран.")
        self.assertEqual(s.bullets[1].text, "Закупка ноутбуков")
        self.assertEqual(s.title, "Планы")

    def test_all_dates_kept_as_timeline(self):
        p = _p([{"index": 0, "layout": "timeline", "title": "Планы", "timeline_items": [
            {"date": "Март", "title": "А"}, {"date": "Май", "title": "Б"},
        ]}])
        s = postprocess.timelines_without_dates_to_bullets(p).slides[1]
        self.assertEqual(s.layout, SlideLayout.TIMELINE)
        self.assertEqual(len(s.timeline_items), 2)

    def test_blank_date_counts_as_missing(self):
        p = _p([{"index": 0, "layout": "timeline", "title": "П", "timeline_items": [
            {"date": "Март", "title": "А"}, {"date": "  ", "title": "Б"},
        ]}])
        self.assertEqual(postprocess.timelines_without_dates_to_bullets(p).slides[1].layout, SlideLayout.BULLETS)

    def test_more_than_six_items_are_merged_not_dropped(self):
        items = [{"title": f"Этап {i}"} for i in range(8)]
        p = _p([{"index": 0, "layout": "timeline", "title": "П", "timeline_items": items}])
        s = postprocess.timelines_without_dates_to_bullets(p).slides[1]
        self.assertEqual(len(s.bullets), 6)
        self.assertIn("Этап 7", s.bullets[-1].text)


class RemapLayouts(unittest.TestCase):
    """Правка 4: layout вне каталога доклада → ближайший из каталога."""

    def _one(self, slide):
        return postprocess.remap_layouts(_p([slide])).slides[1]

    def test_problem_with_text_and_image_becomes_image_hero(self):
        s = self._one({"index": 0, "layout": "problem", "title": "Почему", "subtitle": "П",
                       "body_text": "Текст проблемы.", "image_query": "school gym"})
        self.assertEqual(s.layout, SlideLayout.IMAGE_HERO)
        self.assertEqual(s.body_text, "Текст проблемы.")

    def test_solution_with_bullets_becomes_bullets_and_keeps_text(self):
        s = self._one({"index": 0, "layout": "solution", "title": "Как", "body_text": "Короткое пояснение.",
                       "bullets": [{"text": "Первый"}, {"text": "Второй"}]})
        self.assertEqual(s.layout, SlideLayout.BULLETS)
        self.assertEqual(s.subtitle, "Короткое пояснение.")
        self.assertEqual([b.text for b in s.bullets], ["Первый", "Второй"])

    def test_market_with_metrics_becomes_metrics(self):
        s = self._one({"index": 0, "layout": "market", "title": "Рынок", "metrics": [
            {"value": "10", "label": "а"}, {"value": "20", "label": "б"}]})
        self.assertEqual(s.layout, SlideLayout.METRICS)

    def test_team_becomes_bullets(self):
        s = self._one({"index": 0, "layout": "team", "title": "Команда", "team_members": [
            {"name": "Анна", "role": "директор", "bio": "20 лет в школе"}]})
        self.assertEqual(s.layout, SlideLayout.BULLETS)
        self.assertEqual(s.bullets[0].text, "Анна — директор. 20 лет в школе")
        self.assertEqual(s.team_members, [])

    def test_competition_becomes_bullets(self):
        s = self._one({"index": 0, "layout": "competition", "title": "Сравнение", "competition_table": {
            "our_name": "Мы", "competitors": [{"name": "Они"}],
            "features": [{"name": "Цена", "values": {"Мы": "yes", "Они": "no"}}]}})
        self.assertEqual(s.layout, SlideLayout.BULLETS)
        self.assertEqual(s.bullets[0].text, "Цена: Мы — yes; Они — no")

    def test_body_only_becomes_single_bullet(self):
        s = self._one({"index": 0, "layout": "problem", "title": "П", "body_text": "Только текст."})
        self.assertEqual(s.layout, SlideLayout.BULLETS)
        self.assertEqual(s.bullets[0].text, "Только текст.")

    def test_catalog_layouts_untouched(self):
        slide = {"index": 0, "layout": "quote", "title": "Q", "body_text": "Тезис."}
        self.assertEqual(self._one(slide).layout, SlideLayout.QUOTE)


class PrepareDokladJson(unittest.TestCase):
    """До валидации: source модели выбрасывается, ряд > 4 метрик не обрезается."""

    def test_source_is_dropped(self):
        data = deck([{"index": 0, "layout": "metrics", "title": "М", "metrics": [
            {"value": "5%", "label": "рост", "source": "Отчёт отдела, 2026"}]}])
        out = postprocess.prepare_doklad_json(data)
        self.assertNotIn("source", out["slides"][1]["metrics"][0])

    def test_four_metrics_stay(self):
        metrics = [{"value": str(i), "label": f"м{i}"} for i in range(4)]
        out = postprocess.prepare_doklad_json(deck([{"index": 0, "layout": "metrics", "title": "М", "metrics": metrics}]))
        self.assertEqual(out["slides"][1]["layout"], "metrics")
        self.assertEqual(len(out["slides"][1]["metrics"]), 4)

    def test_five_metrics_become_bullets(self):
        metrics = [{"value": f"{i}%", "label": f"склад {i}", "trend": "т"} for i in range(5)]
        out = postprocess.prepare_doklad_json(deck([{"index": 0, "layout": "metrics", "title": "М", "metrics": metrics}]))
        slide = out["slides"][1]
        self.assertEqual(slide["layout"], "bullets")
        self.assertEqual(slide["metrics"], [])
        self.assertEqual(len(slide["bullets"]), 5)
        self.assertEqual(slide["bullets"][4]["text"], "4% — склад 4 (т)")
        PresentationSchema.model_validate(out)  # и схема принимает результат


class AssignSources(unittest.TestCase):
    """Правка 2: metrics[].source и сноска ставит код."""

    SLIDES = [
        {"index": 0, "layout": "metrics", "title": "Числа", "metrics": [{"value": "4,3", "label": "средний балл"}]},
        {"index": 0, "layout": "bullets", "title": "Без чисел", "bullets": [{"text": "Кружок открыт."}]},
        {"index": 0, "layout": "bullets", "title": "С числом", "bullets": [{"text": "Набрали 42 ученика."}]},
    ]

    def test_material_file_name(self):
        req = UserRequest(topic="Итоги", presentation_type="doklad", audience="general",
                          source_type="document", raw_text="x" * 30, source_name="school.docx")
        p = postprocess.assign_sources(_p(self.SLIDES), req)
        self.assertEqual(p.slides[1].metrics[0].source, "по данным: school.docx")
        self.assertTrue(all(s.footnote is None for s in p.slides))

    def test_material_text(self):
        req = UserRequest(topic="Итоги", presentation_type="doklad", audience="general",
                          source_type="text", raw_text="x" * 30)
        p = postprocess.assign_sources(_p(self.SLIDES), req)
        self.assertEqual(p.slides[1].metrics[0].source, "по данным: текст пользователя")

    def test_topic_estimate_footnote_only_on_slides_with_numbers(self):
        req = UserRequest(topic="Итоги 2026", presentation_type="doklad", audience="general")
        p = postprocess.assign_sources(_p(self.SLIDES), req)
        self.assertEqual(p.slides[1].metrics[0].source, postprocess.ESTIMATE_FOOTNOTE)
        self.assertEqual([s.footnote for s in p.slides], [
            None, postprocess.ESTIMATE_FOOTNOTE, None, postprocess.ESTIMATE_FOOTNOTE, None])


if __name__ == "__main__":
    unittest.main()
