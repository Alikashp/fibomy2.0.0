"""
Общие помощники спайка PPTX (фаза 0, ТЗ раздел 9).

- Логическая сетка 1920×1080 → EMU (1 единица = 6350 EMU, ТЗ 3.4.2).
- Пробная тема: токены цветов и шрифтов, как их описывает ТЗ 3.5.
- Иконки: PNG из того же шрифта Tabler Icons (MIT), что уже вшит в шаблон
  doklad, — без новых внешних ассетов.
- Конвертация PPTX → PDF через LibreOffice headless с замером времени.

Это код спайка, а не прод: в src/ ничего не импортируется.
"""

from __future__ import annotations

import base64
import io
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from pptx.dml.color import RGBColor
from pptx.util import Emu

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
OUT = ROOT / "out"

EMU_PER_UNIT = 6350
SLIDE_W, SLIDE_H = 1920, 1080  # логическая сетка


def u(value: float) -> Emu:
    """Единицы сетки 1920×1080 → EMU."""
    return Emu(int(round(value * EMU_PER_UNIT)))


def rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#"))


# Пробная светлая деловая тема (ТЗ 3.5). Значения — черновые, для спайка.
THEME = {
    "bg": "#FFFFFF",
    "surface": "#F3F5F8",
    "surface_alt": "#E6EAF0",
    "primary": "#1F4E79",
    "accent": "#E07A1F",
    "text": "#1B1F24",
    "text_muted": "#5B6570",
    "on_primary": "#FFFFFF",
    "border": "#D5DBE3",
    "chart": ["#1F4E79", "#E07A1F", "#3C9D9B", "#8E6BBF", "#C94F4F", "#7A8B99"],
    "font_heading": "Arial",
    "font_body": "Arial",
    # типошкала, pt (ТЗ 3.5)
    "size": {"display": 60, "h1": 36, "h2": 26, "h3": 20, "body": 18, "small": 14, "min": 12},
}


# ── Иконки ──────────────────────────────────────────────────────────────────

_ICON_CSS_RE = re.compile(r'\.ti-([a-z-]+):before\{content:"\\([0-9a-f]+)"\}')
_WOFF2_RE = re.compile(r"data:font/woff2;base64,([A-Za-z0-9+/=]+)")


def _tabler_font_and_codes() -> tuple[bytes, dict[str, int]]:
    html = (REPO / "src/templates/doklad/template.html").read_text(encoding="utf-8")
    codes = {name: int(code, 16) for name, code in _ICON_CSS_RE.findall(html)}
    woff2 = base64.b64decode(_WOFF2_RE.search(html).group(1))
    from fontTools.ttLib import TTFont  # woff2 → ttf (нужен пакет brotli)

    font = TTFont(io.BytesIO(woff2))
    font.flavor = None
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue(), codes


def icon_png(name: str, color: str, size_px: int = 512) -> io.BytesIO:
    """PNG иконки Tabler с прозрачным фоном. ТЗ 3.8: иконка — картинка
    высокого разрешения (допустимо) или фигуры; веб-шрифт иконок — нельзя."""
    from PIL import Image, ImageDraw, ImageFont

    ttf, codes = _tabler_font_and_codes()
    font = ImageFont.truetype(io.BytesIO(ttf), int(size_px * 0.9))
    img = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((size_px / 2, size_px / 2), chr(codes[name]), font=font, fill=color, anchor="mm")
    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


# ── Конвертация ─────────────────────────────────────────────────────────────

def soffice_bin() -> str:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("LibreOffice не найден (soffice)")


def pptx_to_pdf(pptx: Path, out_dir: Path) -> tuple[Path, float]:
    """PPTX → PDF через LibreOffice headless. Возвращает путь и время, с.

    Отдельный профиль пользователя на каждый вызов — так же придётся делать
    в воркере при параллельных конвертациях (ТЗ 5)."""
    with tempfile.TemporaryDirectory() as profile:
        started = time.perf_counter()
        subprocess.run(
            [
                soffice_bin(), "--headless", "--norestore",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx),
            ],
            check=True, capture_output=True, timeout=120,
        )
        elapsed = time.perf_counter() - started
    return out_dir / (pptx.stem + ".pdf"), elapsed


def pdf_to_pngs(pdf: Path, out_dir: Path, width_px: int = 1280) -> list[Path]:
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            zoom = width_px / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            path = out_dir / f"{pdf.stem}-{i:02d}.png"
            pix.save(path)
            paths.append(path)
    return paths


def pdf_fonts(pdf: Path) -> dict[int, list[str]]:
    """Какие шрифты LibreOffice реально встроил в PDF — по страницам."""
    import pymupdf

    result = {}
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            names = sorted({f[3].split("+")[-1] for f in page.get_fonts()})
            result[i] = names
    return result
