"""Фикстуры стресс-галереи (07_TESTING.md, 2): содержание слайда каждого варианта
в трёх заполнениях — min (минимум элементов, короткие тексты), typical (середина,
тексты ~70% вместимости, русский), max (максимум элементов, тексты на базовой
вместимости, длинные казахские и немецкие слова).

Без LLM: тексты собираются из словаря до нужной длины по вместимости слота
(core.content.fill — та же вместимость, что в промпте CONTENT).
"""

import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter

from core.content.fill import _slot_caps, item_bounds
from core.models.layout import LayoutSpec, capacity

FIXTURES = ("min", "typical", "max")

RU = ("система данные процесс команда решение рынок клиент проект качество анализ результат модель "
      "отчёт продажи рост доля студент преподаватель занятие план задача этап проверка поддержка "
      "выручка регион склад маршрут расписание сервис платформа продукт пользователь опыт").split()
LONG = ("Qualitätssicherungsmaßnahmen", "жауапкершілігінің", "мемлекеттендірілмегендіктен",
        "Arbeitsplatzbeschreibung", "қамтамасыз", "ұйымдастырушылық")
NUMBERS = {"min": ["12%", "3"], "typical": ["47,3", "1 250", "+18%", "4,5"], "max": ["2 050 000", "−6,2%", "47,3 млн", "98,6%"]}


def words(n_chars: int, fixture: str, seed: int, sentence: bool = True) -> str:
    """Текст ≈ n_chars знаков (не длиннее) из словаря; в max — с длинными словами."""
    rng = random.Random(seed)
    out: list[str] = []
    pool = RU + (list(LONG) * 2 if fixture == "max" else [])
    while True:
        w = rng.choice(pool)
        candidate = " ".join(out + [w])
        if len(candidate) + (1 if sentence else 0) > n_chars:
            break
        out.append(w)
    if not out:
        out = [rng.choice([w for w in RU if len(w) <= max(3, n_chars)])[:max(1, n_chars - 1)]]
    text = " ".join(out)
    text = text[0].upper() + text[1:]
    return text + ("." if sentence and len(text) < n_chars else "")


def share(fixture: str) -> float:
    return {"min": 0.35, "typical": 0.7, "max": 1.0}[fixture]


def n_items(layout: LayoutSpec, fixture: str) -> int | None:
    if not layout.arrangements:
        return None
    lo, hi = layout.item_range
    return {"min": lo, "typical": (lo + hi + 1) // 2, "max": hi}[fixture]


def placeholder_image(seed: int, w: int = 1200, h: int = 1200) -> bytes:
    """Картинка-заглушка «как фото»: мягкий градиент неба, холмы, солнце — без текста."""
    rng = random.Random(seed)
    hue = rng.random()
    import colorsys
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.35, 0.95))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.08) % 1, 0.55, 0.55))
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    d.ellipse([w * 0.6, h * 0.15, w * 0.8, h * 0.35], fill=(255, 236, 200))
    for k in range(3):
        base = h * (0.55 + 0.12 * k)
        pts = [(x, base + math.sin(x / w * math.pi * (2 + k) + rng.random() * 3) * h * 0.05) for x in range(0, w + 1, 20)]
        shade = tuple(max(0, int(c * (0.75 - 0.15 * k))) for c in bottom)
        d.polygon(pts + [(w, h), (0, h)], fill=shade)
    img = img.filter(ImageFilter.GaussianBlur(2))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82)
    return out.getvalue()


def chart_data(variant: str, fixture: str) -> dict | None:
    if variant.startswith("chart_share"):
        values = {"min": [62, 38], "typical": [46, 27, 18, 9], "max": [31, 22, 17, 13, 11, 9]}[fixture]
        if fixture == "max":
            values = [v + 0.5 for v in values[:-1]] + [values[-1]]   # сумма 103%
        labels = ["Онлайн", "Розница", "Партнёры", "Корпоративные клиенты", "Экспорт", "Прочее"]
        return {"chart": "donut", "decimals": 1 if fixture == "max" else 0,
                "categories": [{"row": f"r{i + 1}", "label": labels[i]} for i in range(len(values))],
                "series": [{"name": "Доля, %", "unit": "%", "values": values}], "axis": "category",
                "share_sum": sum(values)}
    if variant.startswith("chart_series"):
        line = variant.endswith("line_chart")
        n = {"min": 4 if line else 2, "typical": 6, "max": 12}[fixture]
        months = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
        cats = [{"row": f"r{i + 1}", "label": months[i] if line else ["Север", "Юг", "Запад", "Восток", "Центр",
                 "Урал", "Сибирь", "Дальний Восток", "Поволжье", "Кавказ", "Крым", "Калининград"][i]} for i in range(n)]
        series_n = {"min": 1, "typical": 2, "max": 3}[fixture]
        rng = random.Random(n)
        series = [{"name": f"{2024 + s} г.", "unit": "млн руб.",
                   "values": [round(rng.uniform(-6 if fixture == "max" else 5, 60), 1) for _ in range(n)]}
                  for s in range(series_n)]
        return {"chart": "line" if line else "column", "decimals": 1, "categories": cats, "series": series,
                "axis": "time" if line else "category", "value_axis_title": "млн руб."}
    return None


