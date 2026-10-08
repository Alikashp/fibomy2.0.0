"""SELECT: выбор варианта макета кодом (05_LAYOUTS.md, 4; D-002).

1. Фильтр применимости по applies_when: число элементов, картинка, длина
   заголовка-утверждения (по вместимости на минимальном стиле).
2. Скоринг: −3 за каждое использование варианта в колоде, −2 за ту же визуальную
   семью у соседа слева, +2 за совпадение роли, шум U(0; 0,5) от hash(seed, slide_id).
3. Максимальный скор; при равенстве — порядок каталога.
4. Нет применимых → запасной вариант kind; не применим и он → bullets.

Сессия 4 (D-066): варианты с картинкой — только у слайдов, которым пайплайн выделил картинку
(has_image), и с бонусом, чтобы картинка встала; «цветные» варианты (color_heavy: пауза,
полоса, картинка на цветном фоне) — не ближе трёх слайдов к титулу, финалу и друг к другу;
comparison.pros_cons — только при полярности «за / против» или «было / стало» (известна
после CONTENT, вариант уточняет reselect_comparison); длина заголовка — по метрикам шрифта.
"""

import hashlib
import random
from dataclasses import dataclass

from core.models.layout import LayoutSpec, capacity, load_layouts, variants_of

ROLE_BONUS = {
    "ask": {"statement.highlight_band"},
    "results": {"metrics.big_number"},
    "criteria": {"bullets.numbered_columns", "process.vertical_steps"},
    "requirements": {"bullets.numbered_columns", "process.vertical_steps"},
}
# Ряд по времени из 4+ точек — линия (05_LAYOUTS.md, 3.7)
SHAPE_BONUS = {"chart_series.line_chart": 1.0}
IMAGE_BONUS = 4.0          # картинку выделили — вариант с картинкой почти всегда выигрывает
COLOR_GAP = 3              # «цветные» слайды — не ближе трёх слайдов друг к другу
FALLBACK_KIND = "bullets"


@dataclass
class SlideNeed:
    """Что SELECT знает о слайде до наполнения."""
    slide_id: str
    kind: str
    role: str = "other"
    items: int | None = None
    title: str = ""
    has_image: bool = False
    # Диаграммы: число категорий, серий, ось, сумма долей (из Slide.data)
    points: int | None = None
    series: int | None = None
    axis: str | None = None
    share_sum: float | None = None
    polarity: str | None = None     # comparison: neutral | pros_cons | before_after (после CONTENT)
    index: int = 0                  # номер слайда в колоде (1 — титул)
    total: int = 0


@dataclass
class Choice:
    kind: str
    variant: str
    items: int | None
    reason: str | None = None   # деградация, если вариант или kind пришлось сменить


