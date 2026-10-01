"""
Образцы тем для docs/design/wireframes/00_themes.png: токены цветов, карточка с
числом, полоса primary, текст, позитив/негатив, столбцы и кольцо цветами chart_1…6.
Источник — themes/*.yaml (те же файлы, что читает рендерер).

Запуск из корня: python scripts/draw_themes.py  (нужен Pillow)
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core.models.theme import all_theme_ids, load_theme  # noqa: E402

FONT = ROOT / "fonts" / "LiberationSans-Regular.ttf"
FONT_B = ROOT / "fonts" / "LiberationSans-Bold.ttf"
PANEL_W, PANEL_H, HEAD = 480, 640, 34
ORDER = ["graphite_light", "graphite_dark", "azure_coral", "fresh_green"]


def f(size, bold=False):
    return ImageFont.truetype(str(FONT_B if bold else FONT), size)


def panel(theme) -> Image.Image:
    c = theme.colors
    img = Image.new("RGB", (PANEL_W, PANEL_H), c["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, PANEL_W - 1, PANEL_H - 1], outline="#9AA3AF")
    y = 12
    tokens = list(c.items())
    for name, value in tokens:
        d.rectangle([12, y, 40, y + 22], fill=value, outline="#9AA3AF")
        d.text((48, y + 3), f"{name} {value}", font=f(12), fill=c["text"])
        y += 28
    for i, value in enumerate(theme.chart, start=1):
        d.rectangle([12, y, 40, y + 22], fill=value, outline="#9AA3AF")
        d.text((48, y + 3), f"chart_{i} {value}", font=f(12), fill=c["text"])
        y += 28
    x0 = 220
    d.rounded_rectangle([x0, 12, PANEL_W - 12, 132], radius=8, fill=c["surface"])
    d.text((x0 + 14, 22), "47,3", font=f(40, True), fill=c["primary"])
    d.text((x0 + 14, 74), "млн руб.", font=f(15, True), fill=c["text"])
    d.text((x0 + 14, 100), "выручка за квартал", font=f(13), fill=c["text_muted"])
    d.rectangle([x0, 146, PANEL_W - 12, 206], fill=c["primary"])
    d.text((x0 + 14, 164), "Нужно решение", font=f(18, True), fill=c["on_primary"])
    d.text((x0, 220), "Заголовок-вывод", font=f(19, True), fill=c["text"])
    d.text((x0, 248), "Основной текст пункта", font=f(14), fill=c["text"])
    d.text((x0, 270), "Подпись, приглушённый", font=f(14), fill=c["text_muted"])
    d.text((x0, 292), "«", font=f(26, True), fill=c["accent"])
    d.text((x0 + 22, 298), "акцент", font=f(14, True), fill=c["accent"])
    d.text((x0 + 90, 298), "+41%", font=f(14, True), fill=c["positive"])
    d.text((x0 + 140, 298), "−6%", font=f(14, True), fill=c["negative"])
    # столбцы
    base, bw, gap = 440, 28, 10
    heights = [60, 95, 45, 110, 80, 55]
    d.line([x0, base, PANEL_W - 12, base], fill=c["border"], width=2)
    for i, (h, col) in enumerate(zip(heights, theme.chart)):
        bx = x0 + 6 + i * (bw + gap)
        d.rectangle([bx, base - h, bx + bw, base], fill=col)
    # кольцо
    cx, cy, r = x0 + 70, 540, 70
    shares = [46, 27, 18, 9]
    start = -90.0
    for share, col in zip(shares, theme.chart):
        end = start + 360 * share / 100
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start, end, fill=col)
        start = end
    d.ellipse([cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6], fill=c["bg"])
    for i, (share, col) in enumerate(zip(shares, theme.chart)):
        ly = 490 + i * 26
        d.rectangle([cx + r + 18, ly, cx + r + 32, ly + 14], fill=col)
        d.text((cx + r + 40, ly - 1), f"{share}%", font=f(13), fill=c["text"])
    return img


def main():
    ids = [t for t in ORDER if t in all_theme_ids()] + [t for t in all_theme_ids() if t not in ORDER]
    cols = 2
    rows = math.ceil(len(ids) / cols)
    out = Image.new("RGB", (cols * PANEL_W, rows * (PANEL_H + HEAD)), "#FFFFFF")
    d = ImageDraw.Draw(out)
    for i, tid in enumerate(ids):
        theme = load_theme(tid)
        x, y = (i % cols) * PANEL_W, (i // cols) * (PANEL_H + HEAD)
        label = f"{tid} — {theme.name['ru']}" + ("" if theme.enabled else "  (в боте с сессии 3)")
        d.text((x + 10, y + 8), label, font=f(16, True), fill="#111111")
        out.paste(panel(theme), (x, y + HEAD))
    path = ROOT / "docs/design/wireframes/00_themes.png"
    out.save(path, optimize=True)
    print(path)


if __name__ == "__main__":
    main()
