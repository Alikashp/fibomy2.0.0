import asyncio
import json
import logging
from datetime import datetime
from string import Template

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from schemas.presentation import (
    PresentationSchema, UserRequest, PresentationType, AudienceType,
    ContentVolume, ContentSourceType, SourceMode, Slide, SlideLayout, BulletPoint,
    _slide_has_required_content,
)
from generation import postprocess
from generation.llm_models import completion_params, record_usage, track_usage
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
# сборка: какой блок подставить в зависимости от запроса. Блоки, верные только
# для одного режима (по теме / по материалу, strict / extend), выбирает код, а
# не условие внутри текста промпта (ТЗ 4.8.6).

SYSTEM_PROMPT = load_text("system.txt")
_SYSTEM_BLOCKS = load_yaml("system_blocks.yaml")

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

SOURCE_MATERIAL_BLOCKS: dict[SourceMode, Template] = {
    SourceMode.STRICT: load_template("source_material_strict.txt"),
    SourceMode.EXTEND: load_template("source_material_extend.txt"),
}
EXTRA_INSTRUCTIONS_BLOCK = load_template("extra_instructions.txt")
_SLIDE_COUNT_LINES = load_template_map("slide_count.yaml")

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


# Объём и схема ответа у доклада свои (prompts/doklad/): без metrics[].source,
# с meta.source_genre, только layout'ы каталога. Остальные типы — прежние файлы.
VOLUME_INSTRUCTIONS: dict[ContentVolume, str] = load_enum_map("volume.yaml", ContentVolume)
DOKLAD_VOLUME_INSTRUCTIONS: dict[ContentVolume, str] = load_enum_map("doklad/volume.yaml", ContentVolume)


_JSON_SCHEMA = load_text("json_schema.txt")
_DOKLAD_JSON_SCHEMA = load_text("doklad/json_schema.txt")


def _get_json_schema(presentation_type: PresentationType | None = None) -> str:
    return _DOKLAD_JSON_SCHEMA if presentation_type == PresentationType.DOKLAD else _JSON_SCHEMA


def _is_doklad(request: UserRequest) -> bool:
    return request.presentation_type == PresentationType.DOKLAD


def _material_mode(request: UserRequest) -> SourceMode | None:
    """None — режим «по теме». По материалу без явного выбора (задачи из очереди
    до деплоя, API без параметра) — strict: безопасный вариант (ТЗ 3.10)."""
    if request.source_type == ContentSourceType.TOPIC or not request.raw_text:
        return None
    return request.source_mode or SourceMode.STRICT


def _slide_count(request: UserRequest) -> int:
    return request.slide_count_hint or _default_slide_count(request.presentation_type)


# 8000 — бюджет, при котором 9 слайдов доклада перестали обрезаться (см.
# комментарий у вызова ниже). Для колод длиннее растим пропорционально, не
# выше потолка ответа gpt-4o (16 384).
_MAX_TOKENS_BASE = 8000
_MAX_TOKENS_BASE_SLIDES = 9
_MAX_TOKENS_CAP = 16000


def _max_tokens_for(slide_count: int) -> int:
    scaled = -(-_MAX_TOKENS_BASE * slide_count // _MAX_TOKENS_BASE_SLIDES)  # округление вверх
    return min(_MAX_TOKENS_CAP, max(_MAX_TOKENS_BASE, scaled))


_MONTHS_RU = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
              "сентябре", "октябре", "ноябре", "декабре"]


def _current_date(now: datetime) -> str:
    return f"Q{(now.month - 1) // 3 + 1} {now.year} ({now.day} {_MONTHS_RU[now.month - 1]})"


def _data_key(mode: SourceMode | None) -> str:
    return {None: "data_topic", SourceMode.STRICT: "data_strict", SourceMode.EXTEND: "data_extend"}[mode]


def _build_system_prompt(request: UserRequest, now: datetime) -> str:
    mode = _material_mode(request)
    structure_block = _pick_structure_block(request)
    volumes = DOKLAD_VOLUME_INSTRUCTIONS if _is_doklad(request) else VOLUME_INSTRUCTIONS
    # Якорь роадмапа — только «по теме»: по материалу даты берутся из материала (П2)
    date_block = "" if mode else (
        Template(_SYSTEM_BLOCKS["date_topic"]).substitute(current_date=_current_date(now)) + "\n\n"
    )
    return SYSTEM_PROMPT.format(
        role=_SYSTEM_BLOCKS["role_material" if mode else "role_topic"],
        schema=_get_json_schema(request.presentation_type),
        language=request.language,
        date_block=date_block,
        volume_instruction=volumes.get(request.content_volume, volumes[ContentVolume.MEDIUM]),
        data_block=_SYSTEM_BLOCKS[_data_key(mode)],
        structure_block=structure_block,
        competition_table_block=_competition_table_block(structure_block),
        market_slide_block=_market_slide_block(structure_block),
    )


