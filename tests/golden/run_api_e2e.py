"""Golden через REST API end-to-end: настоящие процессы api (uvicorn) и воркера (arq),
Postgres, Redis, реальная модель (workflow «golden», шаг «API end-to-end»).

    python tests/golden/run_api_e2e.py --out <папка> [--port 8765]

Нужны DATABASE_URL, REDIS_URL и переменные LLM, как у воркера. Скрипт:
1. создаёт клиента API и ключ (api.store — как python -m api.keys create);
2. запускает uvicorn api.app:app и arq worker.WorkerSettings из src/;
3. POST /v1/decks: G-01 (docx, multipart) с Idempotency-Key и webhook на локальный
   приёмник; повтор с тем же ключом — та же колода;
4. опрашивает GET /v1/decks/{id} каждые 3 с (как рекомендует docs/API.md);
5. скачивает PPTX и PDF, проверяет файлы (run_golden.check_files) и DeckSpec из БД
   (check_case G-01), подпись webhook, /v1/usage.

Итог — раздел «API end-to-end» в <out>/summary.md (дописывается к отчёту run_golden)
и <out>/api_e2e/. Код возврата: 0 — колода собрана и скачана, 1 — нет.
"""
import argparse
import asyncio
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "golden"))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:golden-dummy")

import httpx  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "test_prodazhi_Q3.docx"
TOPIC = "Итоги продаж за квартал"
POLL_SECONDS = 3
DEADLINE_SECONDS = 300


