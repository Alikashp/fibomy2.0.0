import json
import logging

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from schemas.presentation import (
    PresentationSchema, UserRequest, PresentationType, AudienceType,
    ContentVolume, ContentSourceType, Slide, SlideLayout, BulletPoint,
    _slide_has_required_content,
)
from generation.prompts import (
    PROMPTS_VERSION, load_enum_map, load_template, load_template_map, load_text, load_yaml,
)

logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    timeout=settings.openai_timeout_seconds,
)

# Все тексты промптов — в prompts/ (см. generation/prompts.py). Здесь только
# сборка: какой блок подставить в зависимости от запроса.

SYSTEM_PROMPT = load_text("system.txt")

# Раньше эти два блока приходили модели ВСЕГДА, при любом presentation_type —
# включая DOKLAD, где ни таблицы конкурентов, ни слайда объёма рынка не
# бывает. Теперь подставляются в SYSTEM_PROMPT только если реально выбранный
# structure_block (см. _pick_structure_block) содержит соответствующий layout
# — см. _competition_table_block()/_market_slide_block() ниже.

COMPETITION_TABLE_BLOCK = load_text("blocks_competition_table.txt")

MARKET_SLIDE_BLOCK = load_text("blocks_market_slide.txt")


def _competition_table_block(structure_block: str) -> str:
    return COMPETITION_TABLE_BLOCK if 'layout="competition"' in structure_block else ""


def _market_slide_block(structure_block: str) -> str:
    return MARKET_SLIDE_BLOCK if 'layout="market"' in structure_block else ""

USER_PROMPT_TEMPLATE = load_template("user.txt")

SOURCE_MATERIAL_BLOCK = load_template("source_material.txt")
EXTRA_INSTRUCTIONS_BLOCK = load_template("extra_instructions.txt")

# TYPE_CONTEXTS[DOKLAD] — только документированный дефолт: реальный текст для
# DOKLAD считается в _doklad_type_context() по request.source_type.
TYPE_CONTEXTS = load_enum_map("type_contexts.yaml", PresentationType)

# ── Жёсткая по-слайдовая структура для типов, где она уже задана ───────────────
# Для типов, которых здесь нет, используется GENERIC_STRUCTURE_FALLBACK —
# модель ориентируется на TYPE_CONTEXTS выше, но не тянет за собой форму
# питч-дека. Добавляйте сюда новые типы по мере готовности их структуры
# (см. Sprint 1, задача про conference/corp_report).

STRUCTURE_BLOCKS: dict[PresentationType, str] = {
    ptype: load_text(f"structures/{ptype.value}.txt")
    for ptype in (PresentationType.PITCH_DECK, PresentationType.CONFERENCE, PresentationType.CORP_REPORT)
}

# DOKLAD — единственный тип, где даже жёсткая по-слайдовая структура (не
# только TYPE_CONTEXTS) зависит от source_type, а не только от
# presentation_type: "доклад с нуля по теме" и "доклад по проделанной
# работе на основе присланного материала" — это структурно разные вещи
# (см. _pick_structure_block() ниже и template_engine.DOKLAD_TEMPLATE_BY_SOURCE,
# где та же развилка определяет ещё и выбор HTML-шаблона).

LAYOUT_CATALOG = load_text("doklad/layout_catalog.txt")

DOKLAD_NARRATIVE_TOPIC = load_text("doklad/narrative_topic.txt")

DOKLAD_NARRATIVE_TEXT = load_text("doklad/narrative_text.txt")

STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE: dict[ContentSourceType, str] = {
    ContentSourceType.TOPIC: LAYOUT_CATALOG + "\n\n" + DOKLAD_NARRATIVE_TOPIC,
    ContentSourceType.TEXT:  LAYOUT_CATALOG + "\n\n" + DOKLAD_NARRATIVE_TEXT,
}
STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE[ContentSourceType.DOCUMENT] = STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE[ContentSourceType.TEXT]
STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE[ContentSourceType.URL] = STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE[ContentSourceType.TEXT]


