"""SELECT: выбор варианта макета кодом (05_LAYOUTS.md, 4; D-002).

1. Фильтр применимости по applies_when: число элементов, картинка, длина
   заголовка-утверждения (по вместимости на минимальном стиле).
2. Скоринг: −3 за каждое использование варианта в колоде, −2 за ту же визуальную
   семью у соседа слева, +2 за совпадение роли, шум U(0; 0,5) от hash(seed, slide_id).
3. Максимальный скор; при равенстве — порядок каталога.
4. Нет применимых → запасной вариант kind; не применим и он → bullets.

В сессии 1 у каждого kind по одному варианту — алгоритм тот же, выбор
появится, когда вариантов станет больше (сессия 3).
"""

import hashlib
import random
from dataclasses import dataclass

from core.models.layout import LayoutSpec, capacity, load_layouts, variants_of

ROLE_BONUS = {
    "ask": {"statement.highlight_band"},
    "criteria": {"bullets.numbered_columns", "process.vertical_steps"},
    "requirements": {"bullets.numbered_columns", "process.vertical_steps"},
}
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


@dataclass
class Choice:
    kind: str
    variant: str
    items: int | None
    reason: str | None = None   # деградация, если вариант или kind пришлось сменить


def _rng(seed: int, slide_id: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{slide_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def applicable(spec: LayoutSpec, need: SlideNeed) -> bool:
    rule = spec.applies_when
    image = rule.get("image", "optional")
    if image == "required" and not need.has_image:
        return False
    items = rule.get("items")
    if items and need.items is not None and not (items["min"] <= need.items <= items["max"]):
        return False
    limit = rule.get("title_max_chars")
    if limit and need.title:
        slot = next((e for e in spec.elements() if e.bind == "title"), None)
        hard = capacity(slot, slot.min_style) if slot else limit
        if len(need.title) > max(limit, hard):
            return False
    return True


def choose(need: SlideNeed, seed: int, used: dict[str, int], prev_family: str | None) -> Choice:
    candidates = [s for s in variants_of(need.kind) if applicable(s, need)]
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
        score += rng.uniform(0, 0.5)
        if best_score is None or score > best_score:
            best, best_score = spec, score
    return Choice(need.kind, best.id, need.items)


def select_deck(needs: list[SlideNeed], seed: int) -> list[Choice]:
    """Варианты для всех слайдов колоды по порядку."""
    layouts = load_layouts()
    used: dict[str, int] = {}
    prev_family = None
    out = []
    for need in needs:
        choice = choose(need, seed, used, prev_family)
        used[choice.variant] = used.get(choice.variant, 0) + 1
        prev_family = layouts[choice.variant].family
        out.append(choice)
    return out
