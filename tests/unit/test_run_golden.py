"""Сводка golden-прогона (tests/golden/run_golden.py) без сети."""
import sys
import unittest

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "tests" / "golden"))
import run_golden  # noqa: E402

META = {"model": "gpt-6-luna", "effort": "low", "api_host": "api.proxyapi.ru",
        "prompts_version": "2026-09-30", "commit": "abcdef1234"}
USAGE = {"calls": 2, "input_tokens": 12000, "cached_input_tokens": 0, "output_tokens": 7000,
         "reasoning_tokens": 1800, "cost_rub": 1.41, "models": ["gpt-6-luna"]}


class Summary(unittest.TestCase):

    def test_table_and_details(self):
        results = [
            {"run": "G-01-1", "case": "G-01", "title": "t", "status": "ok", "seconds": 41.2, "slides": 8,
             "usage": USAGE, "violations": ["−6% — корпоративные заказы"], "warnings": [], "passed": 20,
             "source_genre": "report"},
            {"run": "G-02-1", "case": "G-02", "title": "t", "status": "error", "seconds": 3.0,
             "error": "BadRequestError: Unsupported parameter"},
            {"run": "pitch", "case": None, "title": "t", "status": "ok", "seconds": 30.0, "slides": 11,
             "usage": {**USAGE, "cost_rub": None}},
        ]
        md = run_golden.summary_md(results, META)
        self.assertTrue(md.startswith("<!-- golden-report -->"))           # маркер для обновления комментария
        self.assertIn("| G-01-1 | ✅ | 8 | 15 → **1** | 41.2 | 12 000 / 7 000 (1 800) | 1,41 |", md)
        self.assertIn("| G-02-1 | ❌ ошибка |", md)
        self.assertIn("| pitch | ✅ | 11 | не проверяется | 30.0 | 12 000 / 7 000 (1 800) | н/д |", md)
        self.assertIn("Итого: 2 из 3 генераций прошли", md)
        self.assertIn("стоимость 1,41 ₽", md)
        self.assertIn("**G-02-1 — ошибка:** `BadRequestError: Unsupported parameter`", md)
        self.assertIn("- −6% — корпоративные заказы", md)

    def test_preflight_error(self):
        md = run_golden.summary_md([], META, "api.proxyapi.ru недоступен: ConnectError")
        self.assertIn("API недоступен — генерации не запускались", md)

    def test_cases_match_task(self):
        runs = {c.run_id: c.repeat for c in run_golden.CASES}
        self.assertEqual(runs, {"G-01": 2, "G-02": 2, "G-03": 1, "pitch": 1})
        for c in run_golden.CASES:
            if c.file:
                self.assertTrue((ROOT / "tests/fixtures" / c.file).exists())
                self.assertEqual(c.request["source_mode"], "strict")


if __name__ == "__main__":
    unittest.main()
