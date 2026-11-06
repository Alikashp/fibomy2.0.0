"""Закрытый бот (bot/access.py, D-081): пользоваться могут только BOT_ALLOWED_TELEGRAM_IDS и админы,
остальным — ссылка на основной бот, и апдейт дальше не идёт. Пустой список — бот открыт всем.

Апдейты идут через настоящий Dispatcher aiogram; запросы к Telegram перехватывает подменная сессия."""
import asyncio
import unittest
from datetime import datetime, timezone
from unittest import mock

import _helpers  # noqa: F401

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, AnswerPreCheckoutQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, PreCheckoutQuery, Update, User

from bot import access
from config import settings

OWNER, ADMIN, STRANGER = 111, 222, 333
MAIN_BOT = "https://t.me/fibonacci_main_bot"


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


class Access(unittest.TestCase):

    def setUp(self):
        self.patches = [mock.patch.object(settings, "bot_allowed_telegram_ids", str(OWNER)),
                        mock.patch.object(settings, "admin_telegram_ids", str(ADMIN)),
                        mock.patch.object(settings, "main_bot_url", MAIN_BOT)]
        for p in self.patches:
            p.start()
        self.session = FakeSession()
        self.bot = Bot(token="123456:test", session=self.session)
        self.dp = Dispatcher(storage=MemoryStorage())
        access.register(self.dp)
        self.handled = []

        async def on_message(message: Message):
            self.handled.append(("message", message.from_user.id))

        async def on_callback(call: CallbackQuery):
            self.handled.append(("callback", call.from_user.id))

        async def on_checkout(query: PreCheckoutQuery):
            self.handled.append(("checkout", query.from_user.id))

        self.dp.message.register(on_message)
        self.dp.callback_query.register(on_callback)
        self.dp.pre_checkout_query.register(on_checkout)
        self._id = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def feed(self, **kw):
        self._id += 1
        asyncio.run(self.dp.feed_update(self.bot, Update(update_id=self._id, **kw)))

    def send(self, user: int, text: str = "/start"):
        before = len(self.session.calls)
        self.feed(message=Message(message_id=self._id + 1, date=datetime.now(timezone.utc), text=text,
                                  chat=Chat(id=user, type="private"),
                                  from_user=User(id=user, is_bot=False, first_name="T")))
        return [c for c in self.session.calls[before:] if isinstance(c, SendMessage)]

    def test_owner_and_admin_pass(self):
        self.assertEqual(self.send(OWNER), [])
        self.assertEqual(self.send(ADMIN), [])
        self.assertEqual(self.handled, [("message", OWNER), ("message", ADMIN)])

    def test_stranger_gets_link_and_nothing_else(self):
        replies = self.send(STRANGER)
        self.assertEqual(self.handled, [])                                   # диалог не начался
        self.assertEqual(len(replies), 1)
        self.assertIn(MAIN_BOT, replies[0].text)
        self.assertEqual(replies[0].reply_markup.inline_keyboard[0][0].url, MAIN_BOT)
        self.assertEqual(self.send(STRANGER, "тема доклада"), [])          # повтор в течение минуты — без ответа
        self.assertEqual(self.handled, [])

    def test_stranger_button_and_payment(self):
        before = len(self.session.calls)
        self.feed(callback_query=CallbackQuery(
            id="1", chat_instance="ci", data="type:doklad", from_user=User(id=STRANGER, is_bot=False, first_name="T"),
            message=Message(message_id=5, date=datetime.now(timezone.utc), text="?",
                            chat=Chat(id=STRANGER, type="private"))))
        calls = self.session.calls[before:]
        self.assertTrue(any(isinstance(c, AnswerCallbackQuery) for c in calls))
        self.assertTrue(any(isinstance(c, SendMessage) and MAIN_BOT in c.text for c in calls))
        self.feed(pre_checkout_query=PreCheckoutQuery(
            id="2", from_user=User(id=STRANGER, is_bot=False, first_name="T"), currency="XTR",
            total_amount=400, invoice_payload="plan:starter"))
        answer = next(c for c in self.session.calls if isinstance(c, AnswerPreCheckoutQuery))
        self.assertFalse(answer.ok)
        self.assertEqual(self.handled, [])

    def test_empty_list_means_open_bot(self):
        with mock.patch.object(settings, "bot_allowed_telegram_ids", ""):
            self.assertEqual(self.send(STRANGER), [])
        self.assertEqual(self.handled, [("message", STRANGER)])

    def test_without_main_bot_url(self):
        with mock.patch.object(settings, "main_bot_url", ""):
            text, markup = access.closed_reply()
        self.assertEqual((text, markup), (access.CLOSED_TEXT_NO_LINK, None))

    def test_parse_ids(self):
        self.assertEqual(access.parse_ids("111, 222;333  abc", "X"), {111, 222, 333})
        self.assertEqual(access.parse_ids("", "X"), frozenset())


if __name__ == "__main__":
    unittest.main()
