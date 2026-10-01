"""Строка decks → DeckStatus (04_CONTRACTS.md, 7.2). Общий для ответа API и тела webhook."""

from datetime import datetime, timedelta, timezone

from api.errors import deck_error, deck_warning

# Колода «в работе» дольше этого — воркер её потерял (перезапуск, таймаут задачи):
# статус отдаётся как failed / DEADLINE_EXCEEDED, чтобы клиент не опрашивал вечно.
# Строку не правим — это сделает сторож зависших колод (сессия 5).
STALE_AFTER = timedelta(minutes=15)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite (тесты) отдаёт даты без часового пояса — считаем их UTC."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def file_url(base_url: str, deck_id: str, fmt: str) -> str:
    return f"{base_url.rstrip('/')}/v1/decks/{deck_id}/files/{fmt}"


def build_status(deck, *, base_url: str = "", progress: tuple[int, int] | None = None,
                 now: datetime | None = None) -> dict:
    """deck — строка db.models.Deck (или объект с теми же полями)."""
    now = now or datetime.now(timezone.utc)
    created = aware(deck.created_at) or now
    status, stage, error = deck.status, deck.stage, None
    finished = aware(deck.finished_at)
    if status in ("queued", "processing") and now - created > STALE_AFTER:
        status, stage, error = "failed", None, deck_error("DEADLINE_EXCEEDED")
    elif status == "failed":
        error = deck_error(deck.error_code)

    files = None
    if status in ("done", "done_pdf_pending") and deck.files and deck.files.get("pptx"):
        files = {
            "pptx": file_url(base_url, deck.id, "pptx"),
            "pdf": file_url(base_url, deck.id, "pdf") if deck.files.get("pdf") else None,
            "expires_at": deck.files.get("expires_at"),
        }

    request = deck.request or {}
    spec = deck.spec or {}
    return {
        "id": deck.id,
        "status": status,
        "stage": stage if status == "processing" else None,
        "progress": ({"slides_done": progress[0], "slides_total": progress[1]}
                     if progress and status == "processing" and stage == "content" else None),
        "title": (spec.get("meta") or {}).get("title") or (request.get("input") or {}).get("topic", ""),
        "slides": len(spec["slides"]) if spec.get("slides") else None,
        "created_at": created,
        "started_at": aware(deck.started_at),
        "finished_at": finished,
        "files": files,
        "warnings": [deck_warning(w) for w in (deck.warnings or [])],
        "error": error,
    }
