"""generate_deck: порядок этапов, дедлайн, деградация (03_ARCHITECTURE.md, 3, 4, 6).

INGEST → OUTLINE → SELECT → CONTENT (параллельно) → FIT → RENDER → CONVERT.
DELIVER (отправка файлов) делает адаптер: бот / API. Здесь же — запись
результата в decks (run_deck).

Сессия 1: только режим «по теме» (материал — сессия 2), без картинок
(сессия 4); CONVERT упал — PPTX отдаётся без PDF (отдельная задача PDF — сессия 4).
"""

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from config import settings
from core import i18n
from core.content.fill import fallback_content, fill_slide, system_prompt, FALLBACK_VARIANT
from core.fitting.fitter import fit_deck
from core.llm import prompts as P
from core.llm.client import DeckUsage, LLMClient, LLMError, get_client
from core.models.deck import DeckSpec, Meta, Slide, Versions
from core.models.digest import SourceDigest
from core.models.ids import slide_id
from core.models.layout import layouts_version
from core.models.request import DeckRequest
from core.models.theme import load_theme
from core.paths import THEMES_DIR
from core.planning.outline import plan_deck
from core.render.pdf.convert import ConvertError, pptx_to_pdf
from core.render.pptx.renderer import render_pptx
from core.selection.select import SlideNeed, select_deck
from logging_setup import stage as log_stage

logger = logging.getLogger(__name__)

ENGINE_VERSION = "core-2026-10-01"
RESERVE_SECONDS = 25        # RENDER + CONVERT + DELIVER после CONTENT (03_ARCHITECTURE.md, 4.2)

Progress = Callable[..., Awaitable[None]]