def _slide_count_line(request: UserRequest) -> str:
    if not _is_doklad(request):
        key = "default"
    else:
        key = {None: "doklad_topic", SourceMode.STRICT: "doklad_strict",
               SourceMode.EXTEND: "doklad_extend"}[_material_mode(request)]
    return _SLIDE_COUNT_LINES[key].substitute(n=_slide_count(request))


def _build_user_prompt(request: UserRequest) -> str:
    extra_block = ""
    if request.extra_instructions:
        extra_block = EXTRA_INSTRUCTIONS_BLOCK.substitute(extra_instructions=request.extra_instructions)

    source_material_block = ""
    mode = _material_mode(request)
    if mode:
        source_material_block = SOURCE_MATERIAL_BLOCKS[mode].substitute(raw_text=request.raw_text)

    if request.presentation_type == PresentationType.DOKLAD:
        type_context = _doklad_type_context(request)
    else:
        type_context = TYPE_CONTEXTS.get(request.presentation_type, "")

    return USER_PROMPT_TEMPLATE.substitute(
        topic=request.topic,
        presentation_type=request.presentation_type.value,
        audience=request.audience.value,
        language=request.language,
        slide_count_line=_slide_count_line(request),
        extra_instructions_block=extra_block,
        source_material_block=source_material_block,
        type_context=type_context,
        audience_context=AUDIENCE_CONTEXTS.get(request.audience, ""),
    )


def build_prompts(request: UserRequest, now: datetime | None = None) -> tuple[str, str]:
    """(system, user) основного вызова. Используется и в tests/golden/prompt_snapshot.py."""
    return _build_system_prompt(request, now or datetime.now()), _build_user_prompt(request)


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
#
# Дозапрос видит весь материал и мысли остальных слайдов: раньше он писал
# слайд вслепую — пересказ всего материала, повторы и противоречия соседям
# (docs/PROMPT_REVIEW.md, раздел 9).

_PATCH_SPECS = load_yaml("patch_instructions.yaml")
_LAYOUT_PATCH_INSTRUCTIONS: dict[SlideLayout, str] = {
    SlideLayout(key): spec["instruction"] for key, spec in _PATCH_SPECS.items()
}

_PATCH_JSON_EXAMPLES: dict[SlideLayout, str] = {
    SlideLayout(key): spec["example"] for key, spec in _PATCH_SPECS.items()
}
_PATCH_PROMPT = load_template_map("patch_slide.yaml")

_DIGEST_FACTS = 3        # сколько фактов слайда показывать дозапросу
_DIGEST_FACT_CHARS = 140


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


def _is_content_slide(slide: Slide) -> bool:
    return slide.layout not in (SlideLayout.TITLE, SlideLayout.CLOSING)


def _has_title(slide: Slide) -> bool:
    return bool(slide.title and slide.title.strip())


def _slide_facts(slide: Slide) -> list[str]:
    facts = [f"{m.value} — {m.label}" for m in slide.metrics]
    facts += [b.text for b in slide.bullets]
    facts += [" ".join(p for p in (i.date, i.title) if p) for i in slide.timeline_items]
    if slide.two_column:
        facts += [b.text for b in slide.two_column.left_bullets + slide.two_column.right_bullets]
    if slide.body_text:
        facts.append(slide.body_text)
    return [f[:_DIGEST_FACT_CHARS] for f in facts if f and f.strip()]


def _slides_digest(presentation: PresentationSchema, exclude_index: int) -> str:
    """Заголовки и ключевые факты остальных содержательных слайдов — для дозапроса."""
    lines = []
    for s in presentation.slides:
        if s.index == exclude_index or not _is_content_slide(s):
            continue
        facts = "; ".join(_slide_facts(s)[:_DIGEST_FACTS])
        title = f"«{s.title}»" if _has_title(s) else "(без заголовка)"
        lines.append(f"- слайд {s.index} {title}" + (f": {facts}" if facts else ""))
    return "\n".join(lines) or "- (других содержательных слайдов нет)"


def _other_titles(presentation: PresentationSchema, exclude_index: int) -> str:
    titles = [f"- {s.title}" for s in presentation.slides
              if s.index != exclude_index and _is_content_slide(s) and _has_title(s)]
    return "\n".join(titles) or "- (нет)"