def _pick_structure_block(request: UserRequest) -> str:
    if request.presentation_type == PresentationType.DOKLAD:
        return STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE.get(
            request.source_type,
            STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE[ContentSourceType.TOPIC],
        )
    return STRUCTURE_BLOCKS.get(request.presentation_type, GENERIC_STRUCTURE_FALLBACK)


GENERIC_STRUCTURE_FALLBACK = load_text("structures/generic_fallback.txt")

AUDIENCE_CONTEXTS = load_enum_map("audience_contexts.yaml", AudienceType)

# Аудитории, для которых DOKLAD+topic-ветка дополнительно усиливает
# требование к простоте языка (сверх того, что уже даёт AUDIENCE_CONTEXTS
# само по себе) — см. _doklad_type_context().
_DOKLAD_SIMPLIFY_AUDIENCES = (AudienceType.SCHOOLKIDS, AudienceType.STUDENTS)
_DOKLAD_TYPE_CONTEXTS = load_yaml("doklad/type_contexts.yaml")


def _doklad_type_context(request: UserRequest) -> str:
    """
    TYPE_CONTEXTS для DOKLAD не статичен — ветка зависит от source_type
    (см. Sprint: "Задача 1. Новый тип DOKLAD с веткой по source_type").
    """
    if request.source_type == ContentSourceType.TOPIC:
        text = _DOKLAD_TYPE_CONTEXTS["topic"]
        if request.audience in _DOKLAD_SIMPLIFY_AUDIENCES:
            text += _DOKLAD_TYPE_CONTEXTS["topic_simplify_suffix"]
        return text

    # TEXT / DOCUMENT / URL — доклад по уже готовому материалу, не с нуля.
    return _DOKLAD_TYPE_CONTEXTS["material"]


VOLUME_INSTRUCTIONS: dict[ContentVolume, str] = load_enum_map("volume.yaml", ContentVolume)


_JSON_SCHEMA = load_text("json_schema.txt")


def _get_json_schema() -> str:
    return _JSON_SCHEMA


def _build_user_prompt(request: UserRequest) -> str:
    slide_count = request.slide_count_hint or _default_slide_count(request.presentation_type)
    extra_block = ""
    if request.extra_instructions:
        extra_block = EXTRA_INSTRUCTIONS_BLOCK.substitute(extra_instructions=request.extra_instructions)

    source_material_block = ""
    if request.source_type != ContentSourceType.TOPIC and request.raw_text:
        source_material_block = SOURCE_MATERIAL_BLOCK.substitute(raw_text=request.raw_text)

    if request.presentation_type == PresentationType.DOKLAD:
        type_context = _doklad_type_context(request)
    else:
        type_context = TYPE_CONTEXTS.get(request.presentation_type, "")

    return USER_PROMPT_TEMPLATE.substitute(
        topic=request.topic,
        presentation_type=request.presentation_type.value,
        audience=request.audience.value,
        language=request.language,
        slide_count_hint=slide_count,
        extra_instructions_block=extra_block,
        source_material_block=source_material_block,
        type_context=type_context,
        audience_context=AUDIENCE_CONTEXTS.get(request.audience, ""),
    )


# ── Точечное дозаполнение пустых слайдов ────────────────────────────────────
# Наблюдали в проде трижды подряд на одной и той же теме: модель ставит
# layout на слайд (чаще всего two_column), но не заполняет поле, которое
# этот layout реально рисует — слайд визуально пустой, кроме футера.
# Жёсткая валидация с retry (пробовали раньше) оказалась хуже: модель может
# СТАБИЛЬНО повторять один и тот же пустой слайд на всех 3 попытках подряд,
# и пользователь не получает вообще ничего. Вместо этого — один маленький
# точечный follow-up запрос ТОЛЬКО на недостающее поле конкретного слайда:
# дешевле и надёжнее полной перегенерации, а если и он не удастся —
# оставляем слайд как есть (тихо, без падения job'а).

_PATCH_SPECS = load_yaml("patch_instructions.yaml")
_LAYOUT_PATCH_INSTRUCTIONS: dict[SlideLayout, str] = {
    SlideLayout(key): spec["instruction"] for key, spec in _PATCH_SPECS.items()
}