def _applicable_title(layout: LayoutSpec, title: str) -> str:
    """Самая длинная версия заголовка, которую вариант принимает (SELECT проверяет её по метрикам
    шрифта: длинные слова max не попадут в вариант, где не влезут)."""
    from core.selection.select import SlideNeed, applicable
    words_ = title.split()
    while len(words_) > 1 and not applicable(layout, SlideNeed("s01", layout.kind, title=" ".join(words_),
                                                              has_image=True)):
        words_.pop()
    return " ".join(words_)


def content_for(layout: LayoutSpec, fixture: str, seed: int) -> tuple[str, dict, int | None]:
    """→ (заголовок слайда, content, число элементов) варианта в заполнении fixture."""
    kind = layout.kind
    n = n_items(layout, fixture)
    f = share(fixture)
    slots = layout.text_slots(n)
    caps = _slot_caps(layout, n) if kind not in ("title", "closing") else {}

    def cap(slot, default=60):
        e = slots.get(slot)
        return capacity(e) if e else default

    title_slot = "title" if "title" in slots else "statement"
    title = words(max(12, int(cap(title_slot) * f)), fixture, seed, sentence=False)
    if kind == "title":
        limit = layout.applies_when.get("title_max_chars", 200)
        title = words({"min": 18, "typical": 60, "max": limit}[fixture], fixture, seed, sentence=False)
        title = _applicable_title(layout, title)
        return title, {"subtitle": words(int(cap("subtitle") * f), fixture, seed + 1),
                       "author": None if fixture == "min" else words(int(cap("author") * f), fixture, seed + 2,
                                                                      sentence=False)}, None
    if kind == "closing":
        return "Спасибо за внимание", {"title": "Спасибо за внимание",
                                        "body": None if fixture == "min" else words(int(cap("body") * f), fixture, seed),
                                        "author": words(40, fixture, seed + 3, sentence=False)}, None
    if kind == "statement":
        limit = layout.applies_when.get("title_max_chars", 150)
        title = _applicable_title(layout, words(max(20, int(min(limit, cap("statement")) * f)), fixture, seed))
        return title, {"body": None if fixture == "min" and "pause" not in layout.id else
                       words(int(caps.get("body", 150) * f), fixture, seed + 1)}, None
    if kind == "bullets":
        lo, hi = item_bounds(layout, n)
        return title, {"items": [{"heading": words(int(caps["item.heading"] * f), fixture, seed + i, sentence=False),
                                  "text": words(int(caps["item.text"] * f), fixture, seed + 10 + i),
                                  "icon": ["bulb", "target", "users", "chart-bar", "clock", "shield"][i]}
                                 for i in range(n)]}, n
    if kind == "conclusion":
        return title, {"items": [{"text": words(int(caps["item.text"] * f), fixture, seed + i)} for i in range(n)]}, n
    if kind == "process":
        label = caps["item.label"]
        if layout.applies_when.get("label_max_chars"):
            label = min(label, layout.applies_when["label_max_chars"])
        return title, {"steps": [{"label": words(int(label * f), fixture, seed + i, sentence=False),
                                  "text": words(int(caps["item.text"] * f), fixture, seed + 10 + i)}
                                 for i in range(n)]}, n
    if kind == "metrics":
        vals = NUMBERS[fixture]
        value_slot = slots.get("item.value")
        vcap = capacity(value_slot, value_slot.min_style) if value_slot else 8
        items = [{"value": vals[i % len(vals)][:vcap], "unit": None if fixture == "min" else "млн руб.",
                  "label": words(int(caps["item.label"] * f), fixture, seed + i), "status": "fact",
                  "tag": "цель" if fixture == "max" and i == 0 else None} for i in range(n)]
        return title, {"items": items, "body": None if fixture == "min" else
                       words(int(caps.get("body", 150) * f), fixture, seed + 20)}, n
    if kind == "comparison":
        points = {"min": 2, "typical": 3, "max": 4}[fixture]
        side = lambda s: {"header": words(int(caps["left_header"] * f), fixture, seed + s, sentence=False),  # noqa: E731
                          "points": [words(int(caps["left_point1"] * f), fixture, seed + s * 10 + i)
                                     for i in range(points)]}
        return title, {"left": side(1), "right": side(2),
                       "polarity": "pros_cons" if layout.id.endswith("pros_cons") else "neutral"}, None
    if kind in ("chart_series", "chart_share"):
        return title, {"insight": words(int(caps["insight"] * f), fixture, seed)}, None
    raise ValueError(kind)
