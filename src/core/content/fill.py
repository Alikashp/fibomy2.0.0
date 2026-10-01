"""CONTENT: наполнение слайдов, один вызов LLM на слайд, параллельно (06_PROMPTS.md, 1, 4).

Схема ответа собирается для конкретного слайда: kind задаёт структуру
(04_CONTRACTS.md, 4.2), выбранный вариант — пределы (maxItems, maxLength).
Те же пределы — в тексте задания (блок вместимости).

Все вызовы колоды начинаются с байт-в-байт одинакового системного промпта
(правила → язык → безопасность → данные → источник → план) — его кэширует
провайдер (03_ARCHITECTURE.md, 5). Слайд-специфичное — только в сообщении user.

Слайд без валидного ответа за 2 попытки → запасной слайд из плана без LLM:
утверждение (statement) с ключевой мыслью как пояснением.
"""

import logging

from pydantic import BaseModel, Field

from core.llm import prompts as P
from core.llm.client import DeckUsage, LLMClient, LLMError
from core.models import schema as S
from core.models.digest import SourceDigest
from core.models.layout import LayoutSpec, capacity, load_layouts
from core.models.outline import PlannedSlide
from core.models.request import DeckRequest
from core.render.pptx.icons import DEFAULT_ICON, icon_names

logger = logging.getLogger(__name__)

CONTENT_TIMEOUT = 30.0
CONTENT_ATTEMPTS = 2
CONTENT_VISIBLE_TOKENS = 1500
FALLBACK_VARIANT = "statement.big_quote"


# ── Схемы ответов по kind ────────────────────────────────────────────────────

class StatementContent(BaseModel):
    body: str | None = None


class BulletItem(BaseModel):
    heading: str
    text: str
    icon: str = DEFAULT_ICON


class BulletsContent(BaseModel):
    items: list[BulletItem] = Field(min_length=1)


class ConclusionItem(BaseModel):
    text: str


class ConclusionContent(BaseModel):
    items: list[ConclusionItem] = Field(min_length=1)


MODELS = {"statement": StatementContent, "bullets": BulletsContent, "conclusion": ConclusionContent}


def _slot_caps(layout: LayoutSpec, n: int | None) -> dict[str, int]:
    """Базовая вместимость слотов; у повторяющихся — минимум по раскладкам от min до n,
    чтобы текст влез при любом числе элементов, которое вернёт модель."""
    caps = {name: capacity(e) for name, e in layout.text_slots().items()}
    if layout.arrangements and n:
        lo, _ = layout.item_range
        for k in range(lo, n + 1):
            for name, e in layout.text_slots(k).items():
                if name.startswith("item."):
                    caps[name] = min(caps.get(name, 10 ** 6), capacity(e))
    return caps


def item_bounds(layout: LayoutSpec, n: int | None) -> tuple[int, int]:
    lo, hi = layout.item_range
    return lo, min(hi, max(lo, n or hi))


def content_schema(kind: str, layout: LayoutSpec, n: int | None) -> tuple[dict, list[str]]:
    """(JSON Schema ответа, строки блока вместимости) для слайда."""
    caps = _slot_caps(layout, n)
    if kind == "statement":
        body = caps.get("body", 200)
        return (S.obj({"body": S.nullable(S.string(body))}),
                [f"- пояснение (body): до {body} знаков, 1–2 предложения; можно null"])
    if kind == "bullets":
        lo, hi = item_bounds(layout, n)
        head, text = caps["item.heading"], caps["item.text"]
        schema = S.obj({"items": S.array(S.obj({
            "heading": S.string(head), "text": S.string(text), "icon": S.enum(list(icon_names())),
        }), min_items=lo, max_items=hi)})
        return schema, [f"- пунктов: от {lo} до {hi}",
                        f"- заголовок пункта (heading): до {head} знаков",
                        f"- текст пункта (text): до {text} знаков — одно предложение",
                        f"- icon — одно из: {', '.join(icon_names())}"]
    if kind == "conclusion":
        lo, hi = item_bounds(layout, n)
        text = caps["item.text"]
        schema = S.obj({"items": S.array(S.obj({"text": S.string(text)}), min_items=lo, max_items=hi)})
        return schema, [f"- выводов: от {lo} до {hi}", f"- вывод (text): до {text} знаков — одно предложение"]
    raise NotImplementedError(f"CONTENT for kind {kind} — сессия 2 (docs/design/09_PLAN.md)")