class _Hook(BaseHTTPRequestHandler):
    received: list[tuple[dict, bytes]] = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Hook.received.append((dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


async def _create_client() -> tuple[str, str, str]:
    from api import store
    from db.session import close_db, init_db
    await init_db()
    try:
        client, key = await store.create_client("golden-e2e", daily_limit=10)
        return client.id, key, client.webhook_secret
    finally:
        await close_db()


async def _load_spec(deck_id: str) -> dict | None:
    from core.storage import decks as deck_store
    from db.session import close_db, init_db
    await init_db()
    try:
        deck = await deck_store.load(deck_id)
        return {"spec": deck.spec, "cost_rub": float(deck.cost_rub or 0), "durations_ms": deck.durations_ms} \
            if deck else None
    finally:
        await close_db()


def _wait_health(base: str, procs: list[subprocess.Popen], timeout: float = 60) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        for p in procs:
            if p.poll() is not None:
                raise RuntimeError(f"процесс {p.args} завершился с кодом {p.returncode}")
        try:
            if httpx.get(f"{base}/v1/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("API не ответил на /v1/health за 60 с")


def run(out: Path, port: int) -> int:
    import check_case
    from run_golden import check_files
    from core.models.deck import DeckSpec

    out.mkdir(parents=True, exist_ok=True)
    files_dir = out / "api_e2e"
    files_dir.mkdir(exist_ok=True)
    base = f"http://127.0.0.1:{port}"
    hook_server = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=hook_server.serve_forever, daemon=True).start()
    hook_url = f"http://127.0.0.1:{hook_server.server_port}/hook"

    client_id, key, secret = asyncio.run(_create_client())
    env = {**os.environ, "API_PUBLIC_URL": base}
    logs = {name: open(files_dir / f"{name}.log", "w") for name in ("api", "worker")}
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1", "--port", str(port)],
                         cwd=ROOT / "src", env=env, stdout=logs["api"], stderr=subprocess.STDOUT),
        subprocess.Popen([sys.executable, "-m", "arq", "worker.WorkerSettings"],
                         cwd=ROOT / "src", env=env, stdout=logs["worker"], stderr=subprocess.STDOUT),
    ]
    report: dict = {"checks": [], "problems": []}

    def check(ok: bool, text: str) -> bool:
        report["checks"].append(("✅" if ok else "❌") + " " + text)
        if not ok:
            report["problems"].append(text)
        return ok

    try:
        _wait_health(base, procs)
        headers = {"Authorization": f"Bearer {key}"}
        http = httpx.Client(base_url=base, headers=headers, timeout=60)
        themes = http.get("/v1/themes").json()["themes"]
        check(len(themes) == 4, f"GET /v1/themes — {len(themes)} темы")

        params = {"input": {"topic": TOPIC}, "slides_count": 9, "audience": "general", "source_mode": "strict",
                  "theme_id": "azure_coral", "webhook_url": hook_url}
        files = {"file": (FIXTURE.name, FIXTURE.read_bytes(),
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        started = time.monotonic()
        r = http.post("/v1/decks", data={"params": json.dumps(params, ensure_ascii=False)}, files=files,
                      headers={"Idempotency-Key": "golden-g01"})
        check(r.status_code == 202, f"POST /v1/decks (docx, multipart) → {r.status_code}")
        deck_id = r.json()["id"]
        again = http.post("/v1/decks", data={"params": json.dumps(params, ensure_ascii=False)}, files=files,
                          headers={"Idempotency-Key": "golden-g01"})
        check(again.status_code == 200 and again.json()["id"] == deck_id,
              f"повтор с тем же Idempotency-Key → {again.status_code}, та же колода")

        stages, polls, body = [], 0, {}
        while time.monotonic() - started < DEADLINE_SECONDS:
            for p in procs:
                if p.poll() is not None:
                    raise RuntimeError(f"процесс {' '.join(map(str, p.args[1:4]))} завершился с кодом {p.returncode} "
                                       f"— см. api_e2e/*.log в артефакте")
            body = http.get(f"/v1/decks/{deck_id}").json()
            polls += 1
            label = body["stage"] or body["status"]
            if body.get("progress"):
                label += f" {body['progress']['slides_done']}/{body['progress']['slides_total']}"
            if not stages or stages[-1] != label:
                stages.append(label)
            if body["status"] in ("done", "failed"):
                break
            time.sleep(POLL_SECONDS)
        seconds = round(time.monotonic() - started, 1)
        report.update(deck_id=deck_id, seconds=seconds, polls=polls, stages=stages, status=body)
        if not check(body.get("status") == "done", f"колода готова за {seconds} с (статус {body.get('status')}, "
                                                   f"ошибка {body.get('error')})"):
            return 1
        pptx = http.get(f"/v1/decks/{deck_id}/files/pptx")
        pdf = http.get(f"/v1/decks/{deck_id}/files/pdf")
        check(pptx.status_code == 200 and pdf.status_code == 200,
              f"скачивание PPTX {pptx.status_code} ({len(pptx.content) // 1024} КБ), "
              f"PDF {pdf.status_code} ({len(pdf.content) // 1024} КБ)")
        (files_dir / f"{deck_id}.pptx").write_bytes(pptx.content)
        (files_dir / f"{deck_id}.pdf").write_bytes(pdf.content)
        check(http.get(f"/v1/decks/{deck_id}/files/pptx", headers={"Authorization": "Bearer fib_wrong_key_123456789"})
              .status_code == 401, "скачивание без верного ключа → 401")

        row = asyncio.run(_load_spec(deck_id))
        spec = DeckSpec.model_validate(row["spec"])
        (files_dir / f"{deck_id}.deckspec.json").write_text(json.dumps(row["spec"], ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
        report.update(cost_rub=row["cost_rub"], durations_ms=row["durations_ms"])
        file_problems = check_files(spec, pptx.content, pdf.content)
        check(not file_problems, "файлы: " + ("PPTX и PDF в порядке" if not file_problems else "; ".join(file_problems)))
        case = check_case.check(row["spec"], "G-01")
        report.update(violations=case.violations, passed=len(case.passed))
        report["checks"].append(f"ℹ️ check_case G-01: нарушений {len(case.violations)}, выполнено {len(case.passed)}")

        for _ in range(10):
            if _Hook.received:
                break
            time.sleep(1)
        if check(bool(_Hook.received), "webhook пришёл"):
            hook_headers, hook_body = _Hook.received[0]
            expected = "sha256=" + hmac.new(secret.encode(), hook_body, hashlib.sha256).hexdigest()
            check(hook_headers.get("X-Fibonacci-Signature") == expected, "подпись webhook верна")
            check(json.loads(hook_body)["status"] == "done", "в webhook status = done")
        usage = http.get("/v1/usage").json()
        check(usage["used_today"] == 1, f"/v1/usage: использовано {usage['used_today']} из {usage['daily_limit']}")
        return 0 if not report["problems"] else 1
    except Exception as e:
        check(False, f"сбой сценария: {type(e).__name__}: {e}")
        return 1
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in logs.values():
            f.close()
        hook_server.shutdown()
        _write_summary(out, report)


def _write_summary(out: Path, report: dict) -> None:
    lines = ["", "## API end-to-end (сервис api + воркер, G-01 docx через multipart)", ""]
    if report.get("deck_id"):
        lines.append(f"Колода `{report['deck_id']}`: {report.get('seconds')} с от POST до `done`, "
                     f"опросов статуса {report.get('polls')} (каждые {POLL_SECONDS} с), "
                     f"стоимость {report.get('cost_rub', 0):.2f} ₽.")
        lines.append("")
        lines.append("Этапы в статусе: " + " → ".join(report.get("stages", [])))
        lines.append("")
    lines += [f"- {c}" for c in report["checks"]]
    for v in report.get("violations", []):
        lines.append(f"  - нарушение G-01: {v}")
    lines.append("")
    lines.append("Итог: " + ("✅ API работает end-to-end" if not report["problems"] else
                             "❌ " + "; ".join(report["problems"])))
    summary = out / "summary.md"
    with summary.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    sys.exit(run(args.out, args.port))
