"""OUTLINE: один вызов LLM — анализ источника и план содержательных слайдов (06_PROMPTS.md, 1; D-032).

Проверки кода после ответа (04_CONTRACTS.md, 4.1):
- ошибки, которые чинит повтор вызова (с перечнем ошибок): мало слайдов, дубли
  заголовков и ключевых мыслей, пустые поля;
- исправления без повтора: лишние слайды отрезаются, «по теме» chart_* → bullets,
  items_planned приводится к пределам kind, слайд role = ask переносится в конец
  (если в analysis.asks есть запрос, а слайда нет — код добавляет его, D-036).
"""

import logging
import re

from core.llm import prompts as P
from core.llm.client import DeckUsage, LLMClient
from core.models.digest import Analysis, SourceDigest
from core.models.layout import variants_of
from core.models.outline import CONTENT_KINDS, OutlineResponse, PlannedSlide, outline_schema
from core.models.request import DeckRequest

logger = logging.getLogger(__name__)

OUTLINE_TIMEOUT = 45.0
OUTLINE_ATTEMPTS = 2
OUTLINE_VISIBLE_TOKENS = 4000

# Пределы числа элементов по kind (схемы CONTENT, 04_CONTRACTS.md, 4.2)
ITEM_LIMITS = {"bullets": (3, 6), "conclusion": (2, 5), "process": (3, 6), "metrics": (1, 4)}
TOPIC_FORBIDDEN = {"chart_series", "chart_share"}  # ТЗ 3.3.3, вопрос 9


def available_kinds(request: DeckRequest) -> tuple[str, ...]:
    """kinds, которые план может предложить: у kind есть хотя бы один вариант макета,
    «по теме» — без диаграмм."""
    kinds = [k for k in CONTENT_KINDS if variants_of(k)]
    if request.mode == "topic":
        kinds = [k for k in kinds if k not in TOPIC_FORBIDDEN]
    return tuple(kinds)


def content_slide_count(request: DeckRequest) -> int:
    return request.total_slides - 2


def storyline_text(name: str) -> str:
    story = P.data(f"outline/storylines/{name}.yaml")
    lines = [story.get("intro", "").strip()]
    for i, block in enumerate(story["blocks"], start=1):
        lines.append(f"{i}. {block['hint']}.")
    return "\n".join(line for line in lines if line)


def build_prompts(request: DeckRequest, digest: SourceDigest, n: int, kinds: tuple[str, ...]) -> tuple[str, str]:
    common = P.common_blocks(mode=request.mode, source_mode=request.source_mode,
                             language=request.language, audience=request.audience)
    kind_lines = P.data("outline/kinds.yaml")
    count_rule = "strict" if request.mode == "material" and request.source_mode != "extend" else "exact"
    system = P.render(
        "outline/system.txt",
        kinds="\n".join(f"  {kind_lines[k]}" for k in kinds),
        slide_count=P.render_value(P.data("outline/slide_count.yaml")[count_rule], n=n),
        **common,
    )
    if request.mode == "topic":
        system += "\n\n" + P.render("outline/rules_topic.txt", storyline=storyline_text("doklad_topic"))
    user = P.render("outline/user.txt", presentation_type=request.presentation_type,
                    topic=request.input.topic, n=n, source="")
    return system, user.rstrip()


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def plan_errors(resp: OutlineResponse, request: DeckRequest, n: int) -> list[str]:
    """Ошибки плана, которые исправляет повтор вызова."""
    slides = resp.deck.slides
    errors = []
    strict = request.mode == "material" and request.source_mode != "extend"
    if strict and len(slides) < 3:
        errors.append(f"в плане {len(slides)} слайдов, нужно от 3 до {n}")
    if not strict and len(slides) < n - 1:
        errors.append(f"в плане {len(slides)} содержательных слайдов, нужно ровно {n}")
    titles = [_norm(s.title) for s in slides]
    dup_titles = {t for t in titles if titles.count(t) > 1}
    if dup_titles:
        errors.append("повторяются заголовки слайдов: " + "; ".join(sorted(dup_titles)))
    messages = [_norm(s.key_message) for s in slides]
    if len(set(messages)) < len(messages):
        errors.append("у двух слайдов одна и та же key_message — одна мысль должна быть на одном слайде")
    for i, s in enumerate(slides, start=1):
        if not s.title.strip() or not s.key_message.strip():
            errors.append(f"слайд {i}: пустой title или key_message")
    if not resp.deck.subtitle.strip():
        errors.append("пустой deck.subtitle")
    return errors