async def _ask_json(system_prompt: str, max_tokens: int) -> dict:
    """Дозапрос. max_tokens — бюджет видимого ответа; параметры под модель
    (max_completion_tokens, запас на рассуждения, temperature) — completion_params."""
    response = await client.chat.completions.create(
        **completion_params(max_tokens),
        messages=[{"role": "system", "content": system_prompt}],
    )
    record_usage(response)
    return json.loads(_response_text(response))


def _response_text(response) -> str:
    choice = response.choices[0]
    content = choice.message.content
    if not content:
        # У моделей с рассуждениями пустой ответ с finish_reason=length значит,
        # что рассуждения съели max_completion_tokens (см. prompts/models.yaml)
        raise ValueError(f"empty LLM response (finish_reason={choice.finish_reason})")
    return content


def _patch_system_prompt(request: UserRequest, presentation: PresentationSchema, slide: Slide) -> str:
    instruction = _LAYOUT_PATCH_INSTRUCTIONS.get(slide.layout)
    example = _PATCH_JSON_EXAMPLES.get(slide.layout)
    if instruction is None or example is None:
        raise ValueError(f"no patch instructions for layout={slide.layout.value}")

    mode = _material_mode(request)
    material_block = ""
    if mode:
        # Весь материал, а не raw_text[:3000]: таблицы в конце документа
        # раньше до дозапроса не доходили (PROMPT_REVIEW, К16)
        material_block = _PATCH_PROMPT["material_block"].substitute(raw_text=request.raw_text)

    title_instruction = ""
    if not _has_title(slide):
        title_instruction = _PATCH_PROMPT["title_instruction"].template
        example = '{"title": "...", "subtitle": "...", ' + example.lstrip()[1:]

    return _PATCH_PROMPT["system"].substitute(
        topic=request.topic,
        audience=request.audience.value,
        language=request.language,
        data_rule=_PATCH_PROMPT[_data_key(mode)].template,
        material_block=material_block,
        other_slides=_slides_digest(presentation, slide.index),
        layout=slide.layout.value,
        title_part=_PATCH_PROMPT["title_part"].substitute(title=slide.title) if _has_title(slide) else "",
        subtitle_part=_PATCH_PROMPT["subtitle_part"].substitute(subtitle=slide.subtitle) if slide.subtitle else "",
        instruction=instruction,
        title_instruction=title_instruction,
        example=example,
    )


async def _patch_one_slide(request: UserRequest, presentation: PresentationSchema, slide: Slide) -> Slide:
    data = await _ask_json(_patch_system_prompt(request, presentation, slide), max_tokens=800)

    merged = slide.model_dump(mode="json")
    allowed = set(Slide.model_fields) - {"index", "layout", "footnote"}
    if _has_title(slide):
        allowed -= {"title"}  # заголовок, который уже есть, дозапрос не переписывает
    merged.update({k: v for k, v in data.items() if k in allowed})
    patched = Slide.model_validate(merged)
    patched.index = slide.index
    patched.layout = slide.layout
    return patched


