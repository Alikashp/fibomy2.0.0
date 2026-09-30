"""Проверка JSON колоды по ожиданиям golden-кейса (ТЗ 7.4).

    python tests/golden/check_case.py <deck.json> <G-01|G-02|G-03> [--md] [--no-fail]

На выходе — список нарушений (обязательные числа с подписями, запрещённые
утверждения и числа, даты в таймлайне, слайды без заголовка, выдуманные
source) и предупреждений (эвристики, которые стоит проверить глазами).
Код возврата 1, если есть нарушения (с --no-fail — 0: так в CI падает только
ошибка самого скрипта, а нарушения идут в отчёт).

Проверки детерминированные и намеренно простые: регулярные выражения по
«единицам текста» слайда (заголовок, пункт, метрика, этап таймлайна…).
Число считается подписанным, если оно и подпись стоят в одной единице.
Это грубее проверки по source_ref из ТЗ 4.8.2 — её сделаем в новом движке.
"""
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DOKLAD_LAYOUTS = {"title", "bullets", "metrics", "two_column", "diagram", "quote",
                  "timeline", "image_full", "image_hero", "closing"}
ESTIMATE_FOOTNOTE = "Оценочные данные — проверьте перед показом"
# Месяцы IV квартала: в источнике G-01 у планов только «IV квартал», а июль–сентябрь — факты III квартала
Q4_MONTHS = r"(октябр|ноябр|декабр)"

CASES = {
    "G-01": {"title": "Отчёт о продажах сети кофеен (docx)", "material": True},
    "G-02": {"title": "ТЗ хакатона VK (pdf)", "material": True},
    "G-03": {"title": "Отчёт о продажах по теме, без файла", "material": False},
}


# ── Текст колоды ─────────────────────────────────────────────────────────────

def norm(text: str | None) -> str:
    if not text:
        return ""
    text = text.lower().replace("ё", "е").replace(" ", " ")
    return re.sub(r"[−–]", "-", text)


@dataclass
class Unit:
    slide: int
    kind: str
    text: str  # нормализованный


def units_of(slide: dict) -> list[Unit]:
    i = slide.get("index")
    out: list[Unit] = []

    def add(kind, *parts):
        text = " ".join(str(p) for p in parts if p)
        if text.strip():
            out.append(Unit(i, kind, norm(text)))

    add("title", slide.get("title"))
    add("subtitle", slide.get("subtitle"))
    add("body", slide.get("body_text"))
    for b in slide.get("bullets") or []:
        add("bullet", b.get("subtitle"), b.get("text"))
    for m in slide.get("metrics") or []:
        add("metric", m.get("value"), m.get("label"), m.get("trend"))
    for t in slide.get("timeline_items") or []:
        add("timeline", t.get("date"), t.get("title"), t.get("description"))
    tc = slide.get("two_column") or {}
    for side in ("left", "right"):
        add("column", tc.get(f"{side}_title"), tc.get(f"{side}_text"))
        for b in tc.get(f"{side}_bullets") or []:
            add("column", b.get("text"))
    return out


def content_slides(deck: dict) -> list[dict]:
    return [s for s in deck.get("slides", []) if s.get("layout") not in ("title", "closing")]


def all_units(deck: dict) -> list[Unit]:
    return [u for s in content_slides(deck) for u in units_of(s)]


def slide_text(slide: dict) -> str:
    return " ".join(u.text for u in units_of(slide))


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def check(self, ok: bool, what: str, detail: str = "") -> None:
        if ok:
            self.passed.append(what)
        else:
            self.violations.append(what + (f" — {detail}" if detail else ""))


def num(pattern: str) -> str:
    """Число, не являющееся частью другого числа: 8% не совпадает с 18%."""
    return rf"(?<![\d,.]){pattern}(?![\d])"


def find(units: list[Unit], *patterns: str) -> list[Unit]:
    return [u for u in units if all(re.search(p, u.text) for p in patterns)]


def where(units: list[Unit]) -> str:
    return ", ".join(sorted({f"слайд {u.slide}" for u in units}))


def quote(u: Unit, limit: int = 90) -> str:
    return f"слайд {u.slide}: «{u.text[:limit]}»"


# ── Общие проверки ───────────────────────────────────────────────────────────