def normalize_plan(resp: OutlineResponse, request: DeckRequest, n: int, kinds: tuple[str, ...],
                   degrade) -> list[PlannedSlide]:
    """Исправления кодом без повтора вызова. degrade(reason) — запись в degradations."""
    slides = [s.model_copy(deep=True) for s in resp.deck.slides]

    for s in slides:
        if s.kind not in kinds:
            degrade(f"kind_unavailable:{s.kind}->bullets")
            s.kind, s.dataset = "bullets", None
        if s.kind in TOPIC_FORBIDDEN and request.mode == "topic":
            degrade(f"chart_in_topic:{s.kind}->bullets")
            s.kind, s.dataset = "bullets", None
        if s.kind != "statement" and s.role == "ask":
            s.kind = "statement"
        lo_hi = ITEM_LIMITS.get(s.kind)
        if lo_hi:
            s.items_planned = min(max(s.items_planned or lo_hi[0], lo_hi[0]), lo_hi[1])
        else:
            s.items_planned = None

    # Слайд-запрос — последний содержательный (D-036)
    asks = [s for s in slides if s.role == "ask"]
    if asks:
        slides = [s for s in slides if s.role != "ask"] + asks[:1]
    elif resp.analysis.asks:
        ask = resp.analysis.asks[0]
        degrade("ask_added_by_code")
        slides.append(PlannedSlide(kind="statement", role="ask", title=ask.text[:150], key_message=ask.text,
                                   refs=ask.refs))

    if len(slides) > n:
        degrade(f"outline_trimmed:{len(slides)}->{n}")
        slides = _trim(slides, n)
    if len(slides) < n and not (request.mode == "material" and request.source_mode != "extend"):
        degrade(f"outline_short:{len(slides)}<{n}")
    return slides


def _trim(slides: list[PlannedSlide], n: int) -> list[PlannedSlide]:
    """Лишние слайды убираются с конца середины: выводы и запрос сохраняются."""
    keep_tail = [s for s in slides[-2:] if s.role in ("ask", "conclusion") or s.kind == "conclusion"]
    body = slides[:len(slides) - len(keep_tail)]
    return body[:max(0, n - len(keep_tail))] + keep_tail


async def plan_deck(request: DeckRequest, digest: SourceDigest, client: LLMClient, usage: DeckUsage,
                    degrade) -> tuple[OutlineResponse, list[PlannedSlide]]:
    n = content_slide_count(request)
    kinds = available_kinds(request)
    system, user = build_prompts(request, digest, n, kinds)
    resp = await client.structured(
        stage="outline", system=system, user=user, schema_name="outline",
        schema=outline_schema(kinds, min_slides=1, max_slides=n + 2),
        model_cls=OutlineResponse, visible_tokens=OUTLINE_VISIBLE_TOKENS, timeout=OUTLINE_TIMEOUT,
        attempts=OUTLINE_ATTEMPTS, usage=usage, validate=lambda r: plan_errors(r, request, n),
    )
    digest.analysis = Analysis(genre=resp.analysis.genre,
                               theses=[t.model_dump() for t in resp.analysis.theses],
                               asks=[a.model_dump() for a in resp.analysis.asks],
                               missing=resp.analysis.missing)
    return resp, normalize_plan(resp, request, n, kinds, degrade)