def content_errors(kind: str, parsed: BaseModel, layout: LayoutSpec, n: int | None) -> list[str]:
    """Пределы, которые провайдер мог не соблюсти (json_object или strict без maxLength)."""
    errors = []
    if kind in ("bullets", "conclusion"):
        lo, hi = item_bounds(layout, n)
        if not lo <= len(parsed.items) <= hi:
            errors.append(f"элементов {len(parsed.items)}, нужно от {lo} до {hi}")
    return errors


# ── Промпты ──────────────────────────────────────────────────────────────────

def digest_text(digest: SourceDigest) -> str:
    if digest.mode == "topic":
        return f"Материала нет — доклад по теме: <topic>{digest.topic}</topic>"
    return "\n".join(f"[{f.id}] {f.text}" for f in digest.fragments)


def outline_text(subtitle: str, plan: list[PlannedSlide]) -> str:
    lines = ["Слайд 1 — титул: тема пользователя (ставит система). Подзаголовок: " + subtitle]
    for i, s in enumerate(plan, start=2):
        lines.append(f"Слайд {i} [{s.kind}, {s.role}]: {s.title} — {s.key_message}")
    lines.append(f"Слайд {len(plan) + 2} — финальный (ставит система).")
    return "\n".join(lines)


def system_prompt(request: DeckRequest, digest: SourceDigest, subtitle: str, plan: list[PlannedSlide]) -> str:
    common = P.common_blocks(mode=request.mode, source_mode=request.source_mode,
                             language=request.language, audience=request.audience)
    common.pop("audience")
    return P.render("content/system.txt", digest=digest_text(digest),
                    outline=outline_text(subtitle, plan), **common)


def task_prompt(planned: PlannedSlide, index: int, total: int, kind: str, capacity_lines: list[str]) -> str:
    return P.render(
        "content/task.txt", index=index, total=total, kind=kind, role=planned.role, title=planned.title,
        key_message=planned.key_message, refs=", ".join(planned.refs) or "—",
        kind_rules=P.text(f"content/kinds/{kind}.txt"),
        capacity=P.render("content/capacity.txt", lines="\n".join(capacity_lines)),
    )


# ── Наполнение ───────────────────────────────────────────────────────────────

def fallback_content(planned: PlannedSlide) -> dict:
    """Запасной слайд без LLM: утверждение = заголовок, пояснение = ключевая мысль."""
    body = planned.key_message if planned.key_message.strip() != planned.title.strip() else None
    return {"body": body}


async def fill_slide(client: LLMClient, usage: DeckUsage, system: str, planned: PlannedSlide, kind: str,
                     variant: str, items: int | None, index: int, total: int, slide_id: str) -> tuple[dict, str, str | None]:
    """→ (content, variant, причина деградации или None)."""
    layout = load_layouts()[variant]
    schema, cap_lines = content_schema(kind, layout, items)
    try:
        parsed = await client.structured(
            stage="content", system=system, user=task_prompt(planned, index, total, kind, cap_lines),
            schema_name=f"slide_{kind}", schema=schema, model_cls=MODELS[kind],
            visible_tokens=CONTENT_VISIBLE_TOKENS, timeout=CONTENT_TIMEOUT, attempts=CONTENT_ATTEMPTS,
            usage=usage, validate=lambda p: content_errors(kind, p, layout, items),
            log_fields={"slide_id": slide_id, "slide_index": index},
        )
    except LLMError as e:
        logger.warning("CONTENT failed — fallback slide from plan", extra={"slide_id": slide_id, "error": str(e)})
        return fallback_content(planned), FALLBACK_VARIANT, "content_fallback"
    content = parsed.model_dump()
    for item in content.get("items", []):
        if "icon" in item and item["icon"] not in icon_names():
            item["icon"] = DEFAULT_ICON
    return content, variant, None