def check_common(deck: dict, case: str, r: Report) -> None:
    slides = content_slides(deck)

    untitled = [s["index"] for s in slides if not (s.get("title") or "").strip()]
    r.check(not untitled, "у каждого содержательного слайда есть заголовок",
            f"title пустой: слайд {', '.join(map(str, untitled))}")

    outside = [f"{s['index']} ({s['layout']})" for s in slides if s.get("layout") not in DOKLAD_LAYOUTS]
    r.check(not outside, "только layout из каталога доклада", f"вне каталога: слайд {', '.join(outside)}")

    titles = [norm(s.get("title")) for s in slides if s.get("title")]
    dups = sorted({t for t in titles if titles.count(t) > 1})
    r.check(not dups, "заголовки не повторяются", "; ".join(dups))

    bad_sources = []
    for s in slides:
        for m in s.get("metrics") or []:
            src = (m.get("source") or "").strip()
            if not src:
                continue
            ok = src.startswith("по данным:") if CASES[case]["material"] else src == ESTIMATE_FOOTNOTE
            if not ok:
                bad_sources.append(f"слайд {s['index']}: «{src}»")
    r.check(not bad_sources, "metrics[].source не выдуман моделью (его ставит код)", "; ".join(bad_sources))

    for s in slides:
        items = s.get("timeline_items") or []
        dates = [norm(t.get("date")) for t in items if t.get("date")]
        if s.get("layout") == "timeline" and len(dates) > 1 and len(set(dates)) == 1:
            r.warnings.append(f"слайд {s['index']}: у всех этапов таймлайна одна дата «{dates[0]}» — это не timeline")


# ── G-01 ─────────────────────────────────────────────────────────────────────

CHANNELS = {"продажи в зале": r"зал", "доставка": r"доставк", "предзаказ": r"предзаказ",
            "корпоративные заказы": r"корпоратив"}
CITIES = {"Санкт-Петербург": r"петербург|спб", "Петрозаводск": r"петрозаводск",
          "Мурманск": r"мурманск", "Архангельск": r"архангельск"}
CHANNEL_ANY = "|".join(CHANNELS.values())
# Приоритет «две новые точки в Санкт-Петербурге», а не «две точки» Архангельска из таблицы городов
TWO_NEW_POINTS = r"(откры\w*|запуст\w*) дв\w* (нов\w+ )?(точ|кофе)|дв\w* нов\w+ (точ|кофе)|дв\w* (точ|кофе)\w* в (санкт|спб|петербург)"


