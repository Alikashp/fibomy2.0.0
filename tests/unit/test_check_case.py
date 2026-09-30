"""tests/golden/check_case.py на колодах старого движка (tests/fixtures/specs/)."""
import json
import sys
import unittest

from _helpers import FIXTURES, ROOT

sys.path.insert(0, str(ROOT / "tests" / "golden"))
import check_case  # noqa: E402


def _check(name, case):
    return check_case.check(json.loads((FIXTURES / "specs" / f"{name}.json").read_text()), case)


class OldDecks(unittest.TestCase):

    def test_known_defects_are_found(self):
        g01 = " | ".join(_check("prodazhi_Q3", "G-01").violations)
        for expected in ("title пустой: слайд 4", "на 8% больше", "октябрь 2026", "Отчет сети кофеен",
                         "нет ['7%']", "Общая динамика"):
            self.assertIn(expected, g01)

        g02 = " | ".join(_check("hakaton", "G-02").violations)
        for expected in ("90%", "слайд 2 (problem)", "Результаты работы", "Тестирование сервиса, 2026",
                         "timeline: слайд 7", "рекордно"):
            self.assertIn(expected, g02)

        g03 = " | ".join(_check("prodazhi_topic_only", "G-03").violations)
        for expected in ("20% (слайд 5), 30% (слайд 4)", "Отчеты отдела маркетинга", "нет сноски"):
            self.assertIn(expected, g03)

    def test_no_false_positives_from_source_facts(self):
        g01 = _check("prodazhi_Q3", "G-01")
        self.assertIn("+41% — рост доставки", g01.passed)
        self.assertIn("112% — план Санкт-Петербурга", g01.passed)
        # «сентябрь» — факт III квартала из источника, не дата плана
        self.assertNotIn("сентябр", " ".join(g01.violations))


class GoldenRunFalsePositives(unittest.TestCase):
    """Фразы из реальных колод gpt-6-luna (golden, PR #75), на которых check_case ошибался."""

    def _deck(self, slides):
        body = [{"index": 1, "layout": "title", "title": "Т"}]
        body += [dict(s, index=i) for i, s in enumerate(slides, start=2)]
        body.append({"index": len(body) + 1, "layout": "closing", "title": "Спасибо"})
        return {"slides": body}

    def test_cities_slide_is_not_q4_priorities_and_two_points_of_arkhangelsk(self):
        deck = self._deck([
            {"layout": "bullets", "title": "Санкт-Петербург превысил план, Мурманск — нет", "bullets": [
                {"text": "Санкт-Петербург 9 точек принесли 26,6 млн руб.; план 112%"},
                {"text": "Петрозаводск 4 точки, план 104%"},
                {"text": "Мурманск 3 точки, план 87% из-за ремонта торгового центра"},
                {"text": "Архангельск две точки принесли 4,7 млн руб., план 98%"}]},
            {"layout": "bullets", "title": "В IV квартале — три приоритета", "bullets": [
                {"text": "Запускается программа лояльности"},
                {"text": "В Санкт-Петербурге планируется открыть две точки"},
                {"text": "Новое предложение для корпоративных клиентов"}]},
        ])
        r = check_case.check(deck, "G-01")
        joined = " | ".join(r.violations)
        self.assertNotIn("приоритетов IV квартала", joined)
        self.assertNotIn("две новые точки", joined)
        self.assertIn("три приоритета IV квартала", r.passed)

    def test_real_q4_extra_priority_still_found(self):
        deck = self._deck([{"layout": "bullets", "title": "Планы на IV квартал", "bullets": [
            {"text": "а"}, {"text": "б"}, {"text": "в"}, {"text": "Анализ результатов"}]}])
        self.assertIn("не больше трёх приоритетов", " | ".join(check_case.check(deck, "G-01").violations))

    def test_constraint_wording_and_conclusion_title(self):
        deck = self._deck([
            {"layout": "two_column", "title": "Решение должно укладываться в ограничения",
             "two_column": {"left_bullets": [{"text": "Генерация одной колоды должна занимать не более 5 минут."}],
                            "right_bullets": [{"text": "Время генерации не должно превышать 5 минут."}]}},
            {"layout": "diagram", "title": "Пайплайн связывает разбор шаблона с проверкой результата",
             "mermaid_code": "graph LR"},
        ])
        r = check_case.check(deck, "G-02")
        joined = " | ".join(r.violations)
        self.assertNotIn("с подписью ограничения", joined)
        self.assertNotIn("«Что сделано»", joined)
        self.assertTrue(any("результат" in w for w in r.warnings))

    def test_g02_wording_from_pr76(self):
        """Golden PR #76: всё требуемое в колоде есть, но другими словами."""
        deck = self._deck([
            {"layout": "two_column", "title": "Командам нужно показать работу с разными шаблонами",
             "two_column": {"left_bullets": [
                 {"text": "Нужно декомпозировать три шаблона разных типов и выделить токены дизайн-системы."},
                 {"text": "Сервис должен генерировать структуру и содержание по брифу, а также слайды с графиками."},
                 {"text": "Для одного шаблона нужно представить три визуально различимых варианта верстки."}]}},
            {"layout": "bullets", "title": "Оценка охватывает архитектуру, качество и выступление",
             "subtitle": "В техническом задании перечислены пять направлений оценки решения.",
             "bullets": [{"text": "Подход коллектива — обоснованность архитектуры пайплайна."}]},
        ])
        passed = check_case.check(deck, "G-02").passed
        for name in ("три бизнес-задачи сервиса", "требование трёх вариантов вёрстки", "есть критерии оценки"):
            self.assertIn(name, passed)

    def test_g02_missing_still_found(self):
        deck = self._deck([{"layout": "bullets", "title": "Сроки", "bullets": [{"text": "Сдать до 1 марта."}]}])
        joined = " | ".join(check_case.check(deck, "G-02").violations)
        for name in ("три бизнес-задачи сервиса", "требование трёх вариантов вёрстки", "есть критерии оценки"):
            self.assertIn(name, joined)


if __name__ == "__main__":
    unittest.main()
