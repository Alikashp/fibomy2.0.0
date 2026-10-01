"""
Собирает PNG-иконки фазы 1 в assets/icons/ из шрифта Tabler Icons (MIT).

Шрифт берётся из шаблона старого движка (src/templates/doklad/template.html,
base64 woff2) — тот же источник, что в спайке (spikes/common.py). После удаления
старого движка (сессия 5) PNG уже лежат в репозитории; для новых иконок шрифт
Tabler Icons нужно будет положить рядом и поправить FONT_SOURCE.

Каждый PNG — чёрная маска на прозрачном фоне, 512 px. Цвет темы подставляет
рендерер (core/render/pptx/icons.py), поэтому один файл годится для всех тем.

Запуск из корня: python scripts/build_icons.py  (нужны fonttools, brotli, Pillow)
"""
import base64
import io
import re
from pathlib import Path

import yaml
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT_SOURCE = ROOT / "src/templates/doklad/template.html"
OUT = ROOT / "assets/icons"
SIZE = 512

html = FONT_SOURCE.read_text(encoding="utf-8")
codes = {n: int(c, 16) for n, c in re.findall(r'\.ti-([a-z-]+):before\{content:"\\([0-9a-f]+)"\}', html)}
woff2 = base64.b64decode(re.search(r"data:font/woff2;base64,([A-Za-z0-9+/=]+)", html).group(1))
font = TTFont(io.BytesIO(woff2))
font.flavor = None
buf = io.BytesIO()
font.save(buf)
pil_font = ImageFont.truetype(io.BytesIO(buf.getvalue()), int(SIZE * 0.9))

names = yaml.safe_load((ROOT / "prompts/v2/icons.yaml").read_text(encoding="utf-8"))["icons"]
OUT.mkdir(parents=True, exist_ok=True)
for name in names:
    img = Image.new("LA", (SIZE, SIZE), (0, 0))
    ImageDraw.Draw(img).text((SIZE / 2, SIZE / 2), chr(codes[name]), font=pil_font, fill=(0, 255), anchor="mm")
    img.save(OUT / f"{name}.png", optimize=True)
    print(name)
