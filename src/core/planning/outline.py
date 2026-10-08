"""OUTLINE: один вызов LLM — анализ источника и план содержательных слайдов (06_PROMPTS.md, 1; D-032).

Проверки кода после ответа (04_CONTRACTS.md, 4.1):
- ошибки, без исправления которых план не принимается (повтор с перечнем ошибок): мало слайдов,
  дубли заголовков и ключевых мыслей, пустые поля;
- замечания (повтор, но на последней попытке план принимается и замечание пишется в degradations):
  разнообразие формы — bullets не больше 40% слайдов, два слайда одного kind подряд, statement не больше
  трети; диаграмма без годного набора данных; один набор и колонка на двух диаграммах;
- исправления без повтора: лишние слайды отрезаются, «по теме» chart_* → bullets, items_planned — в пределы
  kind, слайд role = ask — последним (нет, а в analysis.asks запрос есть — код добавляет его, D-036),
  диаграмма без годных данных → metrics или bullets.
"""

import logging
import math
import re

from core.ingest.render import digest_text
from core.llm import prompts as P
from core.llm.client import DeckUsage, LLMClient
from core.models.digest import Analysis, SourceDigest
from core.models.layout import variants_of
from core.models.outline import CONTENT_KINDS, OutlineResponse, PlannedSlide, outline_schema
from core.models.request import DeckRequest
from core.planning.datasets import coverage_remarks, DatasetError, resolve

logger = logging.getLogger(__name__)

OUTLINE_TIMEOUT = 45.0
OUTLINE_ATTEMPTS = 2
OUTLINE_VISIBLE_TOKENS = 5000

# Пределы числа элементов по kind (схемы CONTENT, 04_CONTRACTS.md, 4.2)
ITEM_LIMITS = {"bullets": (3, 6), "conclusion": (2, 5), "process": (3, 6), "metrics": (1, 4)}
TOPIC_FORBIDDEN = {"chart_series", "chart_share"}  # ТЗ 3.3.3, вопрос 9
CHART_KINDS = {"chart_series", "chart_share"}
BULLETS_MAX_SHARE = 0.4
GENRE_STORYLINES = ("doklad_report", "doklad_requirements", "doklad_plan", "doklad_article", "doklad_other")


def available_kinds(request: DeckRequest) -> tuple[str, ...]:
    """kinds, которые план может предложить: у kind есть хотя бы один вариант макета,
    «по теме» — без диаграмм."""
    kinds = [k for k in CONTENT_KINDS if variants_of(k)]
    if request.mode == "topic":
        kinds = [k for k in kinds if k not in TOPIC_FORBIDDEN]
    return tuple(kinds)


def content_slide_count(request: DeckRequest) -> int:
    return request.total_slides - 2


def is_strict(request: DeckRequest) -> bool:
    return request.mode == "material" and request.source_mode != "extend"


def storyline_text(name: str) -> str:
    story = P.data(f"outline/storylines/{name}.yaml")
    lines = [story.get("intro", "").strip()]
    for i, block in enumerate(story["blocks"], start=1):
        lines.append(f"{i}. {block['hint']}.")
    return "\n".join(line for line in lines if line)


def storylines_all() -> str:
    """Сюжеты всех жанров материала: жанр определяет модель в том же вызове (D-032)."""
    parts = []
    for name in GENRE_STORYLINES:
        story = P.data(f"outline/storylines/{name}.yaml")
        blocks = "; ".join(f"{b['id']} ({b['role']}) — {b['hint']}" for b in story["blocks"])
        parts.append(f"- {story['title']}: {blocks}.")
    return "\n".join(parts)


def build_prompts(request: DeckRequest, digest: SourceDigest, n: int, kinds: tuple[str, ...]) -> tuple[str, str]:
    common = P.common_blocks(mode=request.mode, source_mode=request.source_mode,
                             language=request.language, audience=request.audience)
    kind_lines = P.data("outline/kinds.yaml")
    system = P.render(
        "outline/system.txt",
        kinds="\n".join(f"  {kind_lines[k]}" for k in kinds),
        slide_count=P.render_value(P.data("outline/slide_count.yaml")["strict" if is_strict(request) else "exact"], n=n),
        **common,
    )
    if request.mode == "topic":
        system += "\n\n" + P.render("outline/rules_topic.txt", storyline=storyline_text("doklad_topic"))
        source = ""
    else:
        system += "\n\n" + P.render("outline/rules_material.txt", storylines=storylines_all())
        source = "ИСТОЧНИК\n" + digest_text(digest)
    user = P.render("outline/user.txt", presentation_type=request.presentation_type,
                    topic=request.input.topic, n=n, source=source)
    return system, user.rstrip()


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def plan_errors(resp: OutlineResponse, request: DeckRequest, n: int) -> list[str]:
    """Ошибки плана, без исправления которых он не принимается."""
    slides = resp.deck.slides
    errors = []
    if is_strict(request) and len(slides) < 3:
        errors.append(f"в плане {len(slides)} слайдов, нужно от 3 до {n}")
    if not is_strict(request) and len(slides) < n - 1:
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