_PATCH_JSON_EXAMPLES: dict[SlideLayout, str] = {
    SlideLayout(key): spec["example"] for key, spec in _PATCH_SPECS.items()
}
_PATCH_PROMPT = load_template_map("patch_slide.yaml")


# Layout'ы, для которых есть дешёвый локальный путь деградации в bullets
# ДО обращения к LLM — просто перекладываем то, что реально пришло в других
# полях, без выдумывания нового текста. Дешевле и быстрее второго вызова
# модели; используется, только если исходный layout всё равно не прошёл
# _slide_has_required_content (т.е. слайд и так уже "сломан").
_DEGRADABLE_TO_BULLETS = (
    SlideLayout.TWO_COLUMN, SlideLayout.METRICS, SlideLayout.DIAGRAM,
    SlideLayout.IMAGE_HERO, SlideLayout.TIMELINE,
)


def _locally_degrade_slide(slide: Slide) -> Slide | None:
    if slide.layout not in _DEGRADABLE_TO_BULLETS:
        return None

    bullets = list(slide.bullets)

    if slide.layout == SlideLayout.TWO_COLUMN and slide.two_column:
        tc = slide.two_column
        bullets.extend(tc.left_bullets)
        bullets.extend(tc.right_bullets)
        if tc.left_text and tc.left_text.strip():
            bullets.append(BulletPoint(text=tc.left_text.strip(), subtitle=tc.left_title))
        if tc.right_text and tc.right_text.strip():
            bullets.append(BulletPoint(text=tc.right_text.strip(), subtitle=tc.right_title))

    # metrics/diagram/image_hero/timeline не содержат других текстовых полей,
    # из которых можно честно собрать bullet — просто меняем layout на
    # bullets, и если реально нечего сливать, ниже это увидит
    # _slide_has_required_content и уйдёт на LLM-дозапрос уже как bullets.

    degraded = slide.model_copy(update={"layout": SlideLayout.BULLETS, "bullets": bullets})
    return degraded


async def _patch_one_slide(request: UserRequest, slide: Slide) -> Slide:
    instruction = _LAYOUT_PATCH_INSTRUCTIONS.get(slide.layout)
    example = _PATCH_JSON_EXAMPLES.get(slide.layout)
    if instruction is None or example is None:
        raise ValueError(f"no patch instructions for layout={slide.layout.value}")

    material_block = ""
    if request.source_type != ContentSourceType.TOPIC and request.raw_text:
        material_block = _PATCH_PROMPT["material_block"].substitute(raw_text=request.raw_text[:3000])

    system_prompt = _PATCH_PROMPT["system"].substitute(
        topic=request.topic,
        audience=request.audience.value,
        language=request.language,
        material_block=material_block,
        layout=slide.layout.value,
        title_part=_PATCH_PROMPT["title_part"].substitute(title=slide.title) if slide.title else "",
        subtitle_part=_PATCH_PROMPT["subtitle_part"].substitute(subtitle=slide.subtitle) if slide.subtitle else "",
        instruction=instruction,
        example=example,
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.7,
        max_tokens=800,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system_prompt}],
    )
    data = json.loads(response.choices[0].message.content)

    merged = slide.model_dump(mode="json")
    merged.update({k: v for k, v in data.items() if k in Slide.model_fields})
    patched = Slide.model_validate(merged)
    patched.index = slide.index
    patched.layout = slide.layout
    return patched


