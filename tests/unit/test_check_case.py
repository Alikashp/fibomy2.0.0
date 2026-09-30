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


if __name__ == "__main__":
    unittest.main()
