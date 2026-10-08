"""Админ-команды бота: ключи REST API без railway ssh (D-061).

    /apikey create <имя> [лимит_в_сутки]   новый клиент; ключ показывается один раз
    /apikey list                            клиенты, лимиты, расход за сутки и 30 дней
    /apikey limit <имя> <лимит>             суточный лимит колод
    /apikey watermark <имя> on|off          водяной знак в PDF
    /apikey revoke <имя>                    отключить (с подтверждением кнопкой)
    /apikey rotate <имя>                    новый ключ, старый перестаёт работать

Доступ — только Telegram ID из ADMIN_TELEGRAM_IDS и только в личном чате с ботом.
Для остальных фильтр не срабатывает, и сообщение идёт дальше по обработчикам бота,
как любая незнакомая команда: бот не выдаёт, что команда существует. Поэтому
обработчики регистрируются первыми (register в main.py сразу после Dispatcher):
иначе «/apikey …» в середине диалога перехватил бы обработчик состояния.

Логика — api.admin (общая с python -m api.keys); здесь только разбор и ответы.
Сообщение с ключом бот удаляет через 5 минут — отложенной задачей воркера
delete_message_job (переживает перезапуск бота); без очереди — задачей в процессе.
"""

import asyncio
import html
import logging
import shlex
from typing import Callable

from aiogram import Bot, Dispatcher, F
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from api import admin, store
from config import settings

logger = logging.getLogger(__name__)

SECRET_TTL_SECONDS = 5 * 60
CALLBACK_PREFIX = "akr:"            # подтверждение revoke: akr:<client_id> | akr:cancel
TELEGRAM_LIMIT = 4000

HELP = (
    "🔑 <b>Ключи REST API</b>\n\n"
    "<code>/apikey create Имя [лимит]</code> — новый клиент и ключ (лимит колод в сутки, по умолчанию 100)\n"
    "<code>/apikey list</code> — клиенты, лимиты, расход\n"
    "<code>/apikey limit Имя 500</code> — суточный лимит\n"
    "<code>/apikey watermark Имя on|off</code> — водяной знак в PDF\n"
    "<code>/apikey revoke Имя</code> — отключить клиента\n"
    "<code>/apikey rotate Имя</code> — новый ключ вместо старого\n\n"
    "Имя с пробелами можно писать как есть; если оно кончается числом — в кавычках: "
    "<code>/apikey create \"Бот 2\" 300</code>. Вместо имени подойдёт id <code>ac_…</code>."
)

_background: set[asyncio.Task] = set()


def admin_ids() -> frozenset[int]:
    ids = set()
    for part in settings.admin_telegram_ids.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
        elif part:
            logger.warning("ADMIN_TELEGRAM_IDS: not a number, ignored", extra={"value": part[:20]})
    return frozenset(ids)