def _rng(seed: int, slide_id: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{slide_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _title_fits(spec: LayoutSpec, need: SlideNeed, limit: int) -> bool:
    """Заголовок (тема титула или утверждение) влезает на минимальном стиле слота — по метрикам
    шрифта, а не только по числу знаков: длинные казахские и немецкие слова занимают больше."""
    from core.fitting.fitter import fit_text
    from core.models.theme import DEFAULT_THEME, load_theme
    slot = next((e for e in spec.elements() if e.bind in ("title", "meta.title")), None)
    if slot is None:
        return len(need.title) <= limit
    hard = capacity(slot, slot.min_style)
    if len(need.title) > max(limit, hard):
        return False
    return fit_text(slot, need.title, load_theme(DEFAULT_THEME)).fits


def applicable(spec: LayoutSpec, need: SlideNeed, last_color: int | None = None) -> bool:
    rule = spec.applies_when
    image = rule.get("image", "optional")
    if image == "required" and not need.has_image:
        return False
    polarity = rule.get("polarity")
    if polarity and need.polarity not in polarity:
        return False
    if spec.color_heavy and need.index and need.total:
        if last_color is not None and need.index - last_color < COLOR_GAP:
            return False
        if need.total - need.index < COLOR_GAP:          # финал — тоже цветной
            return False
    items = rule.get("items")
    if items and need.items is not None and not (items["min"] <= need.items <= items["max"]):
        return False
    points = rule.get("points")
    if points and need.points is not None and not (points.get("min", 0) <= need.points <= points.get("max", 10 ** 6)):
        return False
    series = rule.get("series")
    if series and need.series is not None and need.series > series.get("max", 10 ** 6):
        return False
    if rule.get("axis") and need.axis != rule["axis"]:
        return False
    share = rule.get("share_sum")
    if share and (need.share_sum is None or not share["min"] <= need.share_sum <= share["max"]):
        return False
    limit = rule.get("title_max_chars")
    if limit and need.title and not _title_fits(spec, need, limit):
        return False
    return True


def choose(need: SlideNeed, seed: int, used: dict[str, int], prev_family: str | None,
           last_color: int | None = None) -> Choice:
    candidates = [s for s in variants_of(need.kind) if applicable(s, need, last_color)]
    if not candidates and need.kind == "chart_share" and need.points:
        # Доли не сходятся в 95–105% или долей больше 6 → столбцы по тем же данным (05_LAYOUTS.md, 5)
        alt = SlideNeed(need.slide_id, "chart_series", need.role, None, need.title, need.has_image,
                        need.points, need.series, need.axis, None, index=need.index, total=need.total)
        chosen = choose(alt, seed, used, prev_family, last_color)
        chosen.reason = chosen.reason or "kind_fallback:chart_share->chart_series"
        return chosen
    if not candidates:
        # Запасной вариант kind тоже не применим (он самый вместительный) → bullets
        fallback = next((s for s in variants_of(FALLBACK_KIND) if s.fallback), None)
        lo, hi = fallback.item_range
        items = min(max(need.items or lo, lo), hi)
        return Choice(FALLBACK_KIND, fallback.id, items, reason=f"kind_fallback:{need.kind}->{FALLBACK_KIND}")

    rng = _rng(seed, need.slide_id)
    best, best_score = None, None
    for spec in candidates:
        score = -3.0 * used.get(spec.id, 0)
        if prev_family and spec.family == prev_family:
            score -= 2.0
        if spec.id in ROLE_BONUS.get(need.role, ()):
            score += 2.0
        score += SHAPE_BONUS.get(spec.id, 0.0)
        if need.has_image and spec.image_slot:
            score += IMAGE_BONUS
        score += rng.uniform(0, 0.5)
        if best_score is None or score > best_score:
            best, best_score = spec, score
    return Choice(need.kind, best.id, need.items)


def select_deck(needs: list[SlideNeed], seed: int) -> list[Choice]:
    """Варианты для всех слайдов колоды по порядку."""
    layouts = load_layouts()
    used: dict[str, int] = {}
    prev_family = None
    last_color = None
    out = []
    for i, need in enumerate(needs, start=1):
        need.index, need.total = need.index or i, need.total or len(needs)
        choice = choose(need, seed, used, prev_family, last_color)
        used[choice.variant] = used.get(choice.variant, 0) + 1
        layout = layouts[choice.variant]
        prev_family = layout.family
        if layout.color_heavy or need.kind == "title":
            last_color = need.index
        out.append(choice)
    return out


def without_image(choice: Choice, need: SlideNeed, seed: int, used: dict[str, int]) -> Choice:
    """Картинка не пришла: тот же kind, вариант без картинки (05_LAYOUTS.md, 5). Цветные варианты
    не берутся — их расстановку SELECT уже проверил."""
    alt = SlideNeed(**{**need.__dict__, "has_image": False})
    candidates = [s for s in variants_of(need.kind) if not s.image_slot and not s.color_heavy
                  and applicable(s, alt)]
    if not candidates:
        candidates = [s for s in variants_of(need.kind) if s.fallback]
    rng = _rng(seed, need.slide_id + ":noimg")
    best = max(candidates, key=lambda s: (-3.0 * used.get(s.id, 0) + rng.uniform(0, 0.5)))
    return Choice(need.kind, best.id, choice.items, reason="image_missing")


def reselect_comparison(variant: str, polarity: str | None) -> str:
    """После CONTENT: «за / против» и «было / стало» — панели с ✓ и ✕, иначе — две колонки."""
    if polarity in ("pros_cons", "before_after"):
        return "comparison.pros_cons"
    return "comparison.two_columns" if variant == "comparison.pros_cons" else variant
