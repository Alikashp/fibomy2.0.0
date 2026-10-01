"""Сводка golden-прогона и проверки файла (tests/golden/run_golden.py) без сети."""
import asyncio
import sys
import unittest
from unittest import mock

import _helpers  # noqa: F401
from _helpers import ROOT, FakeOpenAI

sys.path.insert(0, str(ROOT / "tests" / "golden"))
import run_golden  # noqa: E402

META = {"model": "gpt-6-luna", "effort": "low", "outline_effort": "medium", "api_host": "api.proxyapi.ru",
        "prompts_version": "2026-10-01.3", "commit": "abcdef1234"}
USAGE = {"calls": 9, "input_tokens": 12000, "cached_input_tokens": 0, "output_tokens": 7000,
         "reasoning_tokens": 1800, "cost_rub": 1.41}


class Summary(unittest.TestCase):

    def test_table_and_details(self):
        results = [
            {"run": "G-04-1", "case": "G-04", "title": "t", "status": "ok", "engine": "new", "seconds": 41.2,
             "slides": 9, "usage": USAGE, "violations": ["слайд-запрос — последний"], "warnings": [], "passed": 20,
             "source_genre": "report", "stages": {"outline": 9100, "content": 6200, "facts": 0},
             "degradations": [{"stage": "fit", "slide_id": "s03", "reason": "facts_fixed"}],
             "kinds": ["title", "metrics", "closing"]},
            {"run": "G-02-1", "case": "G-02", "title": "t", "status": "error", "engine": "new", "seconds": 3.0,
             "error": "BadRequestError: Unsupported parameter"},
            {"run": "pitch", "case": None, "title": "t", "status": "ok", "engine": "old", "seconds": 30.0,
             "slides": 11, "usage": {**USAGE, "cost_rub": None}},
        ]
        md = run_golden.summary_md(results, META)
        self.assertTrue(md.startswith("<!-- golden-report:medium -->"))   # маркер по уровню OUTLINE
        self.assertIn("OUTLINE effort **medium**", md)
        self.assertIn("| G-04-1 | new | ✅ | 9 | — → **1** | 41.2 | 9,1 / 6,2 / 0,0 | 1,41 | 1 | report |", md)
        self.assertIn("| G-02-1 | new | ❌ ошибка |", md)
        self.assertIn("| pitch | old | ✅ | 11 | не проверяется |", md)
        self.assertIn("Итого: 2 из 3 генераций прошли. Новый движок: 1 колод, средняя стоимость **1,41 ₽**", md)
        self.assertIn("**G-02-1 — ошибка:** `BadRequestError: Unsupported parameter`", md)
        self.assertIn("Слайды: title → metrics → closing", md)
        self.assertIn("- fit s03: facts_fixed", md)
        self.assertNotIn("выше 4 ₽", md)
        expensive = run_golden.summary_md([{**results[0], "usage": {**USAGE, "cost_rub": 4.6}}], META)
        self.assertIn("выше 4 ₽", expensive)

    def test_preflight_error(self):
        md = run_golden.summary_md([], META, "api.proxyapi.ru недоступен: ConnectError")
        self.assertIn("API недоступен — генерации не запускались", md)


class NewEngineRun(unittest.TestCase):

    def test_run_case_and_check_files(self):
        """Прогон кейса G-05 на подменном LLM: колода, файлы, проверки check_case и PPTX."""
        from config import settings
        from core import pipeline
        from core.llm import client as llm_client
        import tempfile
        from pathlib import Path

        model = settings.openai_model
        settings.openai_model = "gpt-6-luna"
        llm_client.set_client(llm_client.LLMClient(FakeOpenAI()))

        async def pdf(pptx, **kw):
            return b"%PDF" + b"/Type /Page " * 9

        try:
            case = next(c for c in run_golden.CASES if c.run_id == "G-05")
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pipeline, "pptx_to_pdf", side_effect=pdf):
                r = asyncio.run(run_golden._run_new(case, 1, Path(tmp)))
                files = sorted(p.name for p in Path(tmp).iterdir())
        finally:
            settings.openai_model = model
            llm_client.set_client(None)
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(r["violations"], [])
        self.assertEqual(files, ["G-05.deckspec.json", "G-05.pdf", "G-05.pptx"])
        self.assertEqual(r["slides"], 9)


if __name__ == "__main__":
    unittest.main()
