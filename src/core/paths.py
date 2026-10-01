"""Пути к декларативным файлам движка в корне репозитория (в образе — /app)."""

import os
from pathlib import Path

# src/core/paths.py → корень репозитория
ROOT = Path(os.environ.get("FIBONACCI_ROOT") or Path(__file__).resolve().parents[2])

LAYOUTS_DIR = ROOT / "layouts"
THEMES_DIR = ROOT / "themes"
FONTS_DIR = ROOT / "fonts"
ICONS_DIR = ROOT / "assets" / "icons"
PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR") or ROOT / "prompts")
PROMPTS_V2_DIR = PROMPTS_DIR / "v2"
