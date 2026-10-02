"""Админ-команды бота /apikey (bot/admin.py, D-061): доступ только ADMIN_TELEGRAM_IDS в
личном чате, остальным — как незнакомая команда; создание, список, лимит, водяной знак,
отключение с подтверждением, новый ключ; удаление сообщения с ключом через 5 минут.

Апдейты идут через настоящий Dispatcher aiogram; запросы к Telegram перехватывает
подменная сессия. База — SQLite с миграциями."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import _helpers  # noqa: F401

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api import admin, store
from bot import admin as bot_admin
from config import settings
from db import session as db_session
from db.models import Deck

ADMIN, STRANGER = 111, 222


class FakeSession(BaseSession):
    """Запросы к Bot API → список; sendMessage возвращает сообщение с новым id."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self._next_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            return Message(message_id=self._next_id, date=datetime.now(timezone.utc),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


class FakePool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, args, kwargs))


class AdminCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = f"{self._tmp.name}/admin.db"
        sync = create_engine(f"sqlite:///{path}")
        with sync.begin() as conn:
            db_session._run_migrations(conn)
        sync.dispose()
        self._saved = (db_session.engine, db_session.SessionLocal)
        db_session.engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
        db_session.SessionLocal = async_sessionmaker(db_session.engine, class_=AsyncSession, expire_on_commit=False)
        self.ids = mock.patch.object(settings, "admin_telegram_ids", f"{ADMIN}, 333")
        self.ids.start()
        self.session = FakeSession()
        self.bot = Bot(token="123456:test", session=self.session)
        self.pool = FakePool()
        self.dp = Dispatcher(storage=MemoryStorage())
        bot_admin.register(self.dp, get_pool=lambda: self.pool)
        # Остальной бот: всё, что не перехватили админ-команды, попадает сюда
        self.fallthrough = []

        async def rest_of_bot(message: Message):
            self.fallthrough.append(message.text)

        async def rest_of_bot_cb(call: CallbackQuery):
            self.fallthrough.append(call.data)

        self.dp.message.register(rest_of_bot)
        self.dp.callback_query.register(rest_of_bot_cb)
        self._update_id = 0

    def tearDown(self):
        self.ids.stop()
        asyncio.run(db_session.engine.dispose())
        db_session.engine, db_session.SessionLocal = self._saved
        self._tmp.cleanup()

    # ── апдейты ──────────────────────────────────────────────────────────────

    def _feed(self, update: Update):
        asyncio.run(self.dp.feed_update(self.bot, update))

    def send(self, text: str, user: int = ADMIN, chat_type: str = "private") -> list[str]:
        self._update_id += 1
        before = len(self.session.calls)
        chat_id = user if chat_type == "private" else -100500
        self._feed(Update(update_id=self._update_id, message=Message(
            message_id=self._update_id, date=datetime.now(timezone.utc), text=text,
            chat=Chat(id=chat_id, type=chat_type), from_user=User(id=user, is_bot=False, first_name="T"))))
        return [c.text for c in self.session.calls[before:] if isinstance(c, (SendMessage, EditMessageText))]

    def press(self, data: str, user: int = ADMIN) -> list:
        self._update_id += 1
        before = len(self.session.calls)
        self._feed(Update(update_id=self._update_id, callback_query=CallbackQuery(
            id=str(self._update_id), chat_instance="ci", data=data,
            from_user=User(id=user, is_bot=False, first_name="T"),
            message=Message(message_id=5, date=datetime.now(timezone.utc), text="?",
                            chat=Chat(id=user, type="private")))))
        return self.session.calls[before:]

    def last_markup(self):
        return next(c.reply_markup for c in reversed(self.session.calls) if isinstance(c, SendMessage))

    @staticmethod
    def key_from(text: str) -> str:
        return next(word for word in text.replace("<code>", " ").replace("</code>", " ").split()
                    if word.startswith("fib_"))

    def client(self, name: str):
        return asyncio.run(admin.resolve(name, active_only=False))


