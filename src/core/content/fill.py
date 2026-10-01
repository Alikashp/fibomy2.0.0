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
from core.ingest.render import digest_text
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


class MetricItem(BaseModel):
    value: str
    unit: str | None = None
    label: str
    status: str = "fact"
    source_ref: str | None = None


class MetricsContent(BaseModel):
    items: list[MetricItem] = Field(min_length=1)
    body: str | None = None


class Side(BaseModel):
    header: str
    points: list[str] = Field(min_length=1)


class ComparisonContent(BaseModel):
    left: Side
    right: Side


class Step(BaseModel):
    label: str
    text: str


class ProcessContent(BaseModel):
    steps: list[Step] = Field(min_length=1)


class CategoryLabel(BaseModel):
    row: str
    label: str


class ChartContent(BaseModel):
    insight: str
    value_axis_title: str | None = None
    category_labels: list[CategoryLabel] | None = None


MODELS = {"statement": StatementContent, "bullets": BulletsContent, "conclusion": ConclusionContent,
          "metrics": MetricsContent, "comparison": ComparisonContent, "process": ProcessContent,
          "chart_series": ChartContent, "chart_share": ChartContent}
STATUSES = ["fact", "plan", "requirement", "constraint", "estimate"]
# Подпись не-факта над числом (ТЗ 4.8.3): рендерер показывает её в слоте tag
STATUS_TAGS = {"plan": "цель", "requirement": "требование", "constraint": "ограничение", "estimate": "оценка"}
COMPARISON_POINTS = (2, 4)
CATEGORY_LABEL_MAX = 24


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
    if kind == "metrics":
        lo, hi = item_bounds(layout, n)
        value_el = layout.text_slots(hi)["item.value"]
        value_cap = capacity(value_el, value_el.min_style)
        label, body = caps["item.label"], caps.get("body", 150)
        schema = S.obj({
            "items": S.array(S.obj({
                "value": S.string(value_cap), "unit": S.nullable(S.string(caps.get("item.unit", 20))),
                "label": S.string(label), "status": S.enum(STATUSES),
                "source_ref": S.nullable(S.string(40)),
            }), min_items=lo, max_items=hi),
            "body": S.nullable(S.string(body)),
        })
        return schema, [f"- чисел: от {lo} до {hi}",
                        f"- значение (value): до {value_cap} знаков, ровно как в источнике",
                        f"- единица (unit): до {caps.get('item.unit', 20)} знаков или null",
                        f"- подпись (label): до {label} знаков — к чему относится число",
                        f"- пояснение (body): до {body} знаков или null"]
    if kind == "comparison":
        head, point = caps["left_header"], caps["left_point1"]
        side = S.obj({"header": S.string(head), "points": S.array(S.string(point), *COMPARISON_POINTS)})
        return S.obj({"left": side, "right": side}), [
            "- две стороны: left и right",
            f"- заголовок стороны (header): до {head} знаков",
            f"- пунктов у каждой стороны: от {COMPARISON_POINTS[0]} до {COMPARISON_POINTS[1]}, пункт — до {point} знаков"]
    if kind == "process":
        lo, hi = item_bounds(layout, n)
        label, text = caps["item.label"], caps["item.text"]
        schema = S.obj({"steps": S.array(S.obj({"label": S.string(label), "text": S.string(text)}),
                                         min_items=lo, max_items=hi)})
        return schema, [f"- шагов: от {lo} до {hi}, по порядку",
                        f"- название шага (label): до {label} знаков",
                        f"- пояснение (text): до {text} знаков — одно предложение"]
    if kind in ("chart_series", "chart_share"):
        insight = caps["insight"]
        schema = S.obj({
            "insight": S.string(insight),
            "value_axis_title": S.nullable(S.string(30)),
            "category_labels": S.nullable(S.array(S.obj({"row": S.string(10), "label": S.string(CATEGORY_LABEL_MAX)}))),
        })
        return schema, [f"- вывод (insight): до {insight} знаков — что показывает диаграмма, без новых чисел",
                        "- value_axis_title: подпись оси значений с единицей (только для столбцов и линии) или null",
                        f"- category_labels: короткие подписи категорий до {CATEGORY_LABEL_MAX} знаков, "
                        "только если подписи из источника длиннее; иначе null"]
    raise NotImplementedError(f"CONTENT for kind {kind}")


