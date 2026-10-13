"""CONTENT: наполнение слайдов, один вызов LLM на слайд, параллельно (06_PROMPTS.md, 1, 4).

Схема ответа собирается для конкретного слайда: kind задаёт структуру
(04_CONTRACTS.md, 4.2). Пределов длины в схеме нет (D-071: при strict они обрывают генерацию
посреди слова): вместимость варианта — в тексте задания (блок вместимости), после ответа длину
проверяет код. Превышение жёсткого предела (вместимость на минимальном кегле) — повтор с
просьбой сократить поле до N знаков; на последней попытке ответ принимается, а вписывание без
обрезки доводит FIT (core.fitting.resolve).

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


POLARITIES = ["neutral", "pros_cons", "before_after"]


class ComparisonContent(BaseModel):
    left: Side
    right: Side
    polarity: str = "neutral"


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
AXIS_TITLE_MAX = 30


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


def hard_caps(layout: LayoutSpec, n: int | None) -> dict[str, int]:
    """Вместимость слотов на минимальном кегле: больше — текст не влезет ни при каком шаге."""
    caps = {name: capacity(e, e.min_style) for name, e in layout.text_slots().items()}
    if layout.arrangements and n:
        lo, _ = layout.item_range
        for k in range(lo, n + 1):
            for name, e in layout.text_slots(k).items():
                if name.startswith("item."):
                    caps[name] = min(caps.get(name, 10 ** 6), capacity(e, e.min_style))
    return caps


def content_schema(kind: str, layout: LayoutSpec, n: int | None) -> tuple[dict, list[str]]:
    """(JSON Schema ответа, строки блока вместимости) для слайда. Пределы — только в тексте."""
    caps = _slot_caps(layout, n)
    if kind == "statement":
        body = caps.get("body", 200)
        return (S.obj({"body": S.nullable(S.string())}),
                [f"- пояснение (body): до {body} знаков, 1–2 предложения; можно null"])
    if kind == "bullets":
        lo, hi = item_bounds(layout, n)
        head, text = caps["item.heading"], caps["item.text"]
        schema = S.obj({"items": S.array(S.obj({
            "heading": S.string(), "text": S.string(), "icon": S.enum(list(icon_names())),
        }), min_items=lo)})
        return schema, [f"- пунктов: от {lo} до {hi}",
                        f"- заголовок пункта (heading): до {head} знаков",
                        f"- текст пункта (text): до {text} знаков — одно предложение",
                        f"- icon — одно из: {', '.join(icon_names())}"]
    if kind == "conclusion":
        lo, hi = item_bounds(layout, n)
        text = caps["item.text"]
        schema = S.obj({"items": S.array(S.obj({"text": S.string()}), min_items=lo)})
        return schema, [f"- выводов: от {lo} до {hi}", f"- вывод (text): до {text} знаков — одно предложение"]
    if kind == "metrics":
        lo, hi = item_bounds(layout, n)
        value_cap = value_unit_cap(layout, hi)
        label, body = caps["item.label"], caps.get("body", 150)
        schema = S.obj({
            "items": S.array(S.obj({
                "value": S.string(), "unit": S.nullable(S.string()),
                "label": S.string(), "status": S.enum(STATUSES),
                "source_ref": S.nullable(S.string()),
            }), min_items=lo),
            "body": S.nullable(S.string()),
        })
        return schema, [f"- чисел: от {lo} до {hi}",
                        f"- значение и единица (value + unit) вместе: до {value_cap} знаков, одной строкой; "
                        "value — ровно как в источнике",
                        "- единица (unit): коротко («%», «млн руб.», «чел.») или null",
                        f"- подпись (label): до {label} знаков — к чему относится число",
                        f"- пояснение (body): до {body} знаков или null"]
    if kind == "comparison":
        head, point = caps["left_header"], caps["left_point1"]
        side = S.obj({"header": S.string(), "points": S.array(S.string(), COMPARISON_POINTS[0])})
        return S.obj({"left": side, "right": side, "polarity": S.enum(POLARITIES)}), [
            "- две стороны: left и right; polarity — neutral, pros_cons или before_after",
            f"- заголовок стороны (header): до {head} знаков",
            f"- пунктов у каждой стороны: от {COMPARISON_POINTS[0]} до {COMPARISON_POINTS[1]}, пункт — до {point} знаков"]
    if kind == "process":
        lo, hi = item_bounds(layout, n)
        label, text = caps["item.label"], caps["item.text"]
        schema = S.obj({"steps": S.array(S.obj({"label": S.string(), "text": S.string()}), min_items=lo)})
        return schema, [f"- шагов: от {lo} до {hi}, по порядку",
                        f"- название шага (label): до {label} знаков",
                        f"- пояснение (text): до {text} знаков — одно предложение"]
    if kind in ("chart_series", "chart_share"):
        insight = caps["insight"]
        schema = S.obj({
            "insight": S.string(),
            "value_axis_title": S.nullable(S.string()),
            "category_labels": S.nullable(S.array(S.obj({"row": S.string(), "label": S.string()}))),
        })
        return schema, [f"- вывод (insight): до {insight} знаков — что показывает диаграмма, без новых чисел",
                        "- value_axis_title: подпись оси значений с единицей до 30 знаков (только для столбцов и "
                        "линии) или null",
                        f"- category_labels: короткие подписи категорий до {CATEGORY_LABEL_MAX} знаков, "
                        "только если подписи из источника длиннее; иначе null"]
    raise NotImplementedError(f"CONTENT for kind {kind}")


def value_unit_cap(layout: LayoutSpec, n: int) -> int:
    """Знаков у числа с единицей одной строкой: по слоту значения на минимальном кегле."""
    value_el = layout.text_slots(n)["item.value"]
    return capacity(value_el, value_el.min_style)


def _fields(kind: str, parsed: BaseModel) -> list[tuple[str, str, str | None]]:
    """(ключ слота, путь для сообщения, текст) всех текстов ответа."""
    out: list[tuple[str, str, str | None]] = []
    if kind == "statement":
        out.append(("body", "body", parsed.body))
    elif kind == "bullets":
        for i, it in enumerate(parsed.items, 1):
            out += [("item.heading", f"items[{i}].heading", it.heading), ("item.text", f"items[{i}].text", it.text)]
    elif kind == "conclusion":
        out += [("item.text", f"items[{i}].text", it.text) for i, it in enumerate(parsed.items, 1)]
    elif kind == "metrics":
        for i, it in enumerate(parsed.items, 1):
            out += [("item.label", f"items[{i}].label", it.label),
                    ("item.value", f"items[{i}].value+unit", f"{it.value} {it.unit or ''}".strip())]
        out.append(("body", "body", parsed.body))
    elif kind == "comparison":
        for side in ("left", "right"):
            sd = getattr(parsed, side)
            out.append(("left_header", f"{side}.header", sd.header))
            out += [("left_point1", f"{side}.points[{i}]", p) for i, p in enumerate(sd.points, 1)]
    elif kind == "process":
        for i, st in enumerate(parsed.steps, 1):
            out += [("item.label", f"steps[{i}].label", st.label), ("item.text", f"steps[{i}].text", st.text)]
    elif kind in ("chart_series", "chart_share"):
        out.append(("insight", "insight", parsed.insight))
    return out


def length_remarks(kind: str, parsed: BaseModel, layout: LayoutSpec, n: int | None) -> list[str]:
    """Тексты длиннее, чем слот вмещает на минимальном кегле: «сократи до N знаков» (повтор).
    На последней попытке ответ принимается — вписывание доводит FIT, без обрезки (D-071)."""
    caps = hard_caps(layout, n)
    if kind == "metrics":
        _, hi = item_bounds(layout, n)
        caps["item.value"] = value_unit_cap(layout, hi)
    out = []
    for key, path, text in _fields(kind, parsed):
        cap = caps.get(key)
        if text and cap and len(text) > cap:
            out.append(f"{path}: {len(text)} знаков — сократи до {cap}, законченной фразой, без многоточия")
        if text and text.rstrip().endswith(("…", "...")):
            out.append(f"{path}: многоточие в конце — нужна законченная фраза")
    if kind in ("chart_series", "chart_share"):
        for c in parsed.category_labels or []:
            if len(c.label) > CATEGORY_LABEL_MAX:
                out.append(f"category_labels {c.row}: {len(c.label)} знаков — сократи до {CATEGORY_LABEL_MAX}")
        if parsed.value_axis_title and len(parsed.value_axis_title) > AXIS_TITLE_MAX:
            out.append(f"value_axis_title: {len(parsed.value_axis_title)} знаков — сократи до {AXIS_TITLE_MAX}")
    return out


def content_errors(kind: str, parsed: BaseModel, layout: LayoutSpec, n: int | None) -> list[str]:
    """Без исправления ответ не принимается: меньше пунктов, чем нужно, значения вне перечислений."""
    errors = []
    lo, _ = item_bounds(layout, n) if layout.arrangements else (0, 0)
    if kind in ("bullets", "conclusion", "metrics") and len(parsed.items) < lo:
        errors.append(f"элементов {len(parsed.items)}, нужно не меньше {lo}")
    if kind == "process" and len(parsed.steps) < lo:
        errors.append(f"шагов {len(parsed.steps)}, нужно не меньше {lo}")
    if kind == "comparison":
        if parsed.polarity not in POLARITIES:
            errors.append(f"polarity вне списка {POLARITIES}")
        for name, side in (("left", parsed.left), ("right", parsed.right)):
            if len(side.points) < COMPARISON_POINTS[0]:
                errors.append(f"{name}: пунктов {len(side.points)}, нужно от 2 до 4")
    if kind == "metrics":
        for i, item in enumerate(parsed.items, start=1):
            if item.status not in STATUSES:
                errors.append(f"число {i}: status вне списка {STATUSES}")
    return errors


def count_remarks(kind: str, parsed: BaseModel, layout: LayoutSpec, n: int | None) -> list[str]:
    """Пунктов больше предела (в схеме maxItems нет, D-071): повтор; на последней попытке ответ
    принимается — лишние пункты уходят во второй слайд (core.fitting.resolve.regroup), не теряются."""
    out = []
    _, hi = item_bounds(layout, n) if layout.arrangements else (0, 0)
    if kind in ("bullets", "conclusion", "metrics") and hi and len(parsed.items) > hi:
        out.append(f"элементов {len(parsed.items)}, нужно не больше {hi} — объедини или убери второстепенное")
    if kind == "process" and hi and len(parsed.steps) > hi:
        out.append(f"шагов {len(parsed.steps)}, нужно не больше {hi}")
    if kind == "comparison":
        for name, side in (("left", parsed.left), ("right", parsed.right)):
            if len(side.points) > COMPARISON_POINTS[1]:
                out.append(f"{name}: пунктов {len(side.points)}, нужно не больше {COMPARISON_POINTS[1]}")
    return out


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

def outline_text(subtitle: str, plan: list[PlannedSlide], wishes: list[str] | None = None) -> str:
    lines = ["Слайд 1 — титул: тема пользователя (ставит система). Подзаголовок: " + subtitle]
    if wishes:
        lines.insert(0, "Пожелания пользователя к презентации: " + "; ".join(wishes))
    for i, s in enumerate(plan, start=2):
        lines.append(f"Слайд {i} [{s.kind}, {s.role}]: {s.title} — {s.key_message}")
    lines.append(f"Слайд {len(plan) + 2} — финальный (ставит система).")
    return "\n".join(lines)


def system_prompt(request: DeckRequest, digest: SourceDigest, subtitle: str, plan: list[PlannedSlide],
                  wishes: list[str] | None = None) -> str:
    common = P.common_blocks(mode=request.mode, source_mode=request.source_mode,
                             language=request.language, audience=request.audience)
    common.pop("audience")
    return P.render("content/system.txt", digest=digest_text(digest),
                    outline=outline_text(subtitle, plan, wishes), **common)


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
                     related: list[dict] | None = None,
                     soft=None) -> tuple[dict, str, str | None]:
    """→ (content, variant, причина деградации или None). extra — блок «ошибки проверки» (FIT);
    soft — дополнительные замечания к ответу (проверка текста, D-074)."""
    layout = load_layouts()[variant]
    schema, cap_lines = content_schema(kind, layout, items)
    try:
        parsed = await client.structured(
            stage=stage, system=system,
            user=task_prompt(planned, index, total, kind, cap_lines, data, extra, language, related),
            schema_name=f"slide_{kind}", schema=schema, model_cls=MODELS[kind],
            visible_tokens=CONTENT_VISIBLE_TOKENS, timeout=CONTENT_TIMEOUT, attempts=attempts,
            usage=usage, validate=lambda p: content_errors(kind, p, layout, items),
            soft=lambda p: (count_remarks(kind, p, layout, items) + length_remarks(kind, p, layout, items)
                            + (soft(p) if soft else [])),
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