async def _patch_empty_slides(request: UserRequest, presentation: PresentationSchema) -> PresentationSchema:
    gaps = [
        s for s in presentation.slides
        if s.layout not in (SlideLayout.TITLE, SlideLayout.CLOSING)
        and not _slide_has_required_content(s)
    ]
    if not gaps:
        return presentation

    slides = list(presentation.slides)
    for slide in gaps:
        # Сначала дешёвая локальная деградация (без вызова LLM) — только
        # перекладывает то, что реально уже пришло в других полях слайда.
        # Если этого хватило — точечный запрос к модели не нужен вообще.
        degraded = _locally_degrade_slide(slide)
        working = degraded if degraded is not None else slide

        if degraded is not None and _slide_has_required_content(working):
            slides[slide.index - 1] = working
            logger.info(
                f"Locally degraded slide #{slide.index} from layout={slide.layout.value} "
                f"to bullets without an extra LLM call — salvaged {len(working.bullets)} bullet(s)"
            )
            continue

        try:
            patched = await _patch_one_slide(request, working)
            if _slide_has_required_content(patched):
                slides[slide.index - 1] = patched
                logger.info(
                    f"Patched empty slide #{slide.index} (layout={patched.layout.value}, "
                    f"originally {slide.layout.value})"
                )
            else:
                logger.warning(
                    f"Patch attempt for slide #{slide.index} (layout={patched.layout.value}, "
                    f"originally {slide.layout.value}) still came back without the required "
                    f"content — leaving as-is"
                )
        except Exception as e:
            logger.warning(
                f"Failed to patch empty slide #{slide.index} (layout={working.layout.value}, "
                f"originally {slide.layout.value}): {e}"
            )

    return presentation.model_copy(update={"slides": slides})


def _default_slide_count(presentation_type: PresentationType) -> int:
    defaults = {
        PresentationType.PITCH_DECK:  11,
        PresentationType.DIPLOMA:     12,
        PresentationType.CORP_REPORT: 9,   # держим в шаге с STRUCTURE_BLOCKS[CORP_REPORT]
        PresentationType.EDUCATIONAL: 12,
        PresentationType.SALES:       10,
        PresentationType.CONFERENCE:  9,   # держим в шаге с STRUCTURE_BLOCKS[CONFERENCE]
        PresentationType.ROADMAP:     11,
        PresentationType.DOKLAD:      9,   # держим в шаге со STRUCTURE_BLOCKS_DOKLAD_BY_SOURCE
    }
    return defaults.get(presentation_type, 10)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_presentation_structure(request: UserRequest) -> PresentationSchema:
    from datetime import datetime
    now = datetime.now()
    months_ru = ["январе","феврале","марте","апреле","мае","июне","июле","августе","сентябре","октябре","ноябре","декабре"]
    current_date = f"Q{(now.month-1)//3+1} {now.year} ({now.day} {months_ru[now.month-1]})"

    structure_block = _pick_structure_block(request)
    system_prompt = SYSTEM_PROMPT.format(
        schema=_get_json_schema(),
        language=request.language,
        current_date=current_date,
        volume_instruction=VOLUME_INSTRUCTIONS.get(request.content_volume, VOLUME_INSTRUCTIONS[ContentVolume.MEDIUM]),
        structure_block=structure_block,
        competition_table_block=_competition_table_block(structure_block),
        market_slide_block=_market_slide_block(structure_block),
    )
    user_prompt = _build_user_prompt(request)

    logger.info("Generating presentation", extra={
        "topic": request.topic[:50],
        "type": request.presentation_type,
        "language": request.language,
        "model": settings.openai_model,
        "prompts_version": PROMPTS_VERSION,
    })

    response = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.7,
        # 4500 хватало на старую структуру, но DOKLAD теперь пишет subtitle+icon
        # на КАЖДЫЙ bullet и лимиты полей выросли (bullets/metrics.source и
        # т.д.) — в json_object-режиме модель при нехватке бюджета не ломает
        # синтаксис, а тихо обрезает слайды/поля в конце, чтобы закрыть JSON
        # корректно. Из-за этого "строго 9 слайдов" превращалось в 8 без
        # ошибки парсинга — см. лог с slide_count=8.
        max_tokens=8000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw_json = response.choices[0].message.content

    try:
        data = json.loads(raw_json)
        presentation = PresentationSchema.model_validate(data)
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON", extra={"error": str(e)})
        raise
    except Exception as e:
        logger.error("Schema validation failed", extra={"error": str(e)})
        raise

    logger.info("Presentation generated", extra={
        "slide_count": presentation.slide_count,
        "title": presentation.meta.title[:50],
    })

    presentation = await _patch_empty_slides(request, presentation)

    return presentation
