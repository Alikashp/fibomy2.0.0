"""Превью тем для GET /v1/themes (D-077): есть у каждой включённой темы и собраны из текущих YAML."""
import importlib.util
import json
import unittest

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи

from core.models.theme import enabled_theme_ids
from core.paths import ROOT

DIR = ROOT / "assets" / "theme_previews"


def _script():
    spec = importlib.util.spec_from_file_location("build_theme_previews", ROOT / "scripts" / "build_theme_previews.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Previews(unittest.TestCase):

    def test_every_theme_has_fresh_preview(self):
        manifest = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
        script = _script()
        for theme_id in enabled_theme_ids():
            png = DIR / f"{theme_id}.png"
            self.assertTrue(png.is_file(), theme_id)
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG"), theme_id)
            self.assertEqual(manifest[theme_id]["inputs"], script.inputs_hash(theme_id),
                             f"{theme_id}: тема или макеты превью изменились — пересоберите превью: "
                             "python scripts/build_theme_previews.py")


if __name__ == "__main__":
    unittest.main()
