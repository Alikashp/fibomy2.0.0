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

icons = yaml.safe_load((ROOT / "prompts/v2/icons.yaml").read_text(encoding="utf-8"))
names = icons["icons"] + icons.get("system", [])
OUT.mkdir(parents=True, exist_ok=True)
for name in names:
    img = Image.new("LA", (SIZE, SIZE), (0, 0))
    if name == "x" and name not in codes:
        # В шрифте шаблона нет «x» — рисуем крест линиями той же толщины, что у Tabler (2 из 24)
        d, a, b, w = ImageDraw.Draw(img), SIZE * 0.27, SIZE * 0.73, int(SIZE * 0.085)
        for p0, p1 in (((a, a), (b, b)), ((a, b), (b, a))):
            d.line([p0, p1], fill=(0, 255), width=w)
            for x, y in (p0, p1):
                d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=(0, 255))
        img.save(OUT / f"{name}.png", optimize=True)
        print(name)
        continue
    ImageDraw.Draw(img).text((SIZE / 2, SIZE / 2), chr(codes[name]), font=pil_font, fill=(0, 255), anchor="mm")
    img.save(OUT / f"{name}.png", optimize=True)
    print(name)
