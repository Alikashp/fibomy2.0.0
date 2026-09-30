"""Правка 1: блоки промпта выбирает код по source_type и source_mode (П1, П2, П10)."""
import unittest
from datetime import datetime

from _helpers import FIXTURES  # noqa: F401  (путь к src/)

from generation import llm
from schemas.presentation import UserRequest

NOW = datetime(2026, 9, 30)
OLD_DATA_LINE = "Используй реальные данные из своих знаний"
DATE_ANCHOR = "ТЕКУЩАЯ ДАТА: Q3 2026 (30 сентябре)"
MATERIAL_ROLE = "Ты переносишь на слайды факты и выводы из материала"


def _prompts(**kw):
    base = dict(topic="Итоги года", presentation_type="doklad", audience="general")
    base.update(kw)
    return llm.build_prompts(UserRequest(**base), now=NOW)


class MaterialMode(unittest.TestCase):

    def test_strict(self):
        system, user = _prompts(source_type="text", raw_text="Материал школы " * 5, source_mode="strict",
                                slide_count_hint=12)
        self.assertNotIn(OLD_DATA_LINE, system)
        self.assertNotIn("ТЕКУЩАЯ ДАТА", system)          # якоря роадмапа нет
        self.assertIn(MATERIAL_ROLE, system)              # П10
        self.assertIn("бери ТОЛЬКО из блока МАТЕРИАЛ", system)
        self.assertIn("не больше 12 и не меньше 5", user)
        self.assertIn("используй строго материал ниже", user)

    def test_extend(self):
        system, user = _prompts(source_type="document", raw_text="Материал школы " * 5, source_mode="extend")
        self.assertIn("БЕЗ чисел, дат и ссылок на источники", system)
        self.assertIn("ровно 9 (строго)", user)
        self.assertIn("главный источник фактов", user)

    def test_material_without_mode_is_strict(self):
        _, user = _prompts(source_type="text", raw_text="Материал школы " * 5)
        self.assertIn("не больше 9 и не меньше 5", user)


class TopicMode(unittest.TestCase):

    def test_doklad_topic(self):
        system, user = _prompts(slide_count_hint=7)
        self.assertIn(DATE_ANCHOR, system)
        self.assertNotIn(MATERIAL_ROLE, system)
        self.assertNotIn(OLD_DATA_LINE, system)
        self.assertNotIn("реалистичный диапазон", system)
        self.assertNotIn("смежной отрасли", system)
        self.assertIn("любое число на слайде — оценка", system)
        self.assertIn("ровно 7 (строго)", user)
        self.assertNotIn("строго 9", system)

    def test_doklad_schema_without_source_and_with_genre(self):
        system, _ = _prompts()
        schema = system[system.index("СХЕМА JSON:"):]
        self.assertNotIn('"source"', schema)
        self.assertIn('"source_genre"', schema)
        self.assertNotIn("problem", schema)
        self.assertIn('"date": "string|null"', schema)

    def test_pitch_deck_keeps_schema_and_slide_line(self):
        system, user = _prompts(presentation_type="pitch_deck", audience="investors")
        self.assertIn('"source": "string|null"', system)
        self.assertIn("Количество слайдов: 11 (строго).", user)
        self.assertIn(DATE_ANCHOR, system)


if __name__ == "__main__":
    unittest.main()