def content_errors(kind: str, parsed: BaseModel, layout: LayoutSpec, n: int | None) -> list[str]:
    """Пределы, которые провайдер мог не соблюсти (json_object или strict без maxLength)."""
    errors = []
    if kind in ("bullets", "conclusion", "metrics"):
        lo, hi = item_bounds(layout, n)
        if not lo <= len(parsed.items) <= hi:
            errors.append(f"элементов {len(parsed.items)}, нужно от {lo} до {hi}")
    if kind == "process":
        lo, hi = item_bounds(layout, n)
        if not lo <= len(parsed.steps) <= hi:
            errors.append(f"шагов {len(parsed.steps)}, нужно от {lo} до {hi}")
    if kind == "comparison":
        for name, side in (("left", parsed.left), ("right", parsed.right)):
            if not COMPARISON_POINTS[0] <= len(side.points) <= COMPARISON_POINTS[1]:
                errors.append(f"{name}: пунктов {len(side.points)}, нужно от 2 до 4")
    if kind == "metrics":
        for i, item in enumerate(parsed.items, start=1):
            if item.status not in STATUSES:
                errors.append(f"число {i}: status вне списка {STATUSES}")
    return errors


def data_block(data: dict | None, language: str = "ru", related: list[dict] | None = None) -> str:
    """Данные диаграммы для задания CONTENT: модель пишет вывод по ним, а числа ставит код."""
    if not data:
        return ""
    from core.ingest.numbers import format_number
    lines = ["ДАННЫЕ ДИАГРАММЫ (их нарисует система; не повторяй таблицу в выводе, новых чисел не придумывай):"]
    for s_ in data["series"]:
        unit = f", {s_['unit']}" if s_.get("unit") else ""
        values = ", ".join(f"{c['row']} {c['label']} — " + ("—" if v is None else format_number(v, language))
                           for c, v in zip(data["categories"], s_["values"]))
        lines.append(f"- {s_['name']}{unit}: {values}")
    if related:
        lines.append("ДРУГИЕ КОЛОНКИ ЭТОЙ ТАБЛИЦЫ по тем же строкам (на диаграмме их нет; главные значения — "
                     "рост, падение, выполнение плана, причина — назови в выводе, как в источнике):")
        for col in related:
            unit = f", {col['unit']}" if col.get("unit") else ""
            values = ", ".join(f"{c['label']} — {raw or '—'}" for c, raw in zip(data["categories"], col["raw"]))
            lines.append(f"- {col['name']}{unit}: {values}")
    return "\n".join(lines)


# ── Промпты ──────────────────────────────────────────────────────────────────

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


def task_prompt(planned: PlannedSlide, index: int, total: int, kind: str, capacity_lines: list[str],
                data: dict | None = None, extra: str | None = None, language: str = "ru",
                related: list[dict] | None = None) -> str:
    text = P.render(
        "content/task.txt", index=index, total=total, kind=kind, role=planned.role, title=planned.title,
        key_message=planned.key_message, refs=", ".join(planned.refs) or "—",
        kind_rules=P.text(f"content/kinds/{kind}.txt"),
        capacity=P.render("content/capacity.txt", lines="\n".join(capacity_lines)),
    )
    if data:
        text += "\n\n" + data_block(data, language, related)
    if extra:
        text += "\n\n" + extra
    return text


# ── Наполнение ───────────────────────────────────────────────────────────────

def fallback_content(planned: PlannedSlide) -> dict:
    """Запасной слайд без LLM: утверждение = заголовок, пояснение = ключевая мысль."""
    body = planned.key_message if planned.key_message.strip() != planned.title.strip() else None
    return {"body": body}


async def fill_slide(client: LLMClient, usage: DeckUsage, system: str, planned: PlannedSlide, kind: str,
                     variant: str, items: int | None, index: int, total: int, slide_id: str,
                     data: dict | None = None, extra: str | None = None, language: str = "ru",
                     stage: str = "content", attempts: int = CONTENT_ATTEMPTS,
                     related: list[dict] | None = None) -> tuple[dict, str, str | None]:
    """→ (content, variant, причина деградации или None). extra — блок «ошибки проверки» (FIT)."""
    layout = load_layouts()[variant]
    schema, cap_lines = content_schema(kind, layout, items)
    try:
        parsed = await client.structured(
            stage=stage, system=system,
            user=task_prompt(planned, index, total, kind, cap_lines, data, extra, language, related),
            schema_name=f"slide_{kind}", schema=schema, model_cls=MODELS[kind],
            visible_tokens=CONTENT_VISIBLE_TOKENS, timeout=CONTENT_TIMEOUT, attempts=attempts,
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
        if kind == "metrics":
            item["tag"] = STATUS_TAGS.get(item.get("status"))
    return content, variant, None