def check_g01(deck: dict, r: Report) -> None:
    units = all_units(deck)
    text = " ".join(u.text for u in units)

    # Обязано быть: число → подпись
    r.check(bool(find(units, r"47[,.]3", r"выручк|итог")), "47,3 млн руб. — выручка за квартал")
    r.check(bool(find(units, num(r"\+?41\s?%"), r"доставк")), "+41% — рост доставки")
    r.check(bool(find(units, num(r"-6\s?%"), r"корпоратив")) or bool(
        find(units, num(r"6\s?%"), r"корпоратив", r"паден|сниз|минус|сократ")),
        "−6% — корпоративные заказы")
    eight = find(units, num(r"\+?8\s?%"))
    r.check(any(re.search(r"зал", u.text) for u in eight), "+8% — рост продаж в зале")
    wrong8 = [u for u in eight if not re.search(r"зал", u.text)]
    r.check(not wrong8, "+8% только с подписью «продажи в зале»", "; ".join(quote(u) for u in wrong8))

    shares = {"65%": num(r"65\s?%"), "18%": num(r"18\s?%"), "10%": num(r"10\s?%"), "7%": num(r"7\s?%")}
    shown = {k for k, p in shares.items() if find(units, p, CHANNEL_ANY + "|дол")}
    r.check(not shown or shown == set(shares), "доли каналов — все четыре, если показаны",
            f"показаны {sorted(shown)}, нет {sorted(set(shares) - shown)}")

    murmansk = [s for s in content_slides(deck) if re.search(num(r"87\s?%?"), slide_text(s)) and "мурманск" in slide_text(s)]
    r.check(bool(murmansk), "87% — план Мурманска")
    r.check(any("ремонт" in slide_text(s) for s in murmansk), "у 87% Мурманска указана причина (ремонт ТЦ)")
    r.check(bool(find(units, num(r"112\s?%?"), r"петербург|спб")), "112% — план Санкт-Петербурга")

    # Факты без чисел
    r.check(bool(re.search(r"гост|трафик", text)), "рост обеспечен числом гостей, а не ценой")
    r.check(bool(re.search(r"предзаказ", text)) and bool(re.search(r"обогна|опереди", text)),
            "предзаказ запущен в июле и обогнал корпоративные заказы")
    r.check(bool(re.search(r"лояльност", text)) and bool(re.search(TWO_NEW_POINTS, text))
            and bool(re.search(r"корпоратив\w* (клиент|предложен)|предложени\w* для корпоратив", text)),
            "три приоритета IV квартала")

    cities = {name for name, p in CITIES.items() if re.search(p, text)}
    r.check(len(cities) < 2 or len(cities) == 4, "города — все четыре, если показаны",
            f"нет {sorted(set(CITIES) - cities)}")

    # Не должно быть
    total_growth = [u for u in find(units, r"выручк", r"\d\s?%", r"прошл\w* год|2025")
                    if not re.search(CHANNEL_ANY, u.text)]
    r.check(not total_growth, "нет процента роста общей выручки к прошлому году",
            "; ".join(quote(u) for u in total_growth))

    months = find(units, Q4_MONTHS)
    r.check(not months, "нет месяцев и дат у планов IV квартала", "; ".join(quote(u, 60) for u in months))

    extra = []
    for s in content_slides(deck):
        head = norm(f"{s.get('title')} {s.get('subtitle')}")
        n = len(s.get("timeline_items") or []) or len(s.get("bullets") or [])
        if re.search(r"iv квартал|приоритет|планы? на", head) and n > 3:  # не «превысил план»
            extra.append(f"слайд {s['index']}: {n} пунктов")
    extra += [quote(u, 60) for u in find(units, r"анализ результатов|оценка эффективности")]
    r.check(not extra, "не больше трёх приоритетов IV квартала", "; ".join(extra))

    cut = []
    for s in content_slides(deck):
        st = slide_text(s)
        channels = {n for n, p in CHANNELS.items() if re.search(p, st)}
        if len(channels) >= 3 and "корпоративные заказы" not in channels and re.search(r"\d\s?%", st):
            cut.append(f"слайд {s['index']}: каналы без корпоративных заказов")
    r.check(not cut, "ряд каналов не обрезан, отрицательный элемент на месте", "; ".join(cut))

    causes = find(units, r"поможет вернуть|увеличит охват|результат увеличения потока")
    r.check(not causes, "нет выдуманных причин и следствий из списка ТЗ", "; ".join(quote(u) for u in causes))
    for u in find(units, r"- это (поможет|увеличит|позволит|обеспечит|привед)"):
        r.warnings.append(f"связка «— это …» — проверить по источнику: {quote(u)}")

    repeats = []
    for label, p in (("47,3 млн", r"47[,.]3"), ("+41%", num(r"41\s?%")), ("112%", num(r"112")),
                     ("87%", num(r"87\s?%")), ("программа лояльности", r"лояльност"),
                     ("две новые точки", TWO_NEW_POINTS)):
        slides = {s["index"] for s in content_slides(deck) if re.search(p, slide_text(s))}
        if len(slides) > 1:
            repeats.append(f"{label} — слайды {', '.join(map(str, sorted(slides)))}")
    r.check(not repeats, "один факт — на одном слайде", "; ".join(repeats))

    topic_titles = {"общая динамика", "каналы продаж", "выводы и планы", "итоги работы"}
    bad = [s for s in content_slides(deck) if norm(s.get("title")).strip(" .") in topic_titles]
    r.check(not bad, "нет заголовков-тем из списка ТЗ",
            "; ".join(f"слайд {s['index']}: «{s['title']}»" for s in bad))
    for s in content_slides(deck):
        t = norm(s.get("title"))
        if (s.get("layout") != "quote" and s not in bad and t and len(t.split()) <= 3
                and not re.search(r"\d", t)):
            r.warnings.append(f"слайд {s['index']}: короткий заголовок без числа «{s['title']}» — вывод или тема?")


# ── G-02 ─────────────────────────────────────────────────────────────────────

