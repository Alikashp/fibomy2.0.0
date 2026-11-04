"""Документация не устаревает молча (D-080): пути в README, CLAUDE.md и docs/API.md
существуют, каждый роут API описан в docs/API.md, каждый документ есть в своей карте — scripts/check_docs.py."""
import sys
import unittest

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import check_docs  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
