"""Клиенты и ключи API из командной строки (docs/API.md, «Как получить ключ»).

Запуск из папки src/ с DATABASE_URL в окружении (на Railway — в сервисе api:
`railway ssh --service api`, затем `cd src && python -m api.keys …`):

    python -m api.keys create --name "Второй бот" --daily-limit 200 [--no-watermark]
    python -m api.keys list
    python -m api.keys set ac_… --daily-limit 500 --rate-limit 240 --decks-per-min 20 --watermark/--no-watermark
    python -m api.keys rotate ac_…        # новый ключ, старый перестаёт работать сразу
    python -m api.keys revoke ac_…        # отключить клиента
    python -m api.keys usage [--days 30]  # колоды и себестоимость по клиентам

Ключ и секрет webhook печатаются один раз: в БД хранится только хэш ключа.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from api import store
from db.session import close_db, init_db


def _print_client(client) -> None:
    print(f"  id:               {client.id}")
    print(f"  имя:              {client.name}")
    print(f"  ключ начинается:  {client.key_prefix}…")
    print(f"  колод в сутки:    {client.daily_limit}")
    print(f"  запросов в минуту: {client.rate_limit_per_min}")
    print(f"  колод в минуту:   {client.decks_per_min}")
    print(f"  водяной знак:     {'да' if client.watermark else 'нет'}")
    print(f"  активен:          {'да' if client.active else 'нет'}")


async def _run(args) -> int:
    await init_db()
    try:
        if args.command == "create":
            client, key = await store.create_client(
                args.name, daily_limit=args.daily_limit, rate_limit_per_min=args.rate_limit,
                decks_per_min=args.decks_per_min, watermark=args.watermark)
            print("Клиент создан:")
            _print_client(client)
            print()
            print("Сохраните сейчас — больше они не будут показаны:")
            print(f"  API-ключ:        {key}")
            print(f"  секрет webhook:  {client.webhook_secret}")
            return 0
        if args.command == "list":
            clients = await store.list_clients()
            if not clients:
                print("Клиентов нет. Создать: python -m api.keys create --name \"…\"")
            for client in clients:
                _print_client(client)
                print()
            return 0
        if args.command == "set":
            client = await store.update_client(args.client_id, daily_limit=args.daily_limit,
                                               rate_limit_per_min=args.rate_limit, decks_per_min=args.decks_per_min,
                                               watermark=args.watermark, name=args.name)
            if client is None:
                print(f"Клиент {args.client_id} не найден", file=sys.stderr)
                return 1
            print("Клиент обновлён:")
            _print_client(client)
            return 0
        if args.command == "rotate":
            key = await store.rotate_key(args.client_id)
            if key is None:
                print(f"Клиент {args.client_id} не найден", file=sys.stderr)
                return 1
            print(f"Новый API-ключ (старый уже не работает): {key}")
            return 0
        if args.command == "revoke":
            client = await store.update_client(args.client_id, active=False)
            if client is None:
                print(f"Клиент {args.client_id} не найден", file=sys.stderr)
                return 1
            print(f"Клиент {client.id} отключён: запросы с его ключом получают 401.")
            return 0
        if args.command == "usage":
            since = datetime.now(timezone.utc) - timedelta(days=args.days)
            names = {c.id: c.name for c in await store.list_clients()}
            rows = await store.usage_report(since)
            print(f"За {args.days} дн. (с {since:%Y-%m-%d %H:%M} UTC):")
            if not rows:
                print("  колод нет")
            for row in rows:
                ok = row["decks"] - row["failed"]
                avg = row["cost_rub"] / row["decks"] if row["decks"] else 0
                print(f"  {names.get(row['client_id'], row['client_id'])}: колод {row['decks']} "
                      f"(готово {ok}, ошибок {row['failed']}), себестоимость {row['cost_rub']:.2f} ₽, "
                      f"в среднем {avg:.2f} ₽")
            return 0
    except store.StoreUnavailable:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 2
    finally:
        await close_db()
    return 1


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="python -m api.keys", description="Клиенты и ключи REST API")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="новый клиент и ключ")
    create.add_argument("--name", required=True)
    create.add_argument("--daily-limit", type=int, default=100)
    create.add_argument("--rate-limit", type=int, default=120, help="запросов в минуту")
    create.add_argument("--decks-per-min", type=int, default=10)
    create.add_argument("--watermark", action=argparse.BooleanOptionalAction, default=True,
                        help="водяной знак в PDF (по умолчанию да)")

    sub.add_parser("list", help="все клиенты")

    upd = sub.add_parser("set", help="изменить лимиты клиента")
    upd.add_argument("client_id")
    upd.add_argument("--name")
    upd.add_argument("--daily-limit", type=int)
    upd.add_argument("--rate-limit", type=int)
    upd.add_argument("--decks-per-min", type=int)
    upd.add_argument("--watermark", action=argparse.BooleanOptionalAction, default=None)

    rotate = sub.add_parser("rotate", help="выпустить новый ключ")
    rotate.add_argument("client_id")
    revoke = sub.add_parser("revoke", help="отключить клиента")
    revoke.add_argument("client_id")

    usage = sub.add_parser("usage", help="колоды и себестоимость по клиентам")
    usage.add_argument("--days", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