def check_g02(deck: dict, r: Report) -> None:
    units = all_units(deck)
    text = " ".join(u.text for u in units)

    # Обязано быть
    r.check(bool(re.search(r"парсинг|разбор\w* шаблон|декомпози", text)) and bool(re.search(r"структур", text))
            # «визуализация» — в колодах и «генерировать структуру … а также слайды», «формирует структуру и слайды»
            and bool(re.search(r"визуализ|(генерир|генерац|созда|формир)\w*[^.]{0,50}слайд", text)),
            "три бизнес-задачи сервиса")
    five = find(units, num(r"5\s?мин"))
    r.check(bool(five), "«5 минут» есть в колоде")
    not_limit = [u for u in five if not re.search(
        r"не более|не дольше|не больше|до 5|максимум|огранич|лимит|предел|превыша|не должн|не может|уложит", u.text)]
    r.check(bool(five) and not not_limit, "«5 минут» — с подписью ограничения", "; ".join(quote(u) for u in not_limit))
    # «три визуально различимых варианта» — между числом и словом до двух слов
    r.check(bool(find(units, r"(3|три|трех|трёх)\s+(\w+\s+){0,2}вариант")), "требование трёх вариантов вёрстки")
    r.check(bool(re.search(r"не видел|незнаком|неизвестн|произвольн", text)),
            "решение не заточено под три шаблона: на финале — незнакомый шаблон")
    r.check(bool(re.search(r"критери|направлени\w* оценки|оценк\w* охватыва|оценива\w* по", text)),
            "есть критерии оценки")

    # Если показано — только с подписью
    conditional = [
        ("10–15 слайдов", num(r"10\s?[-–]\s?15"), r"слайд"),
        ("35B", r"35\s?b", r"открыт|apache|mit|модел|вес"),
        ("20B", r"20\s?b", r"изображ|text-to-image|звезд|10 команд"),
        ("9 вариантов", num(r"9\s+вариант"), r"промежуточ|3 шаблон|трех шаблон|трёх шаблон"),
        ("7 минут", num(r"7\s?мин"), r"питч|видео|демо|выступлен"),
        ("4.5:1", r"4[.,]5\s?:\s?1", r"контраст"),
    ]
    for label, p, context in conditional:
        bad = [u for u in find(units, p) if not re.search(context, u.text)]
        r.check(not bad, f"{label} — только с подписью из источника", "; ".join(quote(u) for u in bad))

    # Не должно быть
    pct = find(units, r"\d\s?%")
    r.check(not pct, "нет процентов (в источнике их нет)", "; ".join(quote(u, 70) for u in pct))
    as_result = [u for u in find(units, r"(5\s?мин|(3|три)\s+вариант)")
                 if re.search(r"занимает|генерирует|сгенерир|достиг|обеспечива|успешн", u.text)]
    for s in content_slides(deck):
        if re.search(r"результат|эффективност|показател", norm(s.get("title"))) and s.get("metrics"):
            as_result += [u for u in units_of(s) if u.kind == "metric"]
    as_result = list({(u.slide, u.text): u for u in as_result}.values())
    r.check(not as_result, "«5 минут» и «3 варианта» не выданы за результат", "; ".join(quote(u, 70) for u in as_result))

    # Запрещены заголовки-рамки «Что сделано», «Результаты (работы)» и т.п.; слово
    # «результат» внутри заголовка-вывода («…с проверкой результата») — только предупреждение
    frame = r"^(что сделано|результаты?( работы| проекта)?|измеримые показатели\w*( успеха)?|эффективность сервиса)\W*$"
    bad_titles = [f"слайд {s['index']}: «{s['title']}»" for s in content_slides(deck)
                  if re.search(frame, norm(s.get("title")).strip())]
    for s in content_slides(deck):
        t = norm(s.get("title"))
        if "результат" in t and not re.search(frame, t.strip()):
            r.warnings.append(f"слайд {s['index']}: «{s['title']}» — слово «результат» в заголовке, "
                              f"проверить, не выдано ли требование за результат")
    r.check(not bad_titles, "нет слайдов «Что сделано», «Результаты» и т.п.", "; ".join(bad_titles))

    # краткие причастия: «разработаны», «создана»; «создания», «проведение» не считаются
    past = find(units, r"\b(проведен|разработан|создан|реализован|выполнен)[аоы]?\b")
    r.check(not past, "нет прошедшего времени о работе участников", "; ".join(quote(u, 70) for u in past))

    claims = find(units, r"высок\w* (точност|качеств|степен)|рекордно|соответствует корпоративным стандартам")
    r.check(not claims, "нет оценок качества несуществующего решения", "; ".join(quote(u, 70) for u in claims))

    timeline = [s["index"] for s in content_slides(deck) if s.get("layout") == "timeline"]
    dates = [u for u in units if re.search(r"q[1-4]\s?20\d\d|\b20(2[5-9]|3\d)\b", u.text) and "2026" not in u.text
             or re.search(r"q[1-4]\s?20\d\d", u.text)]
    r.check(not timeline and not dates, "нет timeline и дат",
            "; ".join([f"timeline: слайд {', '.join(map(str, timeline))}"] if timeline else []
                      + [quote(u, 50) for u in dates]))

    steps = find(units, r"сбор\w* (отзыв|обратн)|обратн\w* связ|расширени\w* функционал")
    steps += [u for s in content_slides(deck) if re.search(r"следующие шаги|план действий", norm(s.get("title")))
              for u in units_of(s)[:1]]
    r.check(not steps, "нет придуманных «следующих шагов»", "; ".join(quote(u, 60) for u in steps))


