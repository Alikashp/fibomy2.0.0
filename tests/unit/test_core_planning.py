"""OUTLINE (проверки и исправления плана), SELECT (выбор варианта), промпты prompts/v2."""
import re
import unittest

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи

from core.content.fill import outline_text, system_prompt, task_prompt
from core.llm import prompts as P
from core.models.digest import SourceDigest
from core.models.outline import OutlineResponse
from core.models.request import DeckRequest
from core.paths import PROMPTS_V2_DIR
from core.planning.outline import (available_kinds, build_prompts, content_slide_count, normalize_plan,
                                   plan_errors)
from core.selection.select import SlideNeed, select_deck

TOPIC = DeckRequest(input={"topic": "Как устроен городской транспорт"}, slides_count=9)


def outline(**kw) -> OutlineResponse:
    return OutlineResponse.model_validate(_helpers.fake_outline(**kw))


class PlanChecks(unittest.TestCase):

    def test_kinds_by_mode_and_catalog(self):
        self.assertEqual(available_kinds(TOPIC), ("statement", "bullets", "conclusion"))
        self.assertEqual(content_slide_count(TOPIC), 7)

    def test_valid_plan_has_no_errors(self):
        self.assertEqual(plan_errors(outline(n=7), TOPIC, 7), [])

    def test_too_few_slides_is_error(self):
        self.assertTrue(any("ровно 7" in e for e in plan_errors(outline(n=5), TOPIC, 7)))
        self.assertEqual(plan_errors(outline(n=6), TOPIC, 7), [])  # на 1 меньше — деградация, не повтор

    def test_duplicate_titles_and_messages(self):
        resp = outline(n=7)
        resp.deck.slides[2].title = resp.deck.slides[1].title + "!"
        resp.deck.slides[3].key_message = resp.deck.slides[4].key_message
        errors = plan_errors(resp, TOPIC, 7)
        self.assertTrue(any("заголовки" in e for e in errors))
        self.assertTrue(any("key_message" in e for e in errors))


class PlanFixes(unittest.TestCase):

    def setUp(self):
        self.reasons = []

    def fix(self, resp, n=7, request=TOPIC):
        return normalize_plan(resp, request, n, available_kinds(request), self.reasons.append)

    def test_chart_in_topic_becomes_bullets(self):
        resp = outline(n=7)
        resp.deck.slides[1].kind = "chart_series"
        slides = self.fix(resp)
        self.assertEqual(slides[1].kind, "bullets")
        self.assertTrue(any("bullets" in r for r in self.reasons))

    def test_items_clamped(self):
        resp = outline(n=7)
        resp.deck.slides[1].items_planned = 9
        resp.deck.slides[-1].items_planned = 1
        slides = self.fix(resp)
        self.assertEqual((slides[1].items_planned, slides[-1].items_planned), (6, 2))
        self.assertIsNone(slides[0].items_planned)  # statement

    def test_ask_moved_last(self):
        resp = outline(n=7)
        resp.deck.slides[2].role = "ask"
        slides = self.fix(resp)
        self.assertEqual(slides[-1].role, "ask")
        self.assertEqual(slides[-1].kind, "statement")

    def test_ask_added_by_code_and_plan_trimmed(self):
        resp = outline(n=7, asks=[{"text": "Утвердить расписание новых маршрутов", "refs": ["f3"]}])
        slides = self.fix(resp)
        self.assertEqual(len(slides), 7)
        self.assertEqual((slides[-1].role, slides[-1].title), ("ask", "Утвердить расписание новых маршрутов"))
        self.assertEqual(slides[-2].kind, "conclusion")  # выводы сохранены при обрезке
        self.assertIn("ask_added_by_code", self.reasons)


class Prompts(unittest.TestCase):

    def test_outline_prompt_topic(self):
        system, user = build_prompts(TOPIC, SourceDigest.for_topic(TOPIC.input.topic), 7, available_kinds(TOPIC))
        self.assertIn("ровно 7 содержательных", system)
        self.assertIn("ДОКЛАД ПО ТЕМЕ", system)
        self.assertNotIn("chart_series", system)
        self.assertNotIn("$", system + user)
        self.assertIn("<topic>Как устроен городской транспорт</topic>", user)

    def test_content_prefix_identical_for_all_slides(self):
        resp = outline(n=7)
        system = system_prompt(TOPIC, SourceDigest.for_topic("Т"), resp.deck.subtitle, resp.deck.slides)
        self.assertIn(outline_text(resp.deck.subtitle, resp.deck.slides), system)
        task = task_prompt(resp.deck.slides[1], 3, 9, "bullets", ["- пунктов: от 3 до 4"])
        self.assertIn("СЛАЙД 3 из 9: kind=bullets", task)
        self.assertNotIn("$", system + task)

    def test_languages(self):
        for lang, word in (("kk", "казахский"), ("uz", "узбекский"), ("en", "английский")):
            blocks = P.common_blocks(mode="topic", source_mode=None, language=lang, audience="general")
            self.assertIn(word, blocks["language"])

    def test_no_golden_vocabulary_in_prompts(self):
        """ТЗ 4.8.5: в промптах нет лексики golden-кейсов G-01…G-05 и питч-дека."""
        stop = ("кофе", "кофейн", "хакатон", "навигатор", "стейкхолдер", "предзаказ", "мурманск",
                "петрозаводск", "архангельск", "корм", "vk", "фотосинтез", "лебедева")
        for path in PROMPTS_V2_DIR.rglob("*"):
            if path.is_file() and path.suffix in (".txt", ".yaml"):
                text = path.read_text(encoding="utf-8").lower()
                for word in stop:
                    self.assertIsNone(re.search(rf"\b{word}", text), f"{path.name}: «{word}»")


class Selection(unittest.TestCase):

    NEEDS = [SlideNeed("s01", "title"), SlideNeed("s02", "statement", title="Короткое утверждение"),
             SlideNeed("s03", "bullets", items=4), SlideNeed("s04", "conclusion", items=3),
             SlideNeed("s05", "closing")]

    def test_one_variant_per_kind_and_deterministic(self):
        a = select_deck(self.NEEDS, seed=7)
        b = select_deck(self.NEEDS, seed=7)
        self.assertEqual([c.variant for c in a], [c.variant for c in b])
        self.assertEqual([c.variant for c in a], ["title.cover_center", "statement.big_quote", "bullets.cards_grid",
                                                  "conclusion.numbered_takeaways", "closing.thanks_center"])
        self.assertTrue(all(c.reason is None for c in a))

    def test_long_statement_falls_back_to_bullets(self):
        need = SlideNeed("s02", "statement", title="очень длинное утверждение " * 12)
        choice = select_deck([need], seed=1)[0]
        self.assertEqual((choice.kind, choice.variant, choice.items), ("bullets", "bullets.cards_grid", 3))
        self.assertIn("kind_fallback", choice.reason)

    def test_items_out_of_range_fall_back(self):
        choice = select_deck([SlideNeed("s03", "bullets", items=8)], seed=1)[0]
        self.assertEqual((choice.variant, choice.items), ("bullets.cards_grid", 6))


if __name__ == "__main__":
    unittest.main()
