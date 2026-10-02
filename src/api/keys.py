"""Клиенты и ключи API из командной строки (docs/API.md, «Как получить ключ»).

Запуск из папки src/ с DATABASE_URL в окружении (на Railway — в сервисе api:
`railway ssh --service api`, затем `cd src && python -m api.keys …`):

    python -m api.keys create --name "Второй бот" --daily-limit 200 [--no-watermark]
    python -m api.keys list
    python -m api.keys set "Второй бот" --daily-limit 500 --rate-limit 240 --decks-per-min 20 --watermark/--no-watermark
    python -m api.keys rotate ac_…        # новый ключ, старый перестаёт работать сразу
    python -m api.keys revoke ac_…        # отключить клиента
    python -m api.keys usage [--days 30]  # колоды и себестоимость по клиентам

Клиента можно указать по id (ac_…) или по имени. Ключ и секрет webhook печатаются
один раз: в БД хранится только хэш ключа. Логика — api.admin, общая с командой бота
/apikey (bot/admin.py): владельцу без доступа к Railway удобнее бот.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from api import admin, store
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
            client, key = await admin.create(args.name, daily_limit=args.daily_limit,
                                             rate_limit_per_min=args.rate_limit, decks_per_min=args.decks_per_min,
                                             watermark=args.watermark)
            print("Клиент создан:")
            _print_client(client)
            print()
            print("Сохраните сейчас — больше они не будут показаны:")
            print(f"  API-ключ:        {key}")
            print(f"  секрет webhook:  {client.webhook_secret}")
            return 0
        if args.command == "list":
            rows = await admin.overview()
            if not rows:
                print("Клиентов нет. Создать: python -m api.keys create --name \"…\"")
            for row in rows:
                _print_client(row.client)
                print(f"  сегодня:          {row.used_today} колод, {row.cost_today:.2f} ₽")
                print(f"  за 30 дней:       {row.decks_30d} колод (ошибок {row.failed_30d}), {row.cost_30d:.2f} ₽")
                print()
            return 0
        if args.command == "set":
            client = await admin.update(args.client_id, daily_limit=args.daily_limit,
                                        rate_limit_per_min=args.rate_limit, decks_per_min=args.decks_per_min,
                                        watermark=args.watermark, name=args.name)
            print("Клиент обновлён:")
            _print_client(client)
            return 0
        if args.command == "rotate":
            client, key = await admin.rotate(args.client_id)
            print(f"Новый API-ключ клиента «{client.name}» (старый уже не работает): {key}")
            return 0
        if args.command == "revoke":
            client = await admin.revoke(args.client_id)
            print(f"Клиент «{client.name}» ({client.id}) отключён: запросы с его ключом получают 401.")
            return 0
        if args.command == "usage":
            since = datetime.now(timezone.utc) - timedelta(days=args.days)
            rows = [r for r in await admin.overview(days=args.days) if r.decks_30d]
            print(f"За {args.days} дн. (с {since:%Y-%m-%d %H:%M} UTC):")
            if not rows:
                print("  колод нет")
            for row in rows:
                ok = row.decks_30d - row.failed_30d
                avg = row.cost_30d / row.decks_30d
                print(f"  {row.client.name}: колод {row.decks_30d} (готово {ok}, ошибок {row.failed_30d}), "
                      f"себестоимость {row.cost_30d:.2f} ₽, в среднем {avg:.2f} ₽")
            return 0
    except admin.AdminError as e:
        print(str(e), file=sys.stderr)
        return 1
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
    upd.add_argument("client_id", metavar="client", help="id (ac_…) или имя")
    upd.add_argument("--name")
    upd.add_argument("--daily-limit", type=int)
    upd.add_argument("--rate-limit", type=int)
    upd.add_argument("--decks-per-min", type=int)
    upd.add_argument("--watermark", action=argparse.BooleanOptionalAction, default=None)

    rotate = sub.add_parser("rotate", help="выпустить новый ключ")
    rotate.add_argument("client_id", metavar="client", help="id (ac_…) или имя")
    revoke = sub.add_parser("revoke", help="отключить клиента")
    revoke.add_argument("client_id", metavar="client", help="id (ac_…) или имя")

    usage = sub.add_parser("usage", help="колоды и себестоимость по клиентам")
    usage.add_argument("--days", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