def diversity_remarks(kinds: list[str]) -> list[str]:
    """Разнообразие формы (замечания владельца 01.10.2026): bullets ≤ 40%, без двух одинаковых kind подряд,
    statement ≤ трети. Слайд-запрос (statement) в счёт statement не входит — его требует источник."""
    out = []
    n = len(kinds)
    if n == 0:
        return out
    bullets_max = max(1, math.floor(n * BULLETS_MAX_SHARE + 1e-9))
    if kinds.count("bullets") > bullets_max:
        out.append(f"bullets на {kinds.count('bullets')} слайдах из {n} — не больше {bullets_max}: "
                   f"этапы сделай process, противопоставления — comparison, числа — metrics")
    for i in range(1, n):
        if kinds[i] == kinds[i - 1]:
            out.append(f"слайды {i} и {i + 1} подряд одного kind ({kinds[i]}) — смени форму одного из них")
    statements_max = max(1, n // 3)
    if kinds.count("statement") > statements_max:
        out.append(f"statement на {kinds.count('statement')} слайдах — не больше {statements_max}")
    return out


def plan_remarks(resp: OutlineResponse, request: DeckRequest, digest: SourceDigest) -> list[str]:
    """Замечания: повтор вызова, но на последней попытке план принимается."""
    slides = resp.deck.slides
    kinds = [s.kind for s in slides if s.role != "ask"]
    out = diversity_remarks(kinds)
    used = {}
    for i, s in enumerate(slides, start=1):
        if s.kind in CHART_KINDS:
            try:
                data = resolve(s, digest, s.kind, request.language)
            except DatasetError as e:
                out.append(f"слайд {i} ({s.kind}): {e}")
                continue
            for series in data["series"]:
                key = (data["dataset_id"], series["column"])
                if key in used:
                    out.append(f"слайды {used[key]} и {i}: одна колонка {key[0]}:{key[1]} на двух диаграммах")
                used[key] = i
    if request.mode != "topic" and any(k in CHART_KINDS for k in available_kinds(request)):
        out += coverage_remarks({k[0] for k in used}, digest)
    return out


def normalize_plan(resp: OutlineResponse, request: DeckRequest, n: int, kinds: tuple[str, ...], degrade,
                   digest: SourceDigest | None = None) -> tuple[list[PlannedSlide], list[dict | None]]:
    """Исправления кодом без повтора вызова. → (план, снимки данных диаграмм по слайдам)."""
    slides = [s.model_copy(deep=True) for s in resp.deck.slides]
    digest = digest or SourceDigest.for_topic(request.input.topic)

    for s in slides:
        if s.kind not in kinds:
            degrade(f"kind_unavailable:{s.kind}->bullets")
            s.kind, s.dataset = "bullets", None
        if s.kind in TOPIC_FORBIDDEN and request.mode == "topic":
            degrade(f"chart_in_topic:{s.kind}->bullets")
            s.kind, s.dataset = "bullets", None
        if s.role == "ask":
            s.kind, s.dataset = "statement", None
        if s.kind not in CHART_KINDS:
            s.dataset = None

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
    if len(slides) < n and not is_strict(request):
        degrade(f"outline_short:{len(slides)}<{n}")

    datas: list[dict | None] = []
    for s in slides:
        data = None
        if s.kind in CHART_KINDS:
            try:
                data = resolve(s, digest, s.kind, request.language)
            except DatasetError as e:
                if s.kind == "chart_share":
                    try:  # доли не сходятся — тот же ряд столбцами (05_LAYOUTS.md, 5)
                        data = resolve(s, digest, "chart_series", request.language)
                        degrade(f"chart_share->chart_series:{e}")
                        s.kind = "chart_series"
                    except DatasetError:
                        data = None
                if data is None:
                    degrade(f"chart_without_data->bullets:{e}")
                    s.kind, s.dataset = "bullets", None
        lo_hi = ITEM_LIMITS.get(s.kind)
        default = 3 if s.kind == "metrics" else lo_hi[0] if lo_hi else None
        s.items_planned = min(max(s.items_planned or default, lo_hi[0]), lo_hi[1]) if lo_hi else None
        datas.append(data)

    for remark in diversity_remarks([s.kind for s in slides if s.role != "ask"]):
        degrade(f"plan_diversity:{remark[:80]}")
    return slides, datas


def _trim(slides: list[PlannedSlide], n: int) -> list[PlannedSlide]:
    """Лишние слайды убираются с конца середины: выводы и запрос сохраняются."""
    keep_tail = [s for s in slides[-2:] if s.role in ("ask", "conclusion") or s.kind == "conclusion"]
    body = slides[:len(slides) - len(keep_tail)]
    return body[:max(0, n - len(keep_tail))] + keep_tail


async def plan_deck(request: DeckRequest, digest: SourceDigest, client: LLMClient, usage: DeckUsage,
                    degrade) -> tuple[OutlineResponse, list[PlannedSlide], list[dict | None]]:
    n = content_slide_count(request)
    kinds = available_kinds(request)
    system, user = build_prompts(request, digest, n, kinds)
    resp = await client.structured(
        stage="outline", system=system, user=user, schema_name="outline",
        schema=outline_schema(kinds, min_slides=1, max_slides=n + 2),
        model_cls=OutlineResponse, visible_tokens=OUTLINE_VISIBLE_TOKENS, timeout=OUTLINE_TIMEOUT,
        attempts=OUTLINE_ATTEMPTS, usage=usage, validate=lambda r: plan_errors(r, request, n),
        soft=lambda r: plan_remarks(r, request, digest),
    )
    if request.mode == "topic" and resp.analysis.genre != "topic":
        degrade(f"genre:{resp.analysis.genre}->topic")
        resp.analysis.genre = "topic"
    digest.analysis = Analysis(genre=resp.analysis.genre,
                               theses=[t.model_dump() for t in resp.analysis.theses],
                               asks=[a.model_dump() for a in resp.analysis.asks],
                               missing=resp.analysis.missing)
    plan, datas = normalize_plan(resp, request, n, kinds, degrade, digest)
    return resp, plan, datas
