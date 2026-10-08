"""generate_deck: порядок этапов, дедлайн, деградация (03_ARCHITECTURE.md, 3, 4, 6).

INGEST → OUTLINE → SELECT → CONTENT ∥ IMAGES → FIT → RENDER → CONVERT.
DELIVER (отправка файлов) делает адаптер: бот / API. Здесь же — запись
результата в decks (run_deck).

Картинки (сессия 4, D-067): ИИ-картинки SiliconFlow параллельно с CONTENT, не больше
per_deck на колоду (титул + слайды с image_query плана); не успела — вариант без
картинки. CONVERT упал — PPTX отдаётся без PDF (отдельная задача PDF — сессия 5).
Материал: INGEST → SourceDigest с datasets, диаграммы из таблиц кодом, проверка чисел
по источнику после CONTENT.
"""

import asyncio
import copy
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from config import settings
from core import i18n
from core import images as IMG
from core.checks.facts import slide_errors, source_numbers, strip_unknown
from core.content.fill import fallback_content, fill_slide, system_prompt, FALLBACK_VARIANT
from core.ingest import IngestError, ingest
from core.planning.datasets import legend, related_columns
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
from core.models.layout import load_layouts
from core.selection.select import SlideNeed, reselect_comparison, select_deck, without_image
from logging_setup import stage as log_stage

logger = logging.getLogger(__name__)

ENGINE_VERSION = "core-2026-10-08"
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


def _has_digits(slide: Slide) -> bool:
    if slide.data:
        return True
    parts = [slide.title]

    def walk(value):
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k not in ("source_ref", "icon", "status", "tag"):
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(slide.content)
    return any(ch.isdigit() for ch in " ".join(parts))


def _apply_chart_content(slide: Slide, language: str) -> None:
    """Короткие подписи категорий и подпись оси от модели → снимок данных; легенда кольца."""
    data = slide.data
    if not data:
        return
    short = {c.get("row"): c.get("label") for c in (slide.content.get("category_labels") or [])
             if c.get("row") and c.get("label")}
    for cat in data["categories"]:
        if short.get(cat["row"]):
            cat["label"] = short[cat["row"]]
    if slide.content.get("value_axis_title"):
        data["value_axis_title"] = slide.content["value_axis_title"]
    if slide.kind == "chart_share":
        slide.content["legend"] = legend(data, language)


def deck_title(topic: str) -> str:
    """Тема пользователя на титуле дословно, но с заглавной первой буквой (требование владельца)."""
    topic = topic.strip()
    return topic[:1].upper() + topic[1:]


def image_kinds() -> set[str]:
    return {s.kind for s in load_layouts().values() if s.image_slot and s.kind != "title"}


def plan_images(request: DeckRequest, outline, plan) -> dict[int, str]:
    """Каким слайдам колоды нужна картинка → {номер слайда: запрос}. Титул — первым (запрос
    deck.cover_image_query или первый image_query плана), затем слайды с image_query, которые
    умеют показывать картинку (утверждение, пункты 3–4), не подряд и не слайд-запрос."""
    if not request.images or not IMG.enabled():
        return {}
    budget, slots = IMG.per_deck(), {}
    cover = outline.deck.cover_image_query or next((p.image_query for p in plan if p.image_query), None)
    if cover and budget > 0:
        slots[1], budget = cover, budget - 1
    kinds, last = image_kinds(), 1
    for i, p in enumerate(plan, start=2):
        if budget <= 0:
            break
        if not p.image_query or p.kind not in kinds or p.role == "ask" or i - last < 2:
            continue
        if p.kind == "bullets" and not 3 <= (p.items_planned or 0) <= 4:
            continue
        slots[i], budget, last = p.image_query, budget - 1, i
    return slots