class DeckError(RuntimeError):
    """Колода не собралась; code — из справочника 04_CONTRACTS.md, 7.3."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass
class DeckResult:
    spec: DeckSpec
    pptx: bytes
    pdf: bytes | None
    durations_ms: dict = field(default_factory=dict)
    usage: DeckUsage = field(default_factory=DeckUsage)
    warnings: list[str] = field(default_factory=list)


async def _noop_progress(stage: str, **_) -> None:
    return None


def _theme_version(theme_id: str) -> str:
    raw = (THEMES_DIR / f"{theme_id}.yaml").read_bytes()
    return f"{theme_id}-{hashlib.sha1(raw).hexdigest()[:8]}"


def ingest(request: DeckRequest) -> SourceDigest:
    if request.mode == "topic":
        return SourceDigest.for_topic(request.input.topic)
    # Материал — сессия 2; до неё бот отправляет такие запросы в старый движок (D-038)
    raise DeckError("BAD_REQUEST", "material input is not supported by the new engine yet")


def _has_digits(slide: Slide) -> bool:
    parts = [slide.title]
    for value in slide.content.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(v) for item in value for v in (item.values() if isinstance(item, dict) else [item]))
    return any(ch.isdigit() for ch in " ".join(parts))


async def generate_deck(deck_id: str, request: DeckRequest, progress: Progress | None = None,
                        client: LLMClient | None = None) -> DeckResult:
    progress = progress or _noop_progress
    client = client or get_client()
    usage = DeckUsage()
    timings: dict[str, int] = {}
    started = time.monotonic()
    deadline = started + settings.deck_deadline_seconds
    seed = request.seed if request.seed is not None else random.SystemRandom().randrange(1, 2 ** 31)
    degradations: list[tuple[str, str | None, str]] = []

    def degrade(stage: str, slide: str | None = None):
        return lambda reason: degradations.append((stage, slide, reason))

    # 1 INGEST
    await progress("ingest")
    with log_stage("ingest", timings):
        digest = ingest(request)

    # 2 OUTLINE
    await progress("outline")
    with log_stage("outline", timings):
        try:
            outline, plan = await plan_deck(request, digest, client, usage, degrade("outline"))
        except LLMError as e:
            raise DeckError("OUTLINE_FAILED", str(e)) from e

    # 3 SELECT: титул + план + финал
    with log_stage("select", timings):
        total = len(plan) + 2
        needs = [SlideNeed(slide_id(1), "title", "other", title=request.input.topic)]
        needs += [SlideNeed(slide_id(i), p.kind, p.role, p.items_planned, p.title) for i, p in enumerate(plan, 2)]
        needs.append(SlideNeed(slide_id(total), "closing", "other"))
        choices = select_deck(needs, seed)
        for need, choice in zip(needs, choices):
            if choice.reason:
                degradations.append(("select", need.slide_id, choice.reason))

    author = request.author.line if request.author else None
    slides: list[Slide] = [Slide(
        id=slide_id(1), index=1, kind="title", variant=choices[0].variant, title=request.input.topic,
        content={"subtitle": outline.deck.subtitle, "author": author},
    )]

    # 4 CONTENT — параллельно, до дедлайна этапа
    await progress("content", done=0, total=len(plan))
    system = system_prompt(request, digest, outline.deck.subtitle, plan)
    done_count = 0

    async def one(i: int, planned, choice):
        nonlocal done_count
        result = await fill_slide(client, usage, system, planned, choice.kind, choice.variant, choice.items,
                                  index=i, total=total, slide_id=slide_id(i))
        done_count += 1
        await progress("content", done=done_count, total=len(plan))
        return result

    semaphore = asyncio.Semaphore(max(1, settings.content_concurrency))

    async def limited(*args):
        async with semaphore:
            return await one(*args)

    with log_stage("content", timings):
        tasks = [asyncio.create_task(limited(i, p, c)) for i, (p, c) in enumerate(zip(plan, choices[1:-1]), 2)]
        budget = max(5.0, deadline - time.monotonic() - RESERVE_SECONDS)
        finished, pending = await asyncio.wait(tasks, timeout=budget)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    for i, (planned, choice, task) in enumerate(zip(plan, choices[1:-1], tasks), 2):
        kind, variant = choice.kind, choice.variant
        if task in finished and not task.cancelled() and task.exception() is None:
            content, variant, reason = task.result()
        else:
            if task in finished and task.exception() is not None:
                logger.error("CONTENT task crashed", exc_info=task.exception(), extra={"slide_id": slide_id(i)})
            content, variant, reason = fallback_content(planned), FALLBACK_VARIANT, "content_deadline"
        if variant == FALLBACK_VARIANT and kind != "statement":
            kind = "statement"
        if reason:
            degradations.append(("content", slide_id(i), reason))
        slides.append(Slide(id=slide_id(i), index=i, kind=kind, role=planned.role, variant=variant,
                            plan=planned, title=planned.title, content=content))

    slides.append(Slide(
        id=slide_id(total), index=total, kind="closing", variant=choices[-1].variant,
        title=i18n.text(i18n.THANKS, request.language),
        content={"title": i18n.text(i18n.THANKS, request.language), "body": None, "author": author},
    ))

    # Сноска «Оценочные данные…» у слайдов с числами «по теме» (ТЗ 3.3.3)
    if request.mode == "topic":
        for s in slides[1:-1]:
            if _has_digits(s):
                s.footnote = i18n.text(i18n.FOOTNOTE_ESTIMATE, request.language)

    spec = DeckSpec(
        id=deck_id,
        meta=Meta(
            title=request.input.topic, subtitle=outline.deck.subtitle, language=request.language,
            presentation_type=request.presentation_type, audience=request.audience, mode=request.mode,
            source_mode=request.source_mode, genre=outline.analysis.genre, theme_id=request.theme_id,
            seed=seed, image_mode="none", watermark=request.watermark,
            author=request.author.model_dump() if request.author else None,
            slides_requested=request.total_slides,
            versions=Versions(engine=ENGINE_VERSION, prompts=P.version(), layouts=layouts_version(),
                              themes=_theme_version(request.theme_id)),
        ),
        slides=slides,
    )
    for stage_name, sid, reason in degradations:
        spec.degrade(stage_name, reason, sid)

    # 6 FIT
    theme = load_theme(request.theme_id)
    with log_stage("fit", timings):
        fit_deck(spec, theme)

    # 7 RENDER
    await progress("render")
    with log_stage("render", timings):
        try:
            pptx = render_pptx(spec, watermark=False)
            pptx_for_pdf = render_pptx(spec, watermark=True) if request.watermark else pptx
        except Exception as e:
            logger.exception("RENDER failed")
            raise DeckError("RENDER_FAILED", str(e)) from e

    # 8 CONVERT
    await progress("convert")
    pdf = None
    with log_stage("convert", timings):
        try:
            pdf = await pptx_to_pdf(pptx_for_pdf, max_parallel=settings.libreoffice_max_parallel)
        except ConvertError as e:
            logger.error("CONVERT failed — PPTX only", extra={"error": str(e)})
            spec.degrade("convert", str(e)[:200])

    warnings = []
    if request.slides_count and len(spec.slides) < request.slides_count:
        warnings.append(f"slides_short:{len(spec.slides)}/{request.slides_count}")
    timings["total"] = int((time.monotonic() - started) * 1000)
    logger.info("Deck generated", extra={
        "slides": len(spec.slides), "seed": seed, "degradations": len(spec.degradations),
        "cost_rub": usage.cost_rub, "durations_ms": timings,
    })
    return DeckResult(spec=spec, pptx=pptx, pdf=pdf, durations_ms=timings, usage=usage, warnings=warnings)
