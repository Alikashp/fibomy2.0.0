"""Закрытый бот: пользоваться могут только разрешённые Telegram ID (D-081).

BOT_ALLOWED_TELEGRAM_IDS — Telegram ID через запятую. Пусто — бот открыт всем, как раньше.
Задан — бот отвечает только этим ID и ADMIN_TELEGRAM_IDS (админы проходят всегда); любому
другому пользователю на сообщение, нажатие кнопки или оплату — короткий ответ со ссылкой на
основной бот (MAIN_BOT_URL), и дальше апдейт не идёт: ни диалога, ни FSM, ни очереди.

Проверка — outer-middleware диспетчера (register в main.py до всех обработчиков), поэтому
закрыты все обработчики, включая будущие. Ответ одному и тому же человеку — не чаще раза в
REPLY_EVERY секунд, чтобы на поток сообщений бот не отвечал потоком ссылок.
"""

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PreCheckoutQuery

from config import settings

logger = logging.getLogger(__name__)

REPLY_EVERY = 60.0      # секунд между ответами одному закрытому пользователю
CLOSED_TEXT = "Это служебный бот Fibonacci AI — он закрыт. Презентации делает основной бот:"
CLOSED_TEXT_NO_LINK = "Это служебный бот Fibonacci AI — он закрыт для пользователей."


def parse_ids(value: str, name: str) -> frozenset[int]:
    """«111, 222;333» → {111, 222, 333}; не-числа пропускаются с предупреждением в лог."""
    ids = set()
    for part in (value or "").replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
        elif part:
            logger.warning(f"{name}: not a number, ignored", extra={"value": part[:20]})
    return frozenset(ids)


def allowed_ids() -> frozenset[int] | None:
    """Кому можно пользоваться ботом; None — бот открыт всем (BOT_ALLOWED_TELEGRAM_IDS пуст)."""
    allowed = parse_ids(settings.bot_allowed_telegram_ids, "BOT_ALLOWED_TELEGRAM_IDS")
    if not allowed:
        return None
    return allowed | parse_ids(settings.admin_telegram_ids, "ADMIN_TELEGRAM_IDS")


def is_allowed(user_id: int | None) -> bool:
    allowed = allowed_ids()
    return allowed is None or (user_id is not None and user_id in allowed)


def closed_reply() -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и кнопка для закрытого пользователя."""
    url = (settings.main_bot_url or "").strip()
    if not url:
        return CLOSED_TEXT_NO_LINK, None
    button = InlineKeyboardButton(text="➡️ Открыть основной бот", url=url)
    return f"{CLOSED_TEXT} {url}", InlineKeyboardMarkup(inline_keyboard=[[button]])


class AccessMiddleware(BaseMiddleware):
    """Пропускает апдейт разрешённого пользователя; остальным — ссылка на основной бот."""

    def __init__(self) -> None:
        self._replied: dict[int, float] = {}

    def _should_reply(self, user_id: int) -> bool:
        now = time.monotonic()
        if now - self._replied.get(user_id, -REPLY_EVERY) < REPLY_EVERY:
            return False
        self._replied[user_id] = now
        if len(self._replied) > 10_000:          # память процесса не растёт без предела
            self._replied = {u: t for u, t in self._replied.items() if now - t < REPLY_EVERY}
        return True

    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event: Any, data: dict) -> Any:
        user = getattr(event, "from_user", None)
        if is_allowed(user.id if user else None):
            return await handler(event, data)
        user_id = user.id if user else 0
        logger.info("Closed bot: access denied", extra={"user_id": user_id, "event": type(event).__name__})
        text, markup = closed_reply()
        if isinstance(event, PreCheckoutQuery):
            await event.answer(ok=False, error_message=CLOSED_TEXT_NO_LINK)
            return None
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message is not None and self._should_reply(user_id):
                await event.message.answer(text, reply_markup=markup)
            return None
        if isinstance(event, Message) and event.chat.type == "private" and self._should_reply(user_id):
            await event.answer(text, reply_markup=markup)
        return None


def register(dp: Dispatcher) -> None:
    """Проверка доступа — перед всеми обработчиками сообщений, кнопок и оплаты."""
    middleware = AccessMiddleware()
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.pre_checkout_query.outer_middleware(middleware)
