"""
Детерминированная постобработка ответа модели — без вызовов LLM.

Здесь то, что ТЗ и разбор промптов (docs/PROMPT_REVIEW.md) требуют решать
кодом, а не текстом промпта:
- prepare_doklad_json      — до валидации: ряд больше 4 метрик не обрезается,
                             а уходит в bullets; source от модели выбрасывается;
- remap_layouts            — layout вне каталога доклада → ближайший из каталога;
- timelines_without_dates_to_bullets — таймлайн без дат рисуется списком;
- assign_sources           — metrics[].source и сноска «Оценочные данные…».
"""

import logging
import re

from schemas.presentation import (
    BulletPoint, ContentSourceType, PresentationSchema, Slide, SlideLayout, UserRequest,
)

logger = logging.getLogger(__name__)

# Шаблон доклада корректно рисует до 4 метрик (проверено рендером, см. D-015)
DOKLAD_MAX_METRICS = 4
MAX_BULLETS = 6  # Slide.bullets max_length

ESTIMATE_FOOTNOTE = "Оценочные данные — проверьте перед показом"

# Layout'ы, которые рисует templates/doklad/template.html (LAYOUT_CATALOG)
DOKLAD_LAYOUTS = frozenset({
    SlideLayout.TITLE, SlideLayout.BULLETS, SlideLayout.METRICS,
    SlideLayout.TWO_COLUMN, SlideLayout.DIAGRAM, SlideLayout.QUOTE,
    SlideLayout.TIMELINE, SlideLayout.IMAGE_FULL, SlideLayout.IMAGE_HERO,
    SlideLayout.CLOSING,
})

_DIGIT = re.compile(r"\d")


# ── До валидации ─────────────────────────────────────────────────────────────

def _metric_to_bullet(m: dict) -> dict:
    value = str(m.get("value") or "").strip()
    label = str(m.get("label") or "").strip()
    trend = str(m.get("trend") or "").strip()
    text = " — ".join(p for p in (value, label) if p)
    if trend:
        text += f" ({trend})"
    return {"text": text[:400]}


def prepare_doklad_json(data: dict) -> dict:
    """Сырой JSON доклада до PresentationSchema.

    - source у метрик выбрасываем: его проставляет код (assign_sources);
    - слайд, где метрик больше, чем умеет шаблон, превращаем в bullets по пункту
      на элемент — ряд не обрезается (ТЗ 4.8.3), а схема не роняет генерацию.
    """
    for slide in data.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        metrics = slide.get("metrics")
        if not isinstance(metrics, list):
            continue
        for m in metrics:
            if isinstance(m, dict):
                m.pop("source", None)
        if len(metrics) > DOKLAD_MAX_METRICS:
            bullets = [_metric_to_bullet(m) for m in metrics if isinstance(m, dict)]
            slide["bullets"] = _fit_bullets(bullets)
            slide["metrics"] = []
            if slide.get("layout") == SlideLayout.METRICS.value:
                slide["layout"] = SlideLayout.BULLETS.value
            logger.info(f"Slide #{slide.get('index')}: {len(metrics)} metrics → bullets (row is not truncated)")
    return data


def _fit_bullets(bullets: list[dict]) -> list[dict]:
    """Не больше MAX_BULLETS: хвост склеивается в последний пункт, а не теряется."""
    if len(bullets) <= MAX_BULLETS:
        return bullets
    head = bullets[:MAX_BULLETS - 1]
    tail = "; ".join(b["text"] for b in bullets[MAX_BULLETS - 1:])
    return head + [{"text": tail[:400]}]


# ── Layout вне каталога доклада ──────────────────────────────────────────────

def _text_bullets(texts: list[str]) -> list[BulletPoint]:
    return [BulletPoint(**b) for b in _fit_bullets([{"text": t[:400]} for t in texts if t and t.strip()])]


def _remap_slide(slide: Slide) -> Slide:
    update: dict = {}
    if slide.layout == SlideLayout.TEAM:
        texts = [
            f"{m.name} — {m.role}" + (f". {m.bio}" if m.bio else "")
            for m in slide.team_members
        ]
        update = {"layout": SlideLayout.BULLETS, "bullets": _text_bullets(texts), "team_members": []}
    elif slide.layout == SlideLayout.COMPETITION and slide.competition_table:
        table = slide.competition_table
        texts = [
            f"{f.name}: " + "; ".join(f"{k} — {v}" for k, v in f.values.items())
            for f in table.features
        ]
        update = {"layout": SlideLayout.BULLETS, "bullets": _text_bullets(texts), "competition_table": None}
    elif slide.metrics and len(slide.metrics) <= DOKLAD_MAX_METRICS:
        update = {"layout": SlideLayout.METRICS}
    elif slide.bullets:
        update = {"layout": SlideLayout.BULLETS}
    elif slide.body_text and slide.image_query:
        # текст + картинка (problem/solution питч-дека) — ближе всего image_hero:
        # картинка сверху, под ней заголовок и текст; без картинки — текстовый блок
        update = {"layout": SlideLayout.IMAGE_HERO}
    elif slide.body_text:
        update = {"layout": SlideLayout.BULLETS, "bullets": _text_bullets([slide.body_text])}
    else:
        update = {"layout": SlideLayout.BULLETS}

    # Текст, который новый layout не рисует, не теряем: body_text → подзаголовок или пункт
    new_layout = update["layout"]
    if (slide.body_text and new_layout in (SlideLayout.METRICS, SlideLayout.BULLETS)
            and "bullets" not in update):
        if not slide.subtitle and len(slide.body_text) <= 200:
            update["subtitle"] = slide.body_text
        elif new_layout == SlideLayout.BULLETS:
            update["bullets"] = _text_bullets([slide.body_text] + [b.text for b in slide.bullets])
    return slide.model_copy(update=update)


