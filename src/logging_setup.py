"""
JSON-логи для бота и воркера (ТЗ 4.7).

Раньше logging.basicConfig без формата печатал только текст сообщения — все
поля из extra={...} молча терялись. Теперь каждая строка — один JSON-объект:
message, level, logger, ts, все поля extra, а в воркере ещё и deck_id
текущей задачи (берётся из contextvar, проставлять вручную не нужно).

Длительности этапов пишет stage():

    with stage("llm"):
        presentation = await generate_presentation_structure(request)

→ {"message": "stage", "stage": "llm", "duration_ms": 18234, "status": "ok", "deck_id": "..."}
"""

import contextvars
import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

deck_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("deck_id", default=None)

# Атрибуты, которые есть у любой LogRecord, — всё остальное пришло из extra.
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        deck_id = deck_id_var.get()
        if deck_id:
            payload["deck_id"] = deck_id
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    """Один JSON-хендлер на root. Идемпотентна — можно звать повторно.

    Воркер вызывает её ещё раз в on_startup: CLI arq после импорта модуля
    настраивает свой текстовый хендлер для логгера "arq" — снимаем его, чтобы
    строки arq тоже шли в JSON и не дублировались.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    arq_logger = logging.getLogger("arq")
    arq_logger.handlers[:] = []
    arq_logger.propagate = True


@contextmanager
def stage(name: str, timings: dict[str, int] | None = None, **fields) -> Iterator[None]:
    """Логирует stage, duration_ms и status этапа; при timings — дописывает туда длительность."""
    start = time.perf_counter()
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        if timings is not None:
            timings[name] = duration_ms
        logger.info(
            "stage",
            extra={"stage": name, "duration_ms": duration_ms, "status": status, **fields},
        )