class Access(AdminCase):

    def test_stranger_gets_unknown_command_behaviour(self):
        for text in ("/apikey", "/apikey list", "/apikey create Чужой 5", "/apikey revoke Второй бот"):
            self.assertEqual(self.send(text, user=STRANGER), [], text)
        self.assertEqual(self.fallthrough, ["/apikey", "/apikey list", "/apikey create Чужой 5",
                                            "/apikey revoke Второй бот"],
                         "сообщение не-админа уходит остальному боту, как незнакомая команда")
        self.assertEqual(asyncio.run(store.list_clients()), [])

    def test_admin_only_in_private_chat(self):
        self.assertEqual(self.send("/apikey create Групповой", chat_type="group"), [])
        self.assertEqual(self.fallthrough, ["/apikey create Групповой"])
        self.assertEqual(asyncio.run(store.list_clients()), [])

    def test_no_admins_configured(self):
        with mock.patch.object(settings, "admin_telegram_ids", ""):
            self.assertEqual(self.send("/apikey list"), [])
        self.assertEqual(self.fallthrough, ["/apikey list"])

    def test_admin_ids_parsing(self):
        with mock.patch.object(settings, "admin_telegram_ids", " 1, 2;3 4,abc,"):
            self.assertEqual(bot_admin.admin_ids(), {1, 2, 3, 4})

    def test_stranger_cannot_press_revoke(self):
        self.send("/apikey create Второй бот")
        client = self.client("Второй бот")
        self.press(f"akr:{client.id}", user=STRANGER)
        self.assertTrue(self.client("Второй бот").active)
        self.assertIn(f"akr:{client.id}", self.fallthrough)

    def test_admin_handlers_registered_before_dialog(self):
        import main
        first = main.dp.message.handlers[0]
        self.assertTrue(any(isinstance(f.callback, bot_admin.IsAdmin) for f in first.filters),
                        "/apikey должен проверяться раньше обработчиков состояний диалога")


