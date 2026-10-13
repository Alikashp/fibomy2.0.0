"""PNG-превью тем для GET /v1/themes (preview_url) — из стресс-галереи, при сборке, не на лету (D-077).

Превью темы — титул и один содержательный слайд (карточки с пунктами) друг под другом, по 960×540.
Слайды собирает тот же путь, что галерея (tests/gallery: фикстуры, fitter, рендер, LibreOffice),
поэтому превью выглядит ровно как колода. Результат — assets/theme_previews/<id>.png и manifest.json
с хэшем входов (YAML темы, макеты превью, этот скрипт): юнит-тест падает, если тему поменяли,
а превью не пересобрали. CI-job «gallery» пересобирает превью и кладёт их в артефакт.

    python scripts/build_theme_previews.py [OUT_DIR]      # по умолчанию assets/theme_previews

Нужен LibreOffice. Без сети и LLM.
"""

import asyncio
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "gallery"))

import pypdfium2 as pdfium  # noqa: E402
from PIL import Image  # noqa: E402

PREVIEW_VARIANTS = ("title.cover_center", "bullets.cards_grid")
PREVIEW_TITLE = "Итоги года: что сделано и что дальше"
SLIDE_W, SLIDE_H, GAP = 960, 540, 16
OUT = ROOT / "assets" / "theme_previews"


def inputs_hash(theme_id: str) -> str:
    """Хэш входов превью: YAML темы, YAML макетов превью, этот скрипт."""
    h = hashlib.sha256()
    for path in [ROOT / "themes" / f"{theme_id}.yaml", Path(__file__).resolve(),
                 *(ROOT / "layouts" / v.split(".")[0] / f"{v.split('.')[1]}.yaml" for v in PREVIEW_VARIANTS)]:
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def preview_spec(theme_id: str):
    import fixtures as F
    from build_gallery import deck
    from core.models.deck import Slide
    from core.models.ids import slide_id
    from core.models.layout import load_layouts
    layouts = load_layouts()
    slides = []
    for i, variant in enumerate(PREVIEW_VARIANTS, start=1):
        title, content, _ = F.content_for(layouts[variant], "typical", seed=i * 7)
        if variant.startswith("title"):
            title, content = PREVIEW_TITLE, {"subtitle": "Результаты, выводы и планы на следующий год",
                                             "author": "Иван Петров · группа Б-21"}
        if variant == "bullets.cards_grid":
            title = "Три главных результата года"
            content = {"items": [
                {"heading": "Новые клиенты", "text": "Число клиентов выросло на 18 % за счёт онлайн-заказов.",
                 "icon": "users"},
                {"heading": "Скорость", "text": "Заявку обрабатываем за 2 часа вместо одного дня.", "icon": "clock"},
                {"heading": "Качество", "text": "Возвратов стало вдвое меньше после проверки на складе.",
                 "icon": "shield"}]}
        slides.append(Slide(id=slide_id(i), index=i, kind=layouts[variant].kind, variant=variant, title=title,
                            content=content))
    spec = deck(theme_id, slides, PREVIEW_TITLE)
    spec.meta.title = PREVIEW_TITLE
    return spec


async def build(theme_id: str) -> bytes:
    from core.fitting.fitter import fit_deck
    from core.models.theme import load_theme
    from core.render.pdf.convert import pptx_to_pdf
    from core.render.pptx.renderer import render_pptx
    spec = preview_spec(theme_id)
    fit_deck(spec, load_theme(theme_id))
    pdf = pdfium.PdfDocument(await pptx_to_pdf(render_pptx(spec), timeout=180))
    pages = [pdf[i].render(scale=SLIDE_W / pdf[i].get_width()).to_pil().convert("RGB").resize((SLIDE_W, SLIDE_H))
             for i in range(len(PREVIEW_VARIANTS))]
    sheet = Image.new("RGB", (SLIDE_W, SLIDE_H * len(pages) + GAP * (len(pages) - 1)), (255, 255, 255))
    for k, page in enumerate(pages):
        sheet.paste(page, (0, k * (SLIDE_H + GAP)))
    out = io.BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def main(out: Path) -> None:
    from core.models.theme import enabled_theme_ids
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for theme_id in enabled_theme_ids():
        png = await build(theme_id)
        (out / f"{theme_id}.png").write_bytes(png)
        manifest[theme_id] = {"inputs": inputs_hash(theme_id), "png": hashlib.sha256(png).hexdigest()[:16],
                              "bytes": len(png)}
        print(f"{theme_id}: {len(png) // 1024} КБ")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import logging
    logging.disable(logging.WARNING)
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT))