class IsAdmin(BaseFilter):
    """Пользователь из ADMIN_TELEGRAM_IDS в личном чате (сообщение или нажатие кнопки)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        message = event.message if isinstance(event, CallbackQuery) else event
        chat = getattr(message, "chat", None)
        return bool(user and chat is not None and chat.type == "private" and user.id in admin_ids())


def register(dp: Dispatcher, get_pool: Callable[[], object | None] = lambda: None) -> None:
    """Регистрирует обработчики первыми в dp. get_pool — пул ARQ (main.arq_pool) для удаления
    сообщения с ключом задачей воркера."""
    async def on_command(message: Message, command: CommandObject, bot: Bot) -> None:
        await handle_command(message, command.args or "", bot, get_pool())

    dp.message.register(on_command, Command("apikey"), IsAdmin())
    dp.callback_query.register(on_revoke_button, F.data.startswith(CALLBACK_PREFIX), IsAdmin())


# ── Разбор ──────────────────────────────────────────────────────────────────

def _tokens(args: str) -> list[str]:
    for a, b in (("“", '"'), ("”", '"'), ("«", '"'), ("»", '"'), ("„", '"')):
        args = args.replace(a, b)
    try:
        return shlex.split(args)
    except ValueError:
        raise admin.AdminError("Не закрыта кавычка в имени.")


def _name_and_int(rest: list[str], what: str, required: bool) -> tuple[str, int | None]:
    if rest and rest[-1].isdigit():
        return " ".join(rest[:-1]), int(rest[-1])
    if required:
        raise admin.AdminError(f"Последним укажите {what} числом, например: /apikey limit Второй бот 500")
    return " ".join(rest), None


def _on_off(value: str) -> bool:
    value = value.lower()
    if value in ("on", "вкл", "да", "yes", "1"):
        return True
    if value in ("off", "выкл", "нет", "no", "0"):
        return False
    raise admin.AdminError("Последним укажите on или off, например: /apikey watermark Второй бот off")


def _e(text: str) -> str:
    return html.escape(text or "", quote=False)


def _money(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + " ₽"


# ── Команды ─────────────────────────────────────────────────────────────────

async def handle_command(message: Message, args: str, bot: Bot, pool=None) -> None:
    try:
        tokens = _tokens(args)
        sub, rest = (tokens[0].lower(), tokens[1:]) if tokens else ("help", [])
        logger.info("Admin command", extra={"admin_id": message.from_user.id, "subcommand": sub})
        if sub == "create":
            name, limit = _name_and_int(rest, "лимит", required=False)
            client, key = await admin.create(name, daily_limit=limit if limit is not None else 100)
            await _send_secret(message, bot, pool, (
                f"✅ Клиент <b>{_e(client.name)}</b> создан (<code>{client.id}</code>).\n"
                f"Лимит: {client.daily_limit} колод в сутки · водяной знак в PDF: "
                f"{'да' if client.watermark else 'нет'} (сменить: <code>/apikey watermark {_e(client.name)} off</code>)\n\n"
                f"API-ключ:\n<code>{key}</code>\n\n"
                f"Секрет webhook (нужен, только если второй бот принимает webhook):\n"
                f"<code>{client.webhook_secret}</code>"))
        elif sub == "list":
            await _send_list(message)
        elif sub == "limit":
            name, limit = _name_and_int(rest, "лимит", required=True)
            client = await admin.set_limit(name, limit)
            await message.answer(f"✅ <b>{_e(client.name)}</b>: лимит {client.daily_limit} колод в сутки.",
                                 parse_mode="HTML")
        elif sub == "watermark":
            if len(rest) < 2:
                raise admin.AdminError("Формат: /apikey watermark Имя on|off")
            client = await admin.set_watermark(" ".join(rest[:-1]), _on_off(rest[-1]))
            await message.answer(f"✅ <b>{_e(client.name)}</b>: водяной знак в PDF — "
                                 f"{'включён' if client.watermark else 'выключен'}.", parse_mode="HTML")
        elif sub == "revoke":
            client = await admin.resolve(" ".join(rest))
            await message.answer(
                f"Отключить клиента <b>{_e(client.name)}</b>? Все запросы с его ключом сразу начнут получать "
                f"ошибку 401, второй бот перестанет делать презентации. Включить обратно нельзя — только "
                f"создать нового клиента.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🛑 Отключить", callback_data=f"{CALLBACK_PREFIX}{client.id}"),
                    InlineKeyboardButton(text="Отмена", callback_data=f"{CALLBACK_PREFIX}cancel"),
                ]]))
        elif sub == "rotate":
            client, key = await admin.rotate(" ".join(rest))
            await _send_secret(message, bot, pool, (
                f"🔄 Новый ключ клиента <b>{_e(client.name)}</b>. Старый уже не работает — замените ключ "
                f"во втором боте.\n\n<code>{key}</code>"))
        else:
            await message.answer(HELP, parse_mode="HTML")
    except admin.AdminError as e:
        await message.answer(f"⚠️ {_e(str(e))}", parse_mode="HTML")
    except store.StoreUnavailable:
        await message.answer("⚠️ База данных не настроена (DATABASE_URL) — ключи хранить негде.")


async def on_revoke_button(call: CallbackQuery) -> None:
    target = call.data[len(CALLBACK_PREFIX):]
    if target == "cancel":
        await call.message.edit_text("Отмена — клиент не отключён.")
        await call.answer()
        return
    try:
        client = await admin.revoke(target)
    except admin.AdminError as e:
        await call.message.edit_text(f"⚠️ {_e(str(e))}", parse_mode="HTML")
        await call.answer()
        return
    logger.info("Admin revoked API client", extra={"admin_id": call.from_user.id, "api_client_id": client.id})
    await call.message.edit_text(f"🛑 Клиент <b>{_e(client.name)}</b> отключён.", parse_mode="HTML")
    await call.answer("Отключён")


async def _send_list(message: Message) -> None:
    rows = await admin.overview()
    if not rows:
        await message.answer("Клиентов API пока нет. Создать: <code>/apikey create Второй бот 200</code>",
                             parse_mode="HTML")
        return
    blocks = ["🔑 <b>Клиенты API</b>\nСутки — по UTC, счётчик обнуляется в 03:00 МСК."]
    for r in rows:
        c = r.client
        if not c.active:
            blocks.append(f"<s>{_e(c.name)}</s> · отключён · 30 дней: {r.decks_30d} колод, {_money(r.cost_30d)}")
            continue
        blocks.append(
            f"<b>{_e(c.name)}</b> · <code>{c.id}</code>\n"
            f"Лимит: {c.daily_limit} в сутки · сегодня {r.used_today}, осталось {r.remaining_today}\n"
            f"Водяной знак: {'да' if c.watermark else 'нет'} · ключ <code>{_e(c.key_prefix)}…</code>\n"
            f"Стоимость: сегодня {_money(r.cost_today)} · 30 дней {_money(r.cost_30d)} "
            f"(колод {r.decks_30d}, ошибок {r.failed_30d})")
    chunk = ""
    for block in blocks:
        if chunk and len(chunk) + len(block) + 2 > TELEGRAM_LIMIT:
            await message.answer(chunk, parse_mode="HTML")
            chunk = ""
        chunk = f"{chunk}\n\n{block}" if chunk else block
    await message.answer(chunk, parse_mode="HTML")


async def _send_secret(message: Message, bot: Bot, pool, text: str) -> None:
    minutes = SECRET_TTL_SECONDS // 60
    sent = await message.answer(
        f"{text}\n\n⚠️ Скопируйте ключ сейчас: он показывается один раз и не хранится — восстановить нельзя, "
        f"только выпустить новый (<code>/apikey rotate</code>). Это сообщение я удалю через {minutes} минут.",
        parse_mode="HTML")
    await schedule_delete(bot, pool, sent.chat.id, sent.message_id)


async def schedule_delete(bot: Bot, pool, chat_id: int, message_id: int) -> None:
    """Удалить сообщение через SECRET_TTL_SECONDS: задачей воркера, иначе — в этом процессе."""
    if pool is not None:
        try:
            await pool.enqueue_job("delete_message_job", chat_id, message_id, _defer_by=SECRET_TTL_SECONDS)
            return
        except Exception as e:
            logger.warning("Delete job not enqueued — deleting from the bot process", extra={"error": str(e)})

    async def later():
        await asyncio.sleep(SECRET_TTL_SECONDS)
        await _delete(bot, chat_id, message_id)

    task = asyncio.create_task(later())
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _delete(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning("Secret message not deleted", extra={"error": str(e)})


async def notify_admins_job(ctx: dict, text: str) -> dict:
    """Задача воркера: сообщение всем ADMIN_TELEGRAM_IDS в личку (лимит клиента API, api.limit_alerts)."""
    sent = 0
    for admin_id in sorted(admin_ids()):
        try:
            await ctx["bot"].send_message(admin_id, text, parse_mode="HTML")
            sent += 1
        except Exception as e:  # noqa: BLE001 — админ не начинал чат с ботом или заблокировал его
            logger.warning("Admin notification not delivered", extra={"admin_id": admin_id, "error": str(e)})
    return {"sent": sent}


async def delete_message_job(ctx: dict, chat_id: int, message_id: int) -> dict:
    """Задача воркера: удалить сообщение с ключом (бот и воркер — один Telegram-бот)."""
    await _delete(ctx["bot"], chat_id, message_id)
    return {"chat_id": chat_id, "message_id": message_id}