async def _patch_empty_slides(request: UserRequest, presentation: PresentationSchema) -> PresentationSchema:
    gaps = [
        s for s in presentation.slides
        if _is_content_slide(s) and not _slide_has_required_content(s)
    ]
    if not gaps:
        return presentation

    for slide in gaps:
        # Сначала дешёвая локальная деградация (без вызова LLM) — только
        # перекладывает то, что реально уже пришло в других полях слайда.
        # Если этого хватило — точечный запрос к модели не нужен вообще.
        degraded = _locally_degrade_slide(slide)
        working = degraded if degraded is not None else slide
        result = None  # неудачный дозапрос — слайд остаётся как был

        if degraded is not None and _slide_has_required_content(working):
            result = working
            logger.info(
                f"Locally degraded slide #{slide.index} from layout={slide.layout.value} "
                f"to bullets without an extra LLM call — salvaged {len(working.bullets)} bullet(s)"
            )
        else:
            try:
                # presentation — уже с исправленными слайдами: следующий дозапрос видит предыдущие
                patched = await _patch_one_slide(request, presentation, working)
                if _slide_has_required_content(patched):
                    result = patched
                    logger.info(
                        f"Patched empty slide #{slide.index} (layout={patched.layout.value}, "
                        f"originally {slide.layout.value}, had_title={_has_title(slide)})"
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

        if result is not None:
            slides = list(presentation.slides)
            slides[slide.index - 1] = result
            presentation = presentation.model_copy(update={"slides": slides})

    return presentation


def _slide_content_for_title(slide: Slide) -> str:
    parts = [f"подзаголовок: {slide.subtitle}"] if slide.subtitle else []
    parts += [f"- {f}" for f in _slide_facts(slide)]
    return "\n".join(parts) or "- (содержания нет)"


async def _patch_title(request: UserRequest, presentation: PresentationSchema, slide: Slide) -> Slide:
    prompt = _PATCH_PROMPT["title_only"].substitute(
        topic=request.topic,
        language=request.language,
        layout=slide.layout.value,
        slide_content=_slide_content_for_title(slide),
        other_titles=_other_titles(presentation, slide.index),
    )
    data = await _ask_json(prompt, max_tokens=100)
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("empty title in response")
    return slide.model_copy(update={"title": title[:120]})


async def _patch_missing_titles(request: UserRequest, presentation: PresentationSchema) -> PresentationSchema:
    """Содержательный слайд без заголовка — дозапрос только заголовка (параллельно)."""
    untitled = [s for s in presentation.slides if _is_content_slide(s) and not _has_title(s)]
    if not untitled:
        return presentation

    results = await asyncio.gather(
        *(_patch_title(request, presentation, s) for s in untitled), return_exceptions=True,
    )
    slides = list(presentation.slides)
    for slide, result in zip(untitled, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to patch title of slide #{slide.index}: {result}")
            continue
        slides[slide.index - 1] = result
        logger.info(f"Patched missing title of slide #{slide.index} (layout={slide.layout.value})")
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
        PresentationType.DOKLAD:      9,   # по умолчанию в боте (main.DOKLAD_DEFAULT_SLIDE_COUNT)
    }
    return defaults.get(presentation_type, 10)


async def postprocess_presentation(request: UserRequest, presentation: PresentationSchema) -> PresentationSchema:
    """Всё, что идёт после валидации ответа: детерминированные правки и дозапросы."""
    if _is_doklad(request):
        presentation = postprocess.remap_layouts(presentation)
    presentation = postprocess.timelines_without_dates_to_bullets(presentation)
    presentation = await _patch_empty_slides(request, presentation)
    presentation = await _patch_missing_titles(request, presentation)
    if _is_doklad(request):
        # У питч-дека source по-прежнему пишет модель (расчёт TAM/SAM/SOM)
        presentation = postprocess.assign_sources(presentation, request)
    return presentation


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_presentation_structure(request: UserRequest) -> PresentationSchema:
    system_prompt, user_prompt = build_prompts(request)
    mode = _material_mode(request)
    params = completion_params(_max_tokens_for(_slide_count(request)))

    logger.info("Generating presentation", extra={
        "topic": request.topic[:50],
        "type": request.presentation_type,
        "language": request.language,
        "model": settings.openai_model,
        "prompts_version": PROMPTS_VERSION,
        "slide_count_hint": _slide_count(request),
        "source_type": request.source_type.value,
        "source_mode": mode.value if mode else None,
        "reasoning_effort": params.get("extra_body", {}).get("reasoning_effort"),
        "token_limit": params.get("max_tokens") or params.get("extra_body", {}).get("max_completion_tokens"),
    })

    response = await client.chat.completions.create(
        **params,
        # 4500 хватало на старую структуру, но DOKLAD теперь пишет subtitle+icon
        # на КАЖДЫЙ bullet и лимиты полей выросли (bullets/metrics.source и
        # т.д.) — в json_object-режиме модель при нехватке бюджета не ломает
        # синтаксис, а тихо обрезает слайды/поля в конце, чтобы закрыть JSON
        # корректно. Из-за этого "строго 9 слайдов" превращалось в 8 без
        # ошибки парсинга — см. лог с slide_count=8. Для 12–15 слайдов
        # бюджет растёт — см. _max_tokens_for. Для моделей с рассуждениями это
        # бюджет видимого JSON: completion_params добавляет запас на рассуждения
        # и передаёт его как max_completion_tokens.
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    record_usage(response)
    if response.choices[0].finish_reason == "length":
        logger.warning("LLM response hit the token limit — JSON may be truncated", extra={
            "limit": params.get("max_tokens") or params.get("extra_body", {}).get("max_completion_tokens"),
            "usage": getattr(response, "usage", None) and response.usage.model_dump(),
        })
    raw_json = _response_text(response)

    try:
        data = json.loads(raw_json)
        if _is_doklad(request):
            data = postprocess.prepare_doklad_json(data)
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
        "source_genre": presentation.meta.source_genre,
    })

    return await postprocess_presentation(request, presentation)
