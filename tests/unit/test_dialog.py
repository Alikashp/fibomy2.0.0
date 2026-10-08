"""Короткий диалог со сводкой (src/dialog.py, обработчики main.py; ТЗ 3.2, D-023)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import dialog
import main


def callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class Defaults(unittest.TestCase):

    def test_new_user(self):
        p = dialog.default_params({}, "en-US", "free")
        self.assertEqual(p, {"language": "en", "slide_count": 9, "audience": "general",
                             "color_scheme": "business_slate", "source_mode": "strict"})

    def test_unsupported_telegram_language_falls_back_to_ru(self):
        self.assertEqual(dialog.default_params({}, "de", "free")["language"], "ru")
        self.assertEqual(dialog.default_params({}, None, "free")["language"], "ru")

    def test_last_choice_wins(self):
        profile = {"last_language": "kk", "last_slide_count": 12, "last_audience": "students",
                   "last_color_scheme": "sunny_cream", "source_mode": "extend"}
        p = dialog.default_params(profile, "en", "free")
        self.assertEqual(p, {"language": "kk", "slide_count": 12, "audience": "students",
                             "color_scheme": "sunny_cream", "source_mode": "extend"})

    def test_legacy_scheme_becomes_theme(self):
        """Профиль со схемой старого движка или темой сессий 1–3 → тема сессии 4 (04_CONTRACTS.md, 8.2; D-063)."""
        for old, theme in (("light", "business_slate"), ("dark", "ember_dark"), ("forest", "mint_coral"),
                           ("ember", "ember_dark"), ("graphite_light", "business_slate"),
                           ("graphite_dark", "ember_dark"), ("azure_coral", "mint_coral"),
                           ("fresh_green", "mint_coral"), ("???", "business_slate"), (None, "business_slate")):
            self.assertEqual(dialog.default_params({"last_color_scheme": old}, None, "free")["color_scheme"], theme)

    def test_last_choices_by_type(self):
        base = {"language": "en", "audience": "clients", "color_scheme": "light", "slide_count": 7,
                "source_mode": "extend"}
        pitch = dialog.last_choices({**base, "presentation_type": "pitch_deck"})
        self.assertNotIn("last_slide_count", pitch)   # у питч-дека число слайдов не выбирается
        self.assertNotIn("source_mode", pitch)
        doklad = dialog.last_choices({**base, "presentation_type": "doklad"})
        self.assertEqual(doklad["last_slide_count"], 7)
        self.assertNotIn("source_mode", doklad)       # режим — только если был материал
        with_text = dialog.last_choices({**base, "presentation_type": "doklad", "source_type": "text"})
        self.assertEqual(with_text["source_mode"], "extend")


class SummaryScreen(unittest.TestCase):
    DATA = {"presentation_type": "doklad", "topic": "Тема <b>", "language": "ru", "slide_count": 9,
            "audience": "general", "color_scheme": "light", "source_mode": "strict"}

    def test_mode_only_with_material(self):
        self.assertNotIn("Режим", dialog.summary_text(self.DATA, "free"))
        self.assertNotIn("sum:mode", callbacks(dialog.summary_keyboard(self.DATA)))
        data = {**self.DATA, "source_type": "text", "raw_text": "x" * 500}
        self.assertIn("Режим: <b>📎 Только мой материал</b>", dialog.summary_text(data, "free"))
        self.assertIn("sum:mode", callbacks(dialog.summary_keyboard(data)))

    def test_topic_escaped_and_all_params_shown(self):
        text = dialog.summary_text(self.DATA, "free")
        self.assertIn("Тема &lt;b&gt;", text)
        for part in ("Тип:", "Язык:", "Слайдов: <b>9</b>", "Аудитория:", "Дизайн:"):
            self.assertIn(part, text)

    def test_pitch_keyboard(self):
        kb = callbacks(dialog.summary_keyboard({**self.DATA, "presentation_type": "pitch_deck"}))
        self.assertIn("sum:brief", kb)
        self.assertNotIn("sum:slides", kb)
        self.assertNotIn("sum:material", kb)
        self.assertEqual(kb[-1], "sum:go")

    def test_four_themes_free(self):
        """Все четыре темы доступны на бесплатном тарифе (вопрос 12), текущая отмечена."""
        _, kb = dialog.change_screen("design", {**self.DATA, "color_scheme": "graphite_dark"}, "free")
        cbs = callbacks(kb)
        for theme in ("business_slate", "ember_dark", "sunny_cream", "mint_coral"):
            self.assertIn(f"set:color_scheme:{theme}", cbs)
        self.assertNotIn("set:color_scheme:locked", cbs)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertIn("✓ 🔥 Тёмная тёплая", labels)

    def test_every_change_screen_returns_to_summary(self):
        for param in ("lang", "slides", "aud", "mode", "design", "type"):
            _, kb = dialog.change_screen(param, self.DATA, "free")
            self.assertEqual(callbacks(kb)[-1], "sum:back")
            for cb in callbacks(kb)[:-1]:
                _, key, value = cb.split(":", 2)
                self.assertTrue(value == "locked" or value in dialog.SETTABLE[key], cb)

    def test_topic_from_text(self):
        self.assertEqual(dialog.topic_from_text("\n  Отчёт о работе склада\nдальше текст"), "Отчёт о работе склада")
        long = dialog.topic_from_text("слово " * 50)
        self.assertLessEqual(len(long), 101)
        self.assertTrue(long.endswith("…"))
        self.assertEqual(dialog.topic_from_file_name("отчёт_за_май.pdf"), "отчёт за май")


# ── Прогон обработчиков main.py на подменных объектах Telegram ──────────────

class FakeMessage:
    _ids = iter(range(100, 10_000))

    def __init__(self, chat, text=None, document=None, lang="ru"):
        self.chat = chat
        self.message_id = next(self._ids)
        self.text = text
        self.document = document
        self.from_user = SimpleNamespace(id=chat.user_id, language_code=lang)
        self.markup = None
        self.body = text

    async def answer(self, text, parse_mode=None, reply_markup=None):
        msg = FakeMessage(self.chat)
        msg.body, msg.markup = text, reply_markup
        self.chat.sent.append(msg)
        return msg

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        self.body, self.markup = text, reply_markup
        self.chat.edits.append(self.message_id)
        return self

    async def edit_reply_markup(self, reply_markup=None):
        self.markup = reply_markup


class Chat:
    def __init__(self, user_id=1, lang="ru"):
        self.id = self.user_id = user_id
        self.lang = lang
        self.sent: list[FakeMessage] = []
        self.edits: list[int] = []
        self.alerts: list[str] = []

    @property
    def last(self) -> FakeMessage:
        return self.sent[-1]

    def call(self, data: str, message: FakeMessage | None = None):
        async def answer(text=None, show_alert=False):
            if text:
                self.alerts.append(text)
        return SimpleNamespace(data=data, message=message or self.last, answer=answer,
                               from_user=SimpleNamespace(id=self.user_id, language_code=self.lang))


class Flow(unittest.TestCase):

    def setUp(self):
        self.profile: dict = {}
        self.saved: list[dict] = []
        self.generated: list[dict] = []
        storage = MemoryStorage()
        self.state = FSMContext(storage, StorageKey(bot_id=1, chat_id=1, user_id=1))

        async def load_profile(user_id):
            return dict(self.profile), "free"

        async def save(user_id, data):
            self.saved.append(dialog.last_choices(data))

        async def confirm(message, data, state):
            self.generated.append(dict(data))
            await state.clear()

        async def store_document(message, state):
            await state.update_data(source_type="document", document_ref="ref", document_mime_type="application/pdf",
                                    source_name=message.document.file_name, raw_text=None)
            return True

        async def noop(*a, **k):
            return None

        for name, fn in (("_load_profile", load_profile), ("_save_last_choices", save),
                         ("_confirm_and_generate", confirm), ("_store_document", store_document)):
            p = mock.patch.object(main, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(main.bot, "edit_message_reply_markup", noop)
        p.start()
        self.addCleanup(p.stop)

    def run_(self, coro):
        return asyncio.run(coro)

    def start(self, chat: Chat, ptype: str):
        async def go():
            await main.cmd_new(FakeMessage(chat, "/new"), self.state)
            await main.on_type(chat.call(f"type:{ptype}"), self.state)
        self.run_(go())

    def test_new_user_topic_without_material(self):
        chat = Chat(lang="en")
        self.start(chat, "doklad")

        async def go():
            await main.on_topic(FakeMessage(chat, "Как работает нейросеть"), self.state)
            self.assertEqual(await self.state.get_state(), main.Gen.material_choice.state)
            await main.on_material_choice(chat.call("mat:no"), self.state)
        self.run_(go())
        summary = chat.last
        self.assertIn("Проверьте параметры", summary.body)
        self.assertIn("English", summary.body)            # язык Telegram пользователя
        self.assertIn("Слайдов: <b>9</b>", summary.body)
        self.assertNotIn("Режим", summary.body)

        async def change_and_go():
            await main.on_summary_action(chat.call("sum:slides", summary), self.state)
            await main.on_set_param(chat.call("set:slide_count:12", summary), self.state)
            await main.on_summary_action(chat.call("sum:go", summary), self.state)
        self.run_(change_and_go())
        self.assertIn("Слайдов: <b>12</b>", summary.body)      # та же сводка отредактирована
        self.assertEqual(self.generated[0]["slide_count"], 12)
        self.assertEqual(self.saved[0]["last_language"], "en")
        self.assertEqual(self.saved[0]["last_slide_count"], 12)

    def test_long_text_is_material(self):
        chat = Chat()
        self.start(chat, "doklad")
        text = "Отчёт склада за квартал\n" + "Выручка выросла. " * 40

        async def go():
            await main.on_topic(FakeMessage(chat, text), self.state)
        self.run_(go())
        data = self.run_(self.state.get_data())
        self.assertEqual(data["topic"], "Отчёт склада за квартал")
        self.assertEqual(data["source_type"], "text")
        self.assertIn("Режим: <b>📎 Только мой материал</b>", chat.last.body)

    def test_file_path_and_every_change(self):
        chat = Chat()
        self.start(chat, "doklad")
        doc = SimpleNamespace(file_name="report.pdf")

        async def go():
            await main.on_topic(FakeMessage(chat, "Итоги года"), self.state)
            await main.on_material_choice(chat.call("mat:yes"), self.state)
            await main.on_material(FakeMessage(chat, document=doc), self.state)
        self.run_(go())
        summary = chat.last
        self.assertIn("файл «report.pdf»", summary.body)
        changes = [("mode", "set:source_mode:extend", "Дополнить общими знаниями"),
                   ("lang", "set:language:uz", "O'zbek"),
                   ("aud", "set:audience:students", "Студенты"),
                   ("slides", "set:slide_count:5", "Слайдов: <b>5</b>"),
                   ("design", "set:color_scheme:mint_coral", "Мята и коралл")]

        async def change():
            for param, choice, expected in changes:
                await main.on_summary_action(chat.call(f"sum:{param}", summary), self.state)
                self.assertNotIn("Проверьте параметры", summary.body)   # экран выбора в том же сообщении
                await main.on_set_param(chat.call(choice, summary), self.state)
                self.assertIn(expected, summary.body)
            # неизвестная тема — alert, сводка не меняется
            await main.on_summary_action(chat.call("sum:design", summary), self.state)
            await main.on_set_param(chat.call("set:color_scheme:forest", summary), self.state)
            await main.on_set_param(chat.call("set:color_scheme:locked", summary), self.state)
            await main.on_summary_action(chat.call("sum:back", summary), self.state)
            # тема текстом — новая сводка
            await main.on_summary_action(chat.call("sum:topic", summary), self.state)
            await main.on_topic(FakeMessage(chat, "Итоги 2025 года"), self.state)
        self.run_(change())
        self.assertEqual(len(chat.alerts), 2)
        data = self.run_(self.state.get_data())
        self.assertEqual(data["color_scheme"], "mint_coral")
        self.assertEqual(data["source_type"], "document")        # материал не потерялся
        self.assertIn("Итоги 2025 года", chat.last.body)
        self.assertIn("Проверьте параметры", chat.last.body)

        async def drop_material():
            await main.on_summary_action(chat.call("sum:material"), self.state)
            await main.on_material_choice(chat.call("mat:no"), self.state)
            await main.on_summary_action(chat.call("sum:go"), self.state)
        self.run_(drop_material())
        self.assertEqual(self.generated[0]["source_type"], "topic")
        self.assertEqual(self.generated[0]["language"], "uz")
        self.assertNotIn("source_mode", self.saved[0])   # без материала режим не перезаписываем

    def test_returning_user_gets_last_choices(self):
        self.profile = {"last_language": "kk", "last_slide_count": 15, "last_audience": "management",
                        "last_color_scheme": "light", "source_mode": "extend"}
        chat = Chat(lang="en")
        self.start(chat, "doklad")

        async def go():
            await main.on_topic(FakeMessage(chat, "Тема " + "текст " * 80), self.state)
        self.run_(go())
        body = chat.last.body
        for part in ("Қазақша", "Слайдов: <b>15</b>", "Руководство", "Дополнить общими знаниями"):
            self.assertIn(part, body)

    def test_pitch_brief_and_type_switch(self):
        chat = Chat()
        self.start(chat, "pitch_deck")

        async def go():
            await main.on_topic(FakeMessage(chat, "Доставка еды для собак"), self.state)
            self.assertEqual(await self.state.get_state(), main.Gen.entering_brief.state)
            await main.on_brief_skip(chat.call("brief:skip"), self.state)
        self.run_(go())
        self.assertIn("Бриф: <b>нет</b>", chat.last.body)
        self.assertIn("Слайдов: <b>11</b>", chat.last.body)

        async def switch():
            await main.on_summary_action(chat.call("sum:type"), self.state)
            await main.on_set_param(chat.call("set:presentation_type:doklad"), self.state)
        self.run_(switch())
        self.assertIn("Тип: <b>Доклад</b>", chat.last.body)
        self.assertIn("Материал: <b>нет — соберём по теме</b>", chat.last.body)

    def test_stale_state_restarts(self):
        chat = Chat()
        msg = FakeMessage(chat)
        self.run_(main.on_summary_action(chat.call("sum:go", msg), self.state))
        self.assertIn("Сессия устарела", chat.last.body)
        self.assertEqual(self.generated, [])


class GenerateRequest(unittest.TestCase):

    def test_request_by_type(self):
        jobs = []

        class Pool:
            async def enqueue_job(self, name, *args, **kw):
                jobs.append((name, args, kw))

        rows, uploads = [], []

        async def create(deck_id, payload, user_id, parent_deck_id=None):
            rows.append(payload)
            return True

        async def put(data, mime=None):
            uploads.append((data, mime))
            return "redis:abc"

        chat = Chat()
        base = {"topic": "Тема", "audience": "general", "language": "ru", "color_scheme": "graphite_dark",
                "slide_count": 12, "source_type": "text", "raw_text": "материал " * 10, "source_mode": "extend",
                "brief": "бриф"}
        with mock.patch.object(main, "arq_pool", Pool()), mock.patch.object(main.deck_store, "create_deck", side_effect=create), \
                mock.patch.object(main, "put_upload", side_effect=put):
            asyncio.run(main.generate_and_send(FakeMessage(chat), {**base, "presentation_type": "doklad"}))
            asyncio.run(main.generate_and_send(FakeMessage(chat), {**base, "presentation_type": "pitch_deck"}))
        # доклад с текстом — новый движок, текст — ссылкой на временное хранилище
        self.assertEqual(jobs[0][0], "generate_deck_job")
        material = rows[0]["input"]["material"]
        self.assertEqual((material["kind"], material["ref"]), ("text", "redis:abc"))
        self.assertEqual(uploads[0], (("материал " * 10).encode(), "text/plain"))
        self.assertEqual((rows[0]["source_mode"], rows[0]["slides_count"], rows[0]["theme_id"]),  # старая тема → новая
                         ("extend", 12, "ember_dark"))
        # питч-дек — старый движок до сессии 3, тема → его схема
        name, _, pitch_kw = jobs[1]
        self.assertEqual(name, "generate_presentation_job")
        pitch = pitch_kw["request_data"]
        self.assertEqual((pitch["source_type"], pitch["slide_count_hint"], pitch["raw_text"]), ("topic", None, None))
        self.assertIn("бриф", pitch["extra_instructions"])
        self.assertEqual(pitch_kw["color_scheme"], "dark")


if __name__ == "__main__":
    unittest.main()
