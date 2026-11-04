"""Документация не устаревает молча (D-080): пути в README, CLAUDE.md, docs/API.md, docs/REVIEW.md и
docs/features/ существуют, каждый роут API описан в docs/API.md, каждый документ есть в своей карте,
индекс docs/DECISIONS.md актуален — scripts/check_docs.py, scripts/decisions_index.py."""
import sys
import unittest

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import check_docs  # noqa: E402
import decisions_index  # noqa: E402


class Docs(unittest.TestCase):

    def test_docs_match_repository(self):
        errors = check_docs.run()
        self.assertEqual(errors, [], "\n" + "\n".join(errors) + "\nПравьте документ (код — источник истины) "
                                     "и запустите python scripts/check_docs.py")

    def test_checker_catches_problems(self):
        self.assertEqual(check_docs._candidates("fix/plan-command"), [])          # имя ветки
        self.assertEqual(check_docs._candidates("image/png"), [])                 # MIME
        self.assertEqual(check_docs._candidates("layouts/<kind>/<variant>.yaml"), [])  # шаблон
        self.assertEqual(check_docs._candidates("/v1/decks/{id}"), [])            # адрес API
        self.assertIn("bot/delivery.py", check_docs._candidates("bot/delivery.send_files"))
        doc = ROOT / "README.md"
        self.assertTrue(check_docs._exists(check_docs._candidates("core/fitting/resolve.py"), doc))
        self.assertFalse(check_docs._exists(check_docs._candidates("bot/handlers.py"), doc))
        regex = check_docs._route_regex("/v1/decks/{deck_id}/files/{fmt}")
        self.assertTrue(regex.search("| `GET` | `/v1/decks/{id}/files/pptx` | PPTX |"))
        self.assertFalse(regex.search("`/v1/decks/{id}`"))


    def test_decisions_index(self):
        text = """## Индекс решений

## Шаблон записи

## 2026-11-04 — Сессия

### D-002 — Второе

**Статус:** действует (PR #2); заменяет D-001

### D-001 — Первое

**Статус:** заменено D-002 (решение пересмотрено: подробно)

### D-002 — дополнение без статуса

## Восстановленные решения

### 2026-08 — Старое

**Статус:** действует
"""
        rows = [(e["id"], e["status"], e["date"]) for e in decisions_index.entries(text)]
        self.assertEqual(rows, [("D-002", "действует (PR #2)", "2026-11-04"),
                                ("D-001", "заменено D-002", "2026-11-04"), ("—", "действует", "2026-08")])
        once = decisions_index.updated(text)
        self.assertIn("| D-002 | Второе | действует (PR #2) | 2026-11-04 |", once)
        self.assertEqual(decisions_index.updated(once), once)          # повторная сборка ничего не меняет


if __name__ == "__main__":
    unittest.main()