class Commands(AdminCase):

    def test_create_shows_key_once_and_schedules_delete(self):
        replies = self.send("/apikey create Второй бот 200")
        self.assertEqual(len(replies), 1)
        text = replies[0]
        key = self.key_from(text)
        self.assertIn("удалю через 5 минут", text)
        self.assertIn("whsec_", text)
        client = asyncio.run(store.client_by_key(key))
        self.assertEqual((client.name, client.daily_limit, client.watermark), ("Второй бот", 200, True))
        sent = [c for c in self.session.calls if isinstance(c, SendMessage)][-1]
        self.assertEqual(self.pool.jobs, [("delete_message_job", (ADMIN, self.session._next_id),
                                           {"_defer_by": 300})])
        self.assertTrue(sent.text.startswith("✅"))

    def test_create_default_limit_quotes_and_duplicates(self):
        self.send("/apikey create Простой")
        self.assertEqual(self.client("Простой").daily_limit, 100)
        self.send('/apikey create «Бот 2» 300')
        self.assertEqual(self.client("Бот 2").daily_limit, 300)
        reply = self.send("/apikey create простой 5")[0]
        self.assertIn("уже есть", reply)
        self.assertIn("⚠️", self.send("/apikey create 500")[0], "имя из одного числа — ошибка")
        self.assertIn("кавычка", self.send('/apikey create "Без конца')[0])

    def test_without_queue_message_deleted_in_process(self):
        dp = Dispatcher(storage=MemoryStorage())
        bot_admin.register(dp, get_pool=lambda: None)
        self.dp = dp
        with mock.patch.object(bot_admin, "SECRET_TTL_SECONDS", 0):
            async def go():
                await dp.feed_update(self.bot, Update(update_id=99, message=Message(
                    message_id=99, date=datetime.now(timezone.utc), text="/apikey create Локальный",
                    chat=Chat(id=ADMIN, type="private"), from_user=User(id=ADMIN, is_bot=False, first_name="T"))))
                await asyncio.sleep(0.05)
            asyncio.run(go())
        deletes = [c for c in self.session.calls if isinstance(c, DeleteMessage)]
        self.assertEqual([(d.chat_id, d.message_id) for d in deletes], [(ADMIN, self.session._next_id)])

    def test_delete_job(self):
        out = asyncio.run(bot_admin.delete_message_job({"bot": self.bot}, ADMIN, 77))
        self.assertEqual(out["message_id"], 77)
        self.assertIsInstance(self.session.calls[-1], DeleteMessage)

    def test_list_with_usage(self):
        self.send("/apikey create Второй бот 200")
        self.send("/apikey create Тест 5")
        client = self.client("Второй бот")
        now = datetime.now(timezone.utc)

        async def decks():
            async with db_session.get_session() as s:
                s.add(Deck(id="dk_A", api_client_id=client.id, request={}, status="done", cost_rub=1.25,
                           created_at=now))
                s.add(Deck(id="dk_B", api_client_id=client.id, request={}, status="failed", cost_rub=0.5,
                           created_at=now))
        asyncio.run(decks())
        self.send("/apikey revoke Тест")
        self.press(f"akr:{self.client('Тест').id}")
        text = self.send("/apikey list")[0]
        self.assertIn("Второй бот", text)
        self.assertIn("Лимит: 200 в сутки · сегодня 1, осталось 199", text)
        self.assertIn("сегодня 1,75 ₽", text)
        self.assertIn("30 дней 1,75 ₽ (колод 2, ошибок 1)", text)
        self.assertIn("<s>Тест</s> · отключён", text)
        self.assertNotIn("fib_", text.replace(client.key_prefix, ""), "полный ключ в списке не показывается")

    def test_limit_and_watermark(self):
        self.send("/apikey create Второй бот")
        self.assertIn("лимит 500", self.send("/apikey limit Второй бот 500")[0])
        self.assertEqual(self.client("Второй бот").daily_limit, 500)
        self.assertIn("выключен", self.send("/apikey watermark второй бот off")[0])
        self.assertFalse(self.client("Второй бот").watermark)
        self.assertEqual(self.client("Второй бот").plan, "pro")
        self.send("/apikey watermark Второй бот on")
        self.assertTrue(self.client("Второй бот").watermark)
        self.assertIn("⚠️", self.send("/apikey limit Второй бот много")[0])
        self.assertIn("⚠️", self.send("/apikey limit Второй бот 0")[0])
        self.assertIn("⚠️", self.send("/apikey watermark Второй бот maybe")[0])
        self.assertIn("не найден", self.send("/apikey limit Третий 5")[0])

    def test_revoke_needs_confirmation(self):
        key = self.key_from(self.send("/apikey create Второй бот")[0])
        reply = self.send("/apikey revoke Второй бот")[0]
        self.assertIn("Отключить клиента", reply)
        buttons = self.last_markup().inline_keyboard[0]
        self.assertTrue(self.client("Второй бот").active, "без нажатия кнопки клиент не отключён")
        calls = self.press(buttons[1].callback_data)           # «Отмена»
        self.assertTrue(self.client("Второй бот").active)
        self.assertIn("не отключён", next(c.text for c in calls if isinstance(c, EditMessageText)))
        calls = self.press(buttons[0].callback_data)           # «Отключить»
        self.assertFalse(self.client("Второй бот").active)
        self.assertIn("отключён", next(c.text for c in calls if isinstance(c, EditMessageText)))
        self.assertTrue(any(isinstance(c, AnswerCallbackQuery) for c in calls))
        self.assertIsNone(asyncio.run(store.client_by_key(key)), "ключ отключённого клиента не работает")
        self.assertIn("отключён", self.send("/apikey rotate Второй бот")[0])

    def test_rotate(self):
        old = self.key_from(self.send("/apikey create Второй бот")[0])
        reply = self.send("/apikey rotate Второй бот")[0]
        new = self.key_from(reply)
        self.assertNotEqual(old, new)
        self.assertIn("Старый уже не работает", reply)
        self.assertIsNone(asyncio.run(store.client_by_key(old)))
        self.assertEqual(asyncio.run(store.client_by_key(new)).name, "Второй бот")
        self.assertEqual(len(self.pool.jobs), 2, "сообщение с новым ключом тоже удаляется")

    def test_help_and_db_missing(self):
        self.assertIn("/apikey create", self.send("/apikey")[0])
        self.assertIn("/apikey list", self.send("/apikey что-то")[0])
        with mock.patch.object(db_session, "SessionLocal", None):
            self.assertIn("База данных не настроена", self.send("/apikey list")[0])

    def test_html_in_name_escaped(self):
        self.send("/apikey create <b>Хак</b>")
        text = self.send("/apikey list")[0]
        self.assertIn("&lt;b&gt;Хак&lt;/b&gt;", text)


class SharedWithCli(AdminCase):
    """CLI python -m api.keys и бот — одна логика api.admin: клиент из бота виден CLI по имени."""

    def test_cli_resolves_bot_client_by_name(self):
        import contextlib
        import io
        from api import keys
        self.send("/apikey create Второй бот 200")

        async def noop():
            return None
        out = io.StringIO()
        with mock.patch.object(keys, "init_db", noop), mock.patch.object(keys, "close_db", noop), \
                contextlib.redirect_stdout(out):
            code = asyncio.run(keys._run(keys.parse_args(["set", "Второй бот", "--daily-limit", "300"])))
        self.assertEqual(code, 0)
        self.assertEqual(self.client("Второй бот").daily_limit, 300)
        err = io.StringIO()
        with mock.patch.object(keys, "init_db", noop), mock.patch.object(keys, "close_db", noop), \
                contextlib.redirect_stderr(err):
            code = asyncio.run(keys._run(keys.parse_args(["create", "--name", "второй БОТ"])))
        self.assertEqual(code, 1)
        self.assertIn("уже есть", err.getvalue())


if __name__ == "__main__":
    unittest.main()
