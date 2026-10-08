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
from core.models.layout import load_layouts, variants_of
from core.selection.select import SlideNeed, applicable, reselect_comparison, select_deck, without_image

TOPIC = DeckRequest(input={"topic": "Как устроен городской транспорт"}, slides_count=9)


def outline(**kw) -> OutlineResponse:
    return OutlineResponse.model_validate(_helpers.fake_outline(**kw))


class PlanChecks(unittest.TestCase):

    def test_kinds_by_mode_and_catalog(self):
        self.assertEqual(available_kinds(TOPIC), ("statement", "bullets", "comparison", "process", "metrics",
                                                  "conclusion"))
        material = DeckRequest(input={"topic": "Тема", "material": {"kind": "text", "ref": "redis:x"}})
        self.assertIn("chart_series", available_kinds(material))
        self.assertIn("chart_share", available_kinds(material))
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
        return normalize_plan(resp, request, n, available_kinds(request), self.reasons.append)[0]

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
        resp.deck.slides[4].items_planned = 9      # metrics: 1–4 (одно число — metrics.big_number)
        slides = self.fix(resp)
        self.assertEqual((slides[1].items_planned, slides[-1].items_planned, slides[4].items_planned), (6, 2, 4))
        self.assertIsNone(slides[0].items_planned)  # statement
        self.assertIsNone(slides[3].items_planned)  # comparison

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
        self.assertNotIn("chart_series —", system)
        self.assertIn("определение темы", system)
        self.assertIn("не больше 40%", system)
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

    def test_deterministic_and_kind_preserved(self):
        a = select_deck(self.NEEDS, seed=7)
        b = select_deck(self.NEEDS, seed=7)
        self.assertEqual([c.variant for c in a], [c.variant for c in b])
        self.assertEqual([c.variant.split(".")[0] for c in a], ["title", "statement", "bullets", "conclusion",
                                                                "closing"])
        self.assertTrue(all(c.reason is None for c in a))
        self.assertFalse(any(load_layouts()[c.variant].image_slot for c in a), "без картинки — без слота")

    def test_diversity_metrics(self):
        """05_LAYOUTS.md, 4.4: нет двух одинаковых вариантов подряд, ≥ 60% уникальных, разные seed
        дают разные колоды (≥ 50% различий там, где у kind больше одного варианта)."""
        kinds = ["statement", "bullets", "process", "bullets", "comparison", "metrics", "statement", "bullets",
                 "process", "bullets", "conclusion"]
        items = {"bullets": 4, "process": 4, "metrics": 3, "conclusion": 3}
        needs = lambda: [SlideNeed("s01", "title", title="Тема")] + [  # noqa: E731
            SlideNeed(f"s{i:02d}", k, items=items.get(k), title="Короткое утверждение")
            for i, k in enumerate(kinds, start=2)] + [SlideNeed(f"s{len(kinds) + 2:02d}", "closing")]
        diffs = []
        for seed in range(1, 21):
            a = [c.variant for c in select_deck(needs(), seed)]
            self.assertFalse(any(x == y for x, y in zip(a, a[1:])), a)
            content = a[1:-1]
            self.assertGreaterEqual(len(set(content)) / len(content), 0.6, a)
            b = [c.variant for c in select_deck(needs(), seed + 100)]
            # позиции, где у kind больше одного применимого варианта (с учётом расстановки цветных)
            L, last, multi = load_layouts(), None, []
            for i, (n, v) in enumerate(zip(needs(), a)):
                n.index, n.total = i + 1, len(a)
                if len([x for x in variants_of(n.kind) if applicable(x, n, last)]) > 1:
                    multi.append(i)
                if L[v].color_heavy or n.kind == "title":
                    last = i + 1
            diffs.append(sum(a[i] != b[i] for i in multi) / len(multi))
        self.assertGreaterEqual(sum(diffs) / len(diffs), 0.5)

    def test_color_slides_spaced(self):
        """Цветные варианты (пауза, полоса, картинка на цвете) — не ближе трёх слайдов к титулу, финалу и друг к другу."""
        needs = [SlideNeed("s01", "title", title="Тема")] + [
            SlideNeed(f"s{i:02d}", "statement", title="Короткое утверждение") for i in range(2, 12)] + \
            [SlideNeed("s12", "closing")]
        L = load_layouts()
        for seed in range(1, 30):
            chosen = select_deck([SlideNeed(**n.__dict__) for n in needs], seed)
            colored = [i for i, c in enumerate(chosen, start=1)
                       if L[c.variant].color_heavy or c.kind in ("title", "closing")]
            self.assertTrue(all(b - a >= 3 for a, b in zip(colored, colored[1:])), (seed, colored))

    def test_image_variants_only_with_image(self):
        L = load_layouts()
        with_img = select_deck([SlideNeed("s01", "title", title="Тема", has_image=True),
                                SlideNeed("s02", "statement", title="Утверждение"),
                                SlideNeed("s03", "bullets", items=3, has_image=True),
                                SlideNeed("s04", "closing")], seed=3)
        self.assertEqual(with_img[0].variant, "title.cover_split_image")
        self.assertTrue(L[with_img[2].variant].image_slot)
        self.assertFalse(L[with_img[1].variant].image_slot)
        # картинка не пришла — тот же kind, вариант без картинки
        alt = without_image(with_img[2], SlideNeed("s03", "bullets", items=3, has_image=True), 3, {})
        self.assertEqual(alt.kind, "bullets")
        self.assertFalse(L[alt.variant].image_slot)
        self.assertEqual(alt.reason, "image_missing")
        title = without_image(with_img[0], SlideNeed("s01", "title", title="Тема", has_image=True), 3, {})
        self.assertIn(title.variant, ("title.cover_center", "title.cover_band"))

    def test_pros_cons_by_polarity(self):
        need = SlideNeed("s02", "comparison")
        self.assertEqual(select_deck([need], 1)[0].variant, "comparison.two_columns")
        self.assertEqual(reselect_comparison("comparison.two_columns", "pros_cons"), "comparison.pros_cons")
        self.assertEqual(reselect_comparison("comparison.two_columns", "before_after"), "comparison.pros_cons")
        self.assertEqual(reselect_comparison("comparison.pros_cons", "neutral"), "comparison.two_columns")

    def test_one_number_is_big_number(self):
        self.assertEqual(select_deck([SlideNeed("s02", "metrics", "results", items=1)], 1)[0].variant,
                         "metrics.big_number")
        self.assertEqual(select_deck([SlideNeed("s02", "metrics", items=3)], 1)[0].variant, "metrics.kpi_cards")


if __name__ == "__main__":
    unittest.main()