async def generate_deck(deck_id: str, request: DeckRequest, progress: Progress | None = None,
                        client: LLMClient | None = None, material: bytes | None = None) -> DeckResult:
    """material — байты присланного файла или текста (адаптер забирает их из uploads)."""
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
        try:
            digest = await ingest(request, material)
        except IngestError as e:
            raise DeckError(e.code, str(e)) from e

    # 2 OUTLINE
    await progress("outline")
    with log_stage("outline", timings):
        try:
            outline, plan, datas = await plan_deck(request, digest, client, usage, degrade("outline"))
        except LLMError as e:
            raise DeckError("OUTLINE_FAILED", str(e)) from e

    # 3 SELECT: титул + план + финал; картинки — тем слайдам, которым их выделил план картинок
    title = deck_title(request.input.topic)
    image_slots = plan_images(request, outline, plan)
    with log_stage("select", timings):
        total = len(plan) + 2
        needs = [SlideNeed(slide_id(1), "title", "other", title=title, has_image=1 in image_slots)]
        for i, (p, data) in enumerate(zip(plan, datas), 2):
            needs.append(SlideNeed(
                slide_id(i), p.kind, p.role, p.items_planned, p.title, has_image=i in image_slots,
                points=len(data["categories"]) if data else None, series=len(data["series"]) if data else None,
                axis=data.get("axis") if data else None, share_sum=data.get("share_sum") if data else None))
        needs.append(SlideNeed(slide_id(total), "closing", "other"))
        choices = select_deck(needs, seed)
        for need, choice in zip(needs, choices):
            if choice.reason:
                degradations.append(("select", need.slide_id, choice.reason))

    # 5 IMAGES — параллельно с CONTENT: задачи стартуют сейчас, результат забирается после CONTENT
    layouts = load_layouts()
    jobs = []
    for i, (need, choice) in enumerate(zip(needs, choices), start=1):
        layout = layouts[choice.variant]
        if i in image_slots and layout.image_slot:
            box = next(e.box for e in layout.elements() if e.type == "image")
            jobs.append(IMG.ImageJob(need.slide_id, image_slots[i], box.w / box.h))
    images_started = time.monotonic()
    image_tasks = IMG.start(jobs, request.theme_id, request.language, seed) if jobs else {}

    author = request.author.line if request.author else None
    slides: list[Slide] = [Slide(
        id=slide_id(1), index=1, kind="title", variant=choices[0].variant, title=title,
        content={"subtitle": outline.deck.subtitle, "author": author},
    )]

    # 4 CONTENT — параллельно, до дедлайна этапа
    await progress("content", done=0, total=len(plan))
    system = system_prompt(request, digest, outline.deck.subtitle, plan)
    done_count = 0

    def chart_data(data: dict | None, choice) -> dict | None:
        if not data or choice.kind not in ("chart_series", "chart_share"):
            return None
        data = copy.deepcopy(data)
        data["chart"] = {"chart_series.line_chart": "line", "chart_share.donut": "donut"}.get(choice.variant, "column")
        return data

    slide_datas = [chart_data(d, c) for d, c in zip(datas, choices[1:-1])]

    async def one(i: int, planned, choice, data):
        nonlocal done_count
        result = await fill_slide(client, usage, system, planned, choice.kind, choice.variant, choice.items,
                                  index=i, total=total, slide_id=slide_id(i), data=data, language=request.language,
                                  related=related_columns(data, digest) if data else None)
        done_count += 1
        await progress("content", done=done_count, total=len(plan))
        return result

    semaphore = asyncio.Semaphore(max(1, settings.content_concurrency))

    async def limited(*args):
        async with semaphore:
            return await one(*args)

    with log_stage("content", timings):
        tasks = [asyncio.create_task(limited(i, p, c, d))
                 for i, (p, c, d) in enumerate(zip(plan, choices[1:-1], slide_datas), 2)]
        budget = max(5.0, deadline - time.monotonic() - RESERVE_SECONDS)
        finished, pending = await asyncio.wait(tasks, timeout=budget)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    for i, (planned, choice, task, data) in enumerate(zip(plan, choices[1:-1], tasks, slide_datas), 2):
        kind, variant = choice.kind, choice.variant
        if task in finished and not task.cancelled() and task.exception() is None:
            content, variant, reason = task.result()
        else:
            if task in finished and task.exception() is not None:
                logger.error("CONTENT task crashed", exc_info=task.exception(), extra={"slide_id": slide_id(i)})
            content, variant, reason = fallback_content(planned), FALLBACK_VARIANT, "content_deadline"
        if variant == FALLBACK_VARIANT and kind != "statement":
            kind, data = "statement", None
        if reason:
            degradations.append(("content", slide_id(i), reason))
        if kind == "comparison":
            variant = reselect_comparison(variant, content.get("polarity"))
        slide = Slide(id=slide_id(i), index=i, kind=kind, role=planned.role, variant=variant,
                      plan=planned, title=planned.title, content=content, data=data)
        _apply_chart_content(slide, request.language)
        slides.append(slide)

    # 5 IMAGES: ждём не дольше таймаута картинки от старта; не успела — вариант без картинки
    image_files: dict[str, bytes] = {}
    assets: dict[str, dict] = {}
    if image_tasks:
        with log_stage("images", timings):
            image_files, assets = await _collect_images(image_tasks, images_started, slides, needs, choices, seed,
                                                        usage, degradations)

    # 6a FIT: числа — из источника (режим «по материалу», ТЗ 3.3.3)
    if request.mode == "material":
        with log_stage("facts", timings):
            await _check_facts(slides[1:], digest, request, client, usage, system, total, choices, degradations,
                               deadline)

    slides.append(Slide(
        id=slide_id(total), index=total, kind="closing", variant=choices[-1].variant,
        title=i18n.text(i18n.THANKS, request.language),
        content={"title": i18n.text(i18n.THANKS, request.language), "body": None, "author": author},
        notes=i18n.text(i18n.IMAGES_AI, request.language) if assets else "",
    ))

    # Сноска у слайдов с числами «по теме» — оценка (ТЗ 3.3.3). По материалу сноски нет:
    # «по данным: <файл>» убрана по решению владельца (сессия 4)
    if request.mode == "topic":
        note = i18n.text(i18n.FOOTNOTE_ESTIMATE, request.language)
        for s in slides[1:-1]:
            if _has_digits(s):
                s.footnote = note

    spec = DeckSpec(
        id=deck_id,
        meta=Meta(
            title=title, subtitle=outline.deck.subtitle, language=request.language,
            presentation_type=request.presentation_type, audience=request.audience, mode=request.mode,
            source_mode=request.source_mode, genre=outline.analysis.genre, theme_id=request.theme_id,
            seed=seed, image_mode="ai" if assets else "none", watermark=request.watermark,
            author=request.author.model_dump() if request.author else None,
            slides_requested=request.total_slides,
            versions=Versions(engine=ENGINE_VERSION, prompts=P.version(), layouts=layouts_version(),
                              themes=_theme_version(request.theme_id)),
        ),
        slides=slides,
        assets=assets,
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
            pptx = render_pptx(spec, watermark=False, images=image_files)
            pptx_for_pdf = render_pptx(spec, watermark=True, images=image_files) if request.watermark else pptx
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
    if digest.source and digest.source.truncated:
        warnings.append(f"truncated:{digest.source.chars_used}/{digest.source.chars_total}")
    timings["total"] = int((time.monotonic() - started) * 1000)
    logger.info("Deck generated", extra={
        "slides": len(spec.slides), "seed": seed, "degradations": len(spec.degradations),
        "cost_rub": usage.cost_rub, "durations_ms": timings,
    })
    return DeckResult(spec=spec, pptx=pptx, pdf=pdf, durations_ms=timings, usage=usage, warnings=warnings)


async def _collect_images(tasks: dict, started: float, slides: list[Slide], needs, choices, seed: int,
                          usage: DeckUsage, degradations: list) -> tuple[dict[str, bytes], dict[str, dict]]:
    """Результаты картинок → файлы и DeckSpec.assets; слайды без картинки — вариант без неё."""
    timeout = float(IMG.config()["timeout_seconds"]) + 1
    pending = [t for t in tasks.values() if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=max(0.0, started + timeout - time.monotonic()))
    files, assets = {}, {}
    used: dict[str, int] = {}
    for s in slides:
        used[s.variant] = used.get(s.variant, 0) + 1
    stage = usage.stage("images")
    by_id = {s.id: s for s in slides}
    for sid, task in tasks.items():
        result = task.result() if task.done() and not task.cancelled() and task.exception() is None else None
        if not task.done():
            task.cancel()
        slide = by_id.get(sid)
        if slide is None:
            continue
        stage.calls += 1
        stage.models.add(IMG.model_id())
        if result and result.data:
            stage.cost_rub = (stage.cost_rub or 0) + result.cost_rub
            files[result.asset_id] = result.data
            assets[result.asset_id] = {"provider": IMG.PROVIDER, "model": IMG.model_id(), "prompt": result.prompt,
                                       "ms": result.ms, "cost_rub": result.cost_rub}
            slide.image = result.asset_id
            continue
        if result is None:
            # не завершилась за таймаут — цена не списывается (картинку не получили)
            reason = "timeout"
        else:
            reason = result.error or "error"
        i = slide.index - 1
        alt = without_image(choices[i], needs[i], seed, used)
        degradations.append(("images", sid, f"image_missing:{reason}"[:200]))
        slide.variant = alt.variant
    return files, assets


async def _check_facts(slides: list[Slide], digest: SourceDigest, request: DeckRequest, client: LLMClient,
                       usage: DeckUsage, system: str, total: int, choices, degradations: list, deadline: float) -> None:
    """Числа не из источника: 1 перегенерация слайда с перечнем ошибок, затем — числа убираются,
    metrics без чисел становятся утверждением из плана (03_ARCHITECTURE.md, 6)."""
    known = source_numbers(digest)
    targets = [(s, slide_errors(s, digest, known)) for s in slides if s.kind not in ("closing",) and s.plan]
    targets = [(s, e) for s, e in targets if e]
    if not targets:
        return

    async def fix(slide: Slide, errors: list[str]):
        if deadline - time.monotonic() < RESERVE_SECONDS + 10:
            return None
        extra = P.render("fit/fix_facts.txt", errors="\n".join(f"- {e}" for e in errors))
        choice = choices[slide.index - 1]
        content, variant, reason = await fill_slide(
            client, usage, system, slide.plan, slide.kind, slide.variant, choice.items,
            index=slide.index, total=total, slide_id=slide.id, data=slide.data, extra=extra,
            language=request.language, stage="fit", attempts=1,
            related=related_columns(slide.data, digest) if slide.data else None)
        return None if reason else content

    results = await asyncio.gather(*(fix(s, e) for s, e in targets), return_exceptions=True)
    for (slide, errors), fixed in zip(targets, results):
        if isinstance(fixed, dict):
            slide.content = fixed
            _apply_chart_content(slide, request.language)
        left = slide_errors(slide, digest, known)
        if not left:
            degradations.append(("fit", slide.id, "facts_fixed"))
            continue
        strip_unknown(slide, digest, known)
        degradations.append(("fit", slide.id, "facts_stripped:" + "; ".join(left)[:200]))
        if slide.kind == "metrics" and len(slide.content.get("items") or []) < 2:
            slide.kind, slide.variant, slide.data = "statement", FALLBACK_VARIANT, None
            slide.content = fallback_content(slide.plan)
            degradations.append(("fit", slide.id, "metrics->statement"))
