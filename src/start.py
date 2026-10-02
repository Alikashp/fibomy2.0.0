"""Точка входа образа (CMD Dockerfile): какой процесс запустить — по SERVICE_ROLE (D-062).

    SERVICE_ROLE=bot     (по умолчанию) Telegram-бот:  python src/main.py
    SERVICE_ROLE=worker  ARQ-воркер:                   arq worker.WorkerSettings
    SERVICE_ROLE=api     REST API:                     uvicorn api.app:app на $PORT

Бот, воркер и API — сервисы Railway из одного образа. Раньше команду задавали файлы
railway*.toml (Config as Code), но Railway его отключает: новые сервисы (с 28.08.2026)
не могут его включить, старые файлы работают до 01.12.2026. Переменная окружения
работает всегда, поэтому роль сервиса задаётся ей.

Процесс заменяется через exec: сигналы остановки Railway получает сам бот / воркер / uvicorn.
"""

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROLES = ("bot", "worker", "api")


def command(role: str, port: str | None = None) -> list[str]:
    """Аргументы запуска для роли; ValueError — неизвестная роль."""
    role = (role or "bot").strip().lower()
    if role == "bot":
        return [sys.executable, str(SRC / "main.py")]
    if role == "worker":
        return [sys.executable, "-m", "arq", "worker.WorkerSettings"]
    if role == "api":
        return [sys.executable, "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", str(port or "8000"),
                "--proxy-headers", "--forwarded-allow-ips", "*"]
    raise ValueError(f"SERVICE_ROLE={role!r}: ожидается одно из {', '.join(ROLES)}")


def main() -> None:
    sys.path.insert(0, str(SRC))
    from config import settings

    try:
        argv = command(settings.service_role, os.environ.get("PORT"))
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    print(f"start.py: SERVICE_ROLE={settings.service_role or 'bot'} → {' '.join(argv[1:])}", flush=True)
    os.chdir(SRC)
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