def remap_layouts(presentation: PresentationSchema) -> PresentationSchema:
    """layout вне каталога доклада (problem, solution, market, competition, team,
    why_now) — на ближайший из каталога. Раньше такой слайд рисовала запасная
    ветка шаблона (PROMPT_REVIEW, К9 и 10.3)."""
    slides = []
    for slide in presentation.slides:
        if slide.layout in DOKLAD_LAYOUTS:
            slides.append(slide)
            continue
        new = _remap_slide(slide)
        logger.info(f"Slide #{slide.index}: layout {slide.layout.value} is not in the doklad catalog → {new.layout.value}")
        slides.append(new)
    return presentation.model_copy(update={"slides": slides})


# ── Таймлайн без дат ─────────────────────────────────────────────────────────

def timelines_without_dates_to_bullets(presentation: PresentationSchema) -> PresentationSchema:
    """Если хотя бы у одного этапа нет даты — слайд рисуется списком (ТЗ 3.3.2:
    этапы без дат — это process, а не timeline). Этапы не теряются."""
    slides = []
    for slide in presentation.slides:
        items = slide.timeline_items
        if slide.layout != SlideLayout.TIMELINE or not items or all(i.date and i.date.strip() for i in items):
            slides.append(slide)
            continue
        bullets = []
        for item in items:
            head = " · ".join(p for p in ((item.date or "").strip(), item.title) if p)
            if item.description:
                bullets.append({"text": item.description, "subtitle": head[:80]})
            else:
                bullets.append({"text": head})
        slides.append(slide.model_copy(update={
            "layout": SlideLayout.BULLETS,
            "bullets": [BulletPoint(**b) for b in _fit_bullets(bullets)],
            "timeline_items": [],
        }))
        logger.info(f"Slide #{slide.index}: timeline without dates → bullets ({len(items)} items)")
    return presentation.model_copy(update={"slides": slides})


# ── Источники и сноски ───────────────────────────────────────────────────────

def source_label(request: UserRequest) -> str:
    if request.source_type == ContentSourceType.DOCUMENT:
        return f"по данным: {request.source_name}" if request.source_name else "по данным: документ пользователя"
    if request.source_type == ContentSourceType.URL:
        return "по данным: присланные ссылки"
    return "по данным: текст пользователя"


def _slide_has_numbers(slide: Slide) -> bool:
    parts = [slide.title, slide.subtitle, slide.body_text]
    parts += [b.text for b in slide.bullets] + [b.subtitle for b in slide.bullets]
    parts += [f"{m.value} {m.label} {m.trend or ''}" for m in slide.metrics]
    parts += [f"{i.date or ''} {i.title} {i.description or ''}" for i in slide.timeline_items]
    if slide.two_column:
        parts += [b.text for b in slide.two_column.left_bullets + slide.two_column.right_bullets]
        parts += [slide.two_column.left_text, slide.two_column.right_text]
    return any(p and _DIGIT.search(p) for p in parts)


def assign_sources(presentation: PresentationSchema, request: UserRequest) -> PresentationSchema:
    """metrics[].source модель не заполняет (ТЗ 3.3.3) — его ставит код:
    - по материалу — «по данным: <имя файла>»;
    - по теме — «Оценочные данные — проверьте перед показом», и та же сноска
      внизу каждого содержательного слайда, где есть число."""
    has_material = request.source_type != ContentSourceType.TOPIC
    label = source_label(request) if has_material else ESTIMATE_FOOTNOTE
    slides = []
    for slide in presentation.slides:
        update: dict = {}
        if slide.metrics:
            update["metrics"] = [m.model_copy(update={"source": label}) for m in slide.metrics]
        if (not has_material and slide.layout not in (SlideLayout.TITLE, SlideLayout.CLOSING)
                and _slide_has_numbers(slide)):
            update["footnote"] = ESTIMATE_FOOTNOTE
        slides.append(slide.model_copy(update=update) if update else slide)
    return presentation.model_copy(update={"slides": slides})