# ── G-03 ─────────────────────────────────────────────────────────────────────

def check_g03(deck: dict, r: Report) -> None:
    units = all_units(deck)
    slides = content_slides(deck)

    missing = [s["index"] for s in slides if re.search(r"\d", slide_text(s)) and s.get("footnote") != ESTIMATE_FOOTNOTE]
    r.check(not missing, "у каждого слайда с числом — сноска «Оценочные данные — проверьте перед показом»",
            f"нет сноски: слайд {', '.join(map(str, missing))}")

    refs = find(units, r"отчет|отчет\w*|по данным|исследовани|данн\w+ компании|росстат|отдел\w* маркетинг")
    r.check(not refs, "нет ссылок на конкретные отчёты и организации", "; ".join(quote(u, 70) for u in refs))

    indicators = {"новые клиенты": r"нов\w* клиент", "средний чек": r"средн\w* чек",
                  "объём продаж": r"объ[её]м\w* продаж|рост\w* продаж"}
    contradictions = []
    for name, p in indicators.items():
        values = {}
        for u in find(units, p):
            for v in re.findall(r"(?<![\d,.])[+-]?\d+(?:[.,]\d+)?\s?%", u.text):
                values.setdefault(v.replace(" ", "").lstrip("+"), set()).add(u.slide)
        if len(values) > 1:
            contradictions.append(f"{name}: " + ", ".join(f"{v} (слайд {', '.join(map(str, sorted(s)))})"
                                                        for v, s in sorted(values.items())))
    r.check(not contradictions, "один показатель — одно число на всех слайдах", "; ".join(contradictions))

    specifics = []
    for s in slides:
        if s.get("footnote") == ESTIMATE_FOOTNOTE:
            continue
        specifics += [quote(u, 70) for u in units_of(s)
                      if re.search(r"электроник|\d+\s+человек|\d+([.,]\d+)?\s*(млн|миллион|млрд)", u.text)]
    r.check(not specifics, "нет конкретики, выданной за факт, без пометки оценки", "; ".join(specifics))


CHECKS = {"G-01": check_g01, "G-02": check_g02, "G-03": check_g03}


def check(deck: dict, case: str) -> Report:
    r = Report()
    check_common(deck, case, r)
    CHECKS[case](deck, r)
    return r


def to_markdown(path: str, case: str, r: Report) -> str:
    lines = [f"### {case} — `{path}`", "",
             f"{CASES[case]['title']}. Нарушений: **{len(r.violations)}**, "
             f"предупреждений: {len(r.warnings)}, пройдено проверок: {len(r.passed)}.", ""]
    if r.violations:
        lines += ["**Нарушения**", ""] + [f"- {v}" for v in r.violations] + [""]
    if r.warnings:
        lines += ["**Предупреждения** (эвристики — проверить глазами)", ""] + [f"- {w}" for w in r.warnings] + [""]
    if r.passed:
        lines += ["<details><summary>Пройдено</summary>", ""] + [f"- {p}" for p in r.passed] + ["", "</details>", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[2] not in CASES:
        print(__doc__)
        return 2
    path, case = argv[1], argv[2]
    report = check(json.loads(Path(path).read_text(encoding="utf-8")), case)
    if "--md" in argv:
        print(to_markdown(path, case, report))
    else:
        for v in report.violations:
            print(f"НАРУШЕНИЕ: {v}")
        for w in report.warnings:
            print(f"предупреждение: {w}")
        print(f"итого: нарушений {len(report.violations)}, предупреждений {len(report.warnings)}, "
              f"пройдено {len(report.passed)}")
    return 1 if report.violations and "--no-fail" not in argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
