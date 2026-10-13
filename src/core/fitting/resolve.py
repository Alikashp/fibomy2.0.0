"""Невлезание текста без обрезки (замечание владельца, сессия 5; D-071).

Порядок для слайда, у которого слот не влез и на минимальном кегле (fitter.Overflow):
1. шаг кегля — уже сделан fitter'ом;
2. более вместительный вариант того же kind (смена варианта не требует LLM, D-031);
3. сокращение через LLM: «сократи до N знаков», N — по метрикам шрифта слота; ответ
   проверяется заново, до SHORTEN_ROUNDS раз; не хватило — вариант без картинки (текст важнее);
4. разбить слайд на два: пункты делятся пополам, у второй половины — «(продолжение)».
Если и это не помогло, необязательное поле (подзаголовок, пояснение, автор, сноска) убирается
целиком, обязательное — слайд заменяется утверждением из плана; не влезло и оно — FitError,
колода не отдаётся (финальная проверка, D-072).

Тема пользователя на титуле и числа не сокращаются (never_truncate): для темы — вариант титула
с бо́льшим полем, для числа с единицей — сокращается единица.
Многоточие и обрезка не используются нигде.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel

from core import i18n
from core.fitting.fitter import Overflow, layout_slide, measure
from core.fitting.metrics import text_width
from core.llm import prompts as P
from core.models import bind as B
from core.models import schema as S
from core.models.deck import DeckSpec, Slide
from core.models.ids import slide_id
from core.models.layout import Element, LayoutSpec, load_layouts, max_lines_at, style_chain, variants_of
from core.models.theme import BOLD_STYLES, Theme

logger = logging.getLogger(__name__)

SHORTEN_ROUNDS = 2
SHORTEN_TIMEOUT = 25.0
SHORTEN_VISIBLE_TOKENS = 1200
SHORTEN_RESERVE = 20.0          # секунд до дедлайна колоды, после которых LLM не зовём
MAX_SLIDES = 20                 # DeckSpec.slides max_length
FALLBACK_VARIANT = "statement.big_quote"


class FitError(RuntimeError):
    """Текст не удалось вписать без обрезки."""


class ShortText(BaseModel):
    id: str
    text: str


class ShortenResponse(BaseModel):
    texts: list[ShortText]


Shorten = Callable[[Slide, list[dict]], Awaitable[dict[str, str] | None]]


@dataclass
class Target:
    """Что и до скольких знаков сократить."""
    id: str
    slide: Slide
    element: Element
    item: int | None
    field: str               # bind поля, которое пишется обратно (у числа с единицей — unit)
    text: str
    limit: int
    note: str = ""


# ── Вспомогательное ─────────────────────────────────────────────────────────

def _element_for(layout: LayoutSpec, slide: Slide, o: Overflow) -> Element | None:
    if o.item is None:
        return next((e for e in layout.elements() if e.name == o.element), None)
    items = B.items_of(layout.items_bind, slide)
    if len(items) not in layout.arrangements:
        return None
    return next((e for e in layout.item_elements(len(items))[o.item] if e.name == o.element), None)


def char_limit(element: Element, text: str, theme: Theme, style: str | None = None, ratio: float = 1.0) -> int:
    """Сколько знаков текста помещается в слот на минимальном стиле — по ширине текста этим шрифтом."""
    style = style or style_chain(element.style, element.min_style)[-1]
    pt, bold = theme.pt(style), style in BOLD_STYLES
    lines = max(1, max_lines_at(element, style, theme.typescale))
    room = element.box.w * lines * (0.88 if lines > 1 else 0.97)       # потери на переносе по словам
    width = max(1.0, text_width(text, pt, bold))
    return max(4, math.floor(len(text) * min(1.0, room / width) * ratio) - 1)


def _optional(element: Element) -> bool:
    return element.optional and not element.never_truncate


def _variant_fits(slide: Slide, spec: DeckSpec, variant: str, theme: Theme) -> tuple[Slide, list[Overflow]]:
    trial = slide.model_copy(deep=True)
    trial.variant = variant
    overflows = layout_slide(trial, spec, load_layouts()[variant], theme)
    return trial, overflows


def _need(slide: Slide):
    from core.selection.select import SlideNeed
    items = B.items_of(load_layouts()[slide.variant].items_bind, slide)
    return SlideNeed(slide.id, slide.kind, slide.role, len(items) or None, slide.title,
                     has_image=bool(slide.image), polarity=slide.content.get("polarity"))


def alternatives(slide: Slide, spec: DeckSpec, drop_image: bool = False) -> list[str]:
    """Другие варианты того же kind, которые принимают слайд как есть (число пунктов, картинка);
    «цветные» — только вместо цветного (их расстановку SELECT уже проверил). drop_image — варианты
    без картинки (титул: тема пользователя важнее картинки)."""
    from core.selection.select import applicable
    layouts = load_layouts()
    current = layouts[slide.variant]
    need = _need(slide)
    if drop_image:
        need.has_image = False
    out = []
    for spec_ in variants_of(slide.kind):
        if spec_.id == slide.variant or (spec_.color_heavy and not current.color_heavy):
            continue
        if bool(spec_.image_slot) != need.has_image:
            continue
        rule = dict(spec_.applies_when)
        rule.pop("title_max_chars", None)                  # проверяет примерка, а не число знаков
        probe = LayoutSpec(**{**spec_.__dict__, "applies_when": rule})
        if applicable(probe, need):
            out.append(spec_.id)
    return out


# ── Шаг 2: вариант ──────────────────────────────────────────────────────────

def try_variants(slide: Slide, spec: DeckSpec, theme: Theme, drop_image: bool = False) -> Slide | None:
    """Первый вариант того же kind, где влезает всё. Титул с картинкой — и без неё (тема важнее);
    drop_image — и другие слайды без картинки (после сокращения: текст важнее картинки)."""
    passes = [False, True] if slide.image and (slide.kind == "title" or drop_image) else [False]
    for drop_image in passes:
        source = slide
        if drop_image:
            source = slide.model_copy(deep=True)
            source.image = None
        for variant in alternatives(source, spec, drop_image=drop_image):
            trial, overflows = _variant_fits(source, spec, variant, theme)
            if not overflows:
                trial.fit.variant_from = slide.variant
                return trial
    return None


# ── Шаг 3: сокращение через LLM ─────────────────────────────────────────────

def targets(slide: Slide, spec: DeckSpec, overflows: list[Overflow], theme: Theme,
            ratio: float = 1.0) -> list[Target]:
    layout = load_layouts()[slide.variant]
    items = B.items_of(layout.items_bind, slide)
    out = []
    for n, o in enumerate(overflows):
        element = _element_for(layout, slide, o)
        if element is None:
            continue
        item = items[o.item] if o.item is not None and o.item < len(items) else None
        if element.suffix:
            # Число не сокращается — сокращается единица, чтобы встать в строку со значением
            probe = Element(name=element.name, type="text", box=element.box, bind=element.suffix)
            unit = B.text_of(probe, slide, spec, item=item, index=o.item)
            if not unit:
                continue
            value = B.text_of(element, slide, spec, item=item, index=o.item) or ""
            style = style_chain(element.style, element.min_style)[-1]
            room = element.box.w * 0.97 - text_width(value + " ", theme.pt(style), style in BOLD_STYLES)
            unit_w = text_width(unit, theme.pt("small"), False)
            limit = max(2, math.floor(len(unit) * max(0.0, room) / max(1.0, unit_w) * ratio) - 1)
            out.append(Target(f"t{n + 1}", slide, element, o.item, element.suffix, unit, limit,
                              note="единица измерения рядом с числом"))
            continue
        if o.never_shorten:
            continue
        note = f"слово «{o.overlong[0]}» не помещается в строку" if o.overlong else ""
        out.append(Target(f"t{n + 1}", slide, element, o.item, element.bind or "", o.text,
                          char_limit(element, o.text, theme, ratio=ratio), note=note))
    return out


def _write(t: Target, value: str, spec: DeckSpec) -> None:
    layout = load_layouts()[t.slide.variant]
    items = B.items_of(layout.items_bind, t.slide)
    item = items[t.item] if t.item is not None and t.item < len(items) else None
    B.write(Element(name=t.element.name, type="text", box=t.element.box, bind=t.field), t.slide, value, item=item)


def shorten_prompt_items(ts: list[Target]) -> str:
    lines = []
    for t in ts:
        extra = f"; {t.note}" if t.note else ""
        lines.append(f'- id "{t.id}", до {t.limit} знаков (сейчас {len(t.text)}{extra}): {t.text}')
    return "\n".join(lines)


async def llm_shorten(client, usage, system: str, slide: Slide, ts: list[Target],
                      log_fields: dict | None = None) -> dict[str, str] | None:
    """Один вызов «сократи до N знаков» на слайд. → {id: новый текст} или None."""
    from core.llm.client import LLMError
    schema = S.obj({"texts": S.array(S.obj({"id": S.enum([t.id for t in ts]), "text": S.string()}))})
    limits = {t.id: t.limit for t in ts}

    def errors(resp: ShortenResponse) -> list[str]:
        got = {x.id for x in resp.texts}
        return [f"нет текста id {i}" for i in limits if i not in got]

    def remarks(resp: ShortenResponse) -> list[str]:
        out = []
        for x in resp.texts:
            if x.id in limits and len(x.text) > limits[x.id]:
                out.append(f"id {x.id}: {len(x.text)} знаков, нужно до {limits[x.id]}")
            if x.text.rstrip().endswith(("…", "...")):
                out.append(f"id {x.id}: многоточие в конце — нужна законченная фраза")
        return out

    try:
        resp = await client.structured(
            stage="fit", system=system, user=P.render("fit/shorten.txt", index=slide.index,
                                                      items=shorten_prompt_items(ts)),
            schema_name="shorten", schema=schema, model_cls=ShortenResponse,
            visible_tokens=SHORTEN_VISIBLE_TOKENS, timeout=SHORTEN_TIMEOUT, attempts=2, usage=usage,
            validate=errors, soft=remarks, log_fields={"slide_id": slide.id, **(log_fields or {})})
    except LLMError as e:
        logger.warning("Shorten failed", extra={"slide_id": slide.id, "error": str(e)[:300]})
        return None
    return {x.id: x.text.strip() for x in resp.texts if x.id in limits and x.text.strip()}


# ── Шаг 4: разбить на два ───────────────────────────────────────────────────

SPLIT_KINDS = {"bullets", "conclusion", "process", "metrics"}


def _items_key(slide: Slide) -> str | None:
    bind = load_layouts()[slide.variant].items_bind
    return bind.split(".")[1] if bind else None


def _variant_for(slide: Slide, n: int, prefer: str) -> str | None:
    from core.selection.select import applicable
    need = _need(slide)
    need.items = n
    options = [s for s in variants_of(slide.kind) if not s.color_heavy and bool(s.image_slot) == bool(slide.image)
               and n in s.arrangements and applicable(s, need)]
    if not options:
        return None
    ids = [s.id for s in options]
    if prefer in ids:
        return prefer
    return next((s.id for s in options if s.fallback), ids[0])


def split_slide(slide: Slide, language: str) -> list[Slide] | None:
    """Пункты пополам → два слайда того же kind. None — делить нечего или некуда."""
    key = _items_key(slide)
    if slide.kind not in SPLIT_KINDS or not key:
        return None
    items = list(slide.content.get(key) or [])
    if len(items) < 2:
        return None
    half = math.ceil(len(items) / 2)
    parts = [items[:half], items[half:]]
    out = []
    for k, part in enumerate(parts):
        s = slide.model_copy(deep=True)
        s.content[key] = part
        if k == 1:
            s.image = None
            s.title = f"{slide.title} ({i18n.text(i18n.CONTINUED, language)})"
            if "body" in s.content:
                s.content["body"] = None                  # пояснение — на первой половине
        variant = _variant_for(s, len(part), slide.variant)
        if variant is None:
            return None
        s.variant = variant
        out.append(s)
    return out


def regroup(slide: Slide, language: str) -> list[Slide]:
    """Пунктов больше, чем умеет вариант (в схеме CONTENT нет maxItems, D-071): другой вариант того
    же kind, иначе — два слайда. Пункты не теряются."""
    layouts = load_layouts()
    layout = layouts[slide.variant]
    key = _items_key(slide)
    if key:
        n = len(slide.content.get(key) or [])
        if not n or n in layout.arrangements:
            return [slide]
        variant = _variant_for(slide, n, slide.variant)
        if variant:
            slide.variant = variant
            return [slide]
        parts = split_slide(slide, language)
        if not parts:
            return [slide]
        return [p for part in parts for p in regroup(part, language)]
    if slide.kind == "comparison":
        limit = 4
        left = list((slide.content.get("left") or {}).get("points") or [])
        right = list((slide.content.get("right") or {}).get("points") or [])
        if len(left) <= limit and len(right) <= limit:
            return [slide]
        first, second = slide.model_copy(deep=True), slide.model_copy(deep=True)
        first.content["left"]["points"], first.content["right"]["points"] = left[:limit], right[:limit]
        second.content["left"]["points"], second.content["right"]["points"] = left[limit:], right[limit:]
        second.title = f"{slide.title} ({i18n.text(i18n.CONTINUED, language)})"
        return [first, second]
    return [slide]


def renumber(spec: DeckSpec) -> dict[str, str]:
    """Номера и id слайдов по порядку после разбиения. → {старый id: новый id}."""
    mapping = {}
    for i, s in enumerate(spec.slides, start=1):
        new = slide_id(i)
        if s.id != new:
            mapping[s.id] = new
        s.index, s.id = i, new
    for d in spec.degradations:
        if d.slide_id in mapping:
            d.slide_id = mapping[d.slide_id]
    return mapping


# ── Последний шаг: убрать необязательное, заменить утверждением ─────────────

def drop_optional(slide: Slide, spec: DeckSpec, overflows: list[Overflow]) -> bool:
    """Необязательное поле, которое не влезло, убирается целиком (не обрезается)."""
    layout = load_layouts()[slide.variant]
    dropped = False
    for o in overflows:
        element = _element_for(layout, slide, o)
        if element is None or not _optional(element) or o.item is not None:
            continue
        if (element.bind or "").startswith("content."):
            B.write(element, slide, None)
        elif element.bind == "footnote":
            slide.footnote = None
        else:
            continue
        dropped = True
        spec.degrade("fit", f"dropped:{element.name}", slide.id)
    return dropped


def to_statement(slide: Slide) -> Slide:
    s = slide.model_copy(deep=True)
    plan = slide.plan
    title = plan.title if plan else slide.title
    body = plan.key_message if plan and plan.key_message.strip() != title.strip() else None
    s.kind, s.variant, s.data, s.image = "statement", FALLBACK_VARIANT, None, None
    s.title, s.content = title, {"body": body}
    return s


# ── Оркестровка ─────────────────────────────────────────────────────────────

async def resolve_deck(spec: DeckSpec, theme: Theme, overflows: list[Overflow], *,
                       shorten: Shorten | None = None, deadline: float | None = None,
                       ratio: float = 1.0) -> None:
    """Устраняет невлезания колоды по порядку D-071. Не получилось — FitError."""
    by_slide: dict[str, list[Overflow]] = {}
    for o in overflows:
        by_slide.setdefault(o.slide_id, []).append(o)
    if not by_slide:
        return

    async def one(sid: str, found: list[Overflow]) -> list[Slide]:
        slide = next(s for s in spec.slides if s.id == sid)
        layouts = load_layouts()
        # 2 — вариант
        if ratio >= 1.0:
            alt = try_variants(slide, spec, theme)
            if alt is not None:
                spec.degrade("fit", f"variant:{slide.variant}->{alt.variant}", slide.id)
                return [alt]
        # 3 — сокращение
        current, left = slide, found
        for round_ in range(SHORTEN_ROUNDS):
            if shorten is None or (deadline is not None and deadline - time.monotonic() < SHORTEN_RESERVE):
                break
            ts = targets(current, spec, left, theme, ratio=ratio * (0.9 if round_ else 1.0))
            if not ts:
                break
            answer = await shorten(current, ts)
            if not answer:
                break
            trial = current.model_copy(deep=True)
            for t in ts:
                t.slide = trial
                if answer.get(t.id):
                    _write(t, answer[t.id], spec)
            left = layout_slide(trial, spec, layouts[trial.variant], theme)
            current = trial
            spec.degrade("fit", f"shortened:{','.join(sorted({t.element.name for t in ts}))}", slide.id)
            if not left:
                return [current]
            alt = try_variants(current, spec, theme)
            if alt is not None:
                spec.degrade("fit", f"variant:{current.variant}->{alt.variant}", slide.id)
                return [alt]
        # 3b — без картинки: варианты с полным полем текста
        if current.image:
            alt = try_variants(current, spec, theme, drop_image=True)
            if alt is not None:
                spec.degrade("fit", f"variant:{current.variant}->{alt.variant}:no_image", slide.id)
                return [alt]
            no_image = current.model_copy(deep=True)
            no_image.image = None
            options = [v for v in alternatives(no_image, spec, drop_image=True)]
            if options:
                current = no_image
                current.variant = options[0]
                layout_slide(current, spec, layouts[current.variant], theme)
        # 4 — разбить на два
        if len(spec.slides) < MAX_SLIDES:
            parts = split_slide(current, spec.meta.language)
            if parts:
                ok = True
                for p in parts:
                    rest = layout_slide(p, spec, layouts[p.variant], theme)
                    if rest and shorten is not None and (deadline is None
                                                         or deadline - time.monotonic() >= SHORTEN_RESERVE):
                        ts = targets(p, spec, rest, theme, ratio=ratio)
                        answer = await shorten(p, ts) if ts else None
                        for t in ts:
                            t.slide = p
                            if answer and answer.get(t.id):
                                _write(t, answer[t.id], spec)
                        rest = layout_slide(p, spec, layouts[p.variant], theme)
                    ok = ok and not rest
                if ok:
                    spec.degrade("fit", f"split:{current.variant}", slide.id)
                    return parts
        # Последнее: необязательное — целиком, обязательное — утверждение из плана
        left = layout_slide(current, spec, layouts[current.variant], theme)
        if left and drop_optional(current, spec, left):
            left = layout_slide(current, spec, layouts[current.variant], theme)
        if left and current.kind not in ("title", "closing"):
            st = to_statement(current)
            rest = layout_slide(st, spec, layouts[st.variant], theme)
            if not rest:
                spec.degrade("fit", f"{current.kind}->statement", slide.id)
                return [st]
        if left:
            raise FitError(f"{slide.id}: " + "; ".join(f"{o.key}:{o.reason}" for o in left))
        return [current]

    results = await asyncio.gather(*(one(sid, found) for sid, found in by_slide.items()))
    replaced = dict(zip(by_slide, results))
    slides = []
    for s in spec.slides:
        slides.extend(replaced.get(s.id, [s]))
    spec.slides = slides
    renumber(spec)
