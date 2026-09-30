"""Реальные генерации golden-корпуса без Telegram (workflow «golden»).

    python tests/golden/run_golden.py --out <папка>

Прямой вызов пайплайна воркера: извлечение текста → generate_presentation_structure
(основной вызов + постобработка и дозапросы) → HTML → PDF. Картинки не подбираются.
Каждая колода доклада проверяется tests/golden/check_case.py.

Нужны переменные окружения, как у воркера: OPENAI_API_KEY, OPENAI_BASE_URL,
OPENAI_MODEL, OPENAI_REASONING_EFFORT (см. .env.example).

В папке --out: <прогон>.json (spec колоды), <прогон>.pdf, results.json,
summary.md (таблица для комментария в PR). Код возврата: 0 — все генерации
прошли (нарушения check_case на код не влияют), 1 — хотя бы одна упала,
2 — API недоступен.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "golden"))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "golden-dummy")

FIXTURES = ROOT / "tests" / "fixtures"
MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


@dataclass
class Case:
    run_id: str
    case: str | None          # id кейса ТЗ 7.4 для check_case; None — только генерация
    title: str
    request: dict
    file: str | None = None
    repeat: int = 1


CASES = [
    Case("G-01", "G-01", "G-01 отчёт о продажах, docx, только мой материал",
         dict(topic="Итоги продаж за квартал", presentation_type="doklad", audience="general",
              language="ru", content_volume="medium", slide_count_hint=9, source_mode="strict"),
         file="test_prodazhi_Q3.docx", repeat=2),
    Case("G-02", "G-02", "G-02 ТЗ хакатона, pdf, только мой материал",
         dict(topic="Техническое задание для хакатона", presentation_type="doklad", audience="general",
              language="ru", content_volume="medium", slide_count_hint=9, source_mode="strict"),
         file="test_hakaton_Q3.pdf", repeat=2),
    Case("G-03", "G-03", "G-03 отчёт о продажах по теме, без файла",
         dict(topic="Итоги продаж за квартал", presentation_type="doklad", audience="general",
              language="ru", content_volume="medium", slide_count_hint=9)),
    Case("pitch", None, "Питч-дек по теме",
         dict(topic="Сервис доставки корма для собак по подписке", presentation_type="pitch_deck",
              audience="investors", language="ru")),
]


def _baseline() -> dict:
    return json.loads((ROOT / "tests/golden/baseline.json").read_text(encoding="utf-8"))["violations"]


async def _preflight() -> str | None:
    """Доступен ли API с этой машины. None — да, иначе текст ошибки."""
    import httpx
    from config import settings
    url = settings.openai_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {settings.openai_api_key}"})
    except Exception as e:  # сеть, DNS, TLS
        return f"{urlparse(url).netloc} недоступен: {type(e).__name__}: {e}"
    if resp.status_code in (401, 403):
        return f"{urlparse(url).netloc} ответил {resp.status_code}: ключ не принят или доступ с этого IP закрыт — {resp.text[:200]}"
    # 404 и прочее: /models у прокси может не быть — это не повод не пробовать генерацию
    print(f"preflight: GET {url} → {resp.status_code}")
    return None


async def _run_one(case: Case, n: int, out: Path) -> dict:
    from generation.content_extractor import extract_from_document
    from generation.llm import generate_presentation_structure
    from generation.llm_models import track_usage
    from generation.pdf_renderer import html_to_pdf
    from generation.template_engine import render_presentation
    from schemas.presentation import UserRequest
    import check_case

    run_name = f"{case.run_id}-{n}" if case.repeat > 1 else case.run_id
    result = {"run": run_name, "case": case.case, "title": case.title, "status": "ok"}
    data = dict(case.request)
    if case.file:
        path = FIXTURES / case.file
        data.update(source_type="document", source_name=case.file,
                    raw_text=await extract_from_document(path.read_bytes(), MIME[path.suffix]))
    request = UserRequest.model_validate(data)

    started = time.perf_counter()
    try:
        with track_usage() as usage:
            presentation = await generate_presentation_structure(request)
    except Exception as e:
        result.update(status="error", error=f"{type(e).__name__}: {e}"[:500],
                      seconds=round(time.perf_counter() - started, 1))
        traceback.print_exc()
        return result
    result["seconds"] = round(time.perf_counter() - started, 1)
    result["usage"] = usage.as_dict()
    result["slides"] = presentation.slide_count
    result["source_genre"] = presentation.meta.source_genre

    spec = presentation.model_dump(mode="json")
    (out / f"{run_name}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        html = render_presentation(presentation, image_urls={}, watermark=False, color_scheme="light")
        has_mermaid = any(s.layout.value == "diagram" for s in presentation.slides)
        pdf = await html_to_pdf(html, has_mermaid=has_mermaid, expected_pages=presentation.slide_count)
        (out / f"{run_name}.pdf").write_bytes(pdf)
    except Exception as e:  # PDF — артефакт для глаз, его ошибка не отменяет проверку колоды
        result["pdf_error"] = f"{type(e).__name__}: {e}"[:300]

    if case.case:
        report = check_case.check(spec, case.case)
        result.update(violations=report.violations, warnings=report.warnings, passed=len(report.passed))
    return result


def _fmt_tokens(u: dict | None) -> str:
    if not u:
        return "—"
    return f"{u['input_tokens']:,} / {u['output_tokens']:,} ({u['reasoning_tokens']:,})".replace(",", " ")


def _fmt_cost(u: dict | None) -> str:
    if not u or u.get("cost_rub") is None:
        return "н/д"
    return f"{u['cost_rub']:.2f}".replace(".", ",")


def summary_md(results: list[dict], meta: dict, preflight_error: str | None = None) -> str:
    baseline = _baseline()
    lines = ["<!-- golden-report -->", "## Golden: реальные генерации", "",
             f"Модель **{meta['model']}**, reasoning_effort **{meta['effort']}**, API `{meta['api_host']}`, "
             f"промпты `{meta['prompts_version']}`, коммит `{meta['commit'][:7]}`.", ""]
    if preflight_error:
        lines += [f"**API недоступен — генерации не запускались.** {preflight_error}", ""]
        return "\n".join(lines)

    lines += ["| Прогон | Статус | Слайдов | Нарушений: до → сейчас | Время, с | Токены: вход / выход (рассужд.) | Стоимость, ₽ |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        if r["status"] != "ok":
            lines.append(f"| {r['run']} | ❌ ошибка | — | — | {r.get('seconds', '—')} | — | — |")
            continue
        if r["case"]:
            before = baseline.get(r["case"], "—")
            viol = f"{before} → **{len(r['violations'])}**"
        else:
            viol = "не проверяется"
        status = "✅" + (" (PDF не собран)" if r.get("pdf_error") else "")
        lines.append(f"| {r['run']} | {status} | {r['slides']} | {viol} | {r['seconds']} | "
                     f"{_fmt_tokens(r.get('usage'))} | {_fmt_cost(r.get('usage'))} |")

    ok = [r for r in results if r["status"] == "ok"]
    costs = [r["usage"]["cost_rub"] for r in ok if r.get("usage", {}).get("cost_rub") is not None]
    total_cost = f"{sum(costs):.2f}".replace(".", ",") if costs else "н/д"
    lines += ["", f"Итого: {len(ok)} из {len(results)} генераций прошли, время {sum(r.get('seconds', 0) for r in results):.0f} с, "
                  f"стоимость {total_cost} ₽. «До» — `{json.loads((ROOT / 'tests/golden/baseline.json').read_text())['report']}` "
                  "(по одному прогону старого движка на кейс).", ""]

    for r in results:
        if r["status"] != "ok":
            lines += [f"**{r['run']} — ошибка:** `{r['error']}`", ""]
            continue
        if not r["case"]:
            continue
        lines += [f"<details><summary><b>{r['run']}</b> — нарушений {len(r['violations'])}, "
                  f"предупреждений {len(r['warnings'])}, жанр: {r.get('source_genre') or '—'}</summary>", ""]
        lines += [f"- {v}" for v in r["violations"]] or ["- нарушений нет"]
        if r["warnings"]:
            lines += ["", "Предупреждения:"] + [f"- {w}" for w in r["warnings"]]
        lines += ["", "</details>", ""]
    lines += ["JSON колод и PDF — в артефакте `golden-decks` этого запуска."]
    return "\n".join(lines)


async def main(out: Path, only: set[str] | None) -> int:
    from config import settings
    from generation.pdf_renderer import shutdown_renderer
    from generation.prompts import PROMPTS_VERSION

    out.mkdir(parents=True, exist_ok=True)
    meta = {"model": settings.openai_model, "effort": settings.openai_reasoning_effort,
            "api_host": urlparse(settings.openai_base_url).netloc, "prompts_version": PROMPTS_VERSION,
            "commit": os.environ.get("GITHUB_SHA", "local")}

    preflight_error = await _preflight()
    if preflight_error:
        (out / "summary.md").write_text(summary_md([], meta, preflight_error), encoding="utf-8")
        print(preflight_error, file=sys.stderr)
        return 2

    results = []
    try:
        for case in CASES:
            if only and case.run_id not in only:
                continue
            for n in range(1, case.repeat + 1):
                print(f"=== {case.title}, прогон {n}", flush=True)
                results.append(await _run_one(case, n, out))
                r = results[-1]
                print(f"    {r['status']}, {r.get('seconds')} с, нарушений {len(r.get('violations', []))}", flush=True)
    finally:
        await shutdown_renderer()

    (out / "results.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (out / "summary.md").write_text(summary_md(results, meta), encoding="utf-8")
    return 1 if any(r["status"] != "ok" for r in results) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--only", help="через запятую: G-01,G-02,G-03,pitch")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.out, set(args.only.split(",")) if args.only else None)))
