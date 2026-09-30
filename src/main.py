import asyncio
import logging
import sys
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    LabeledPrice, PreCheckoutQuery,
)
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from generation.prompts import load_template
import dialog
from db.session import (
    init_db, close_db, get_session, get_or_create_user, upgrade_user_plan, update_user_profile,
    update_user_last_choices, user_last_choices,
)
from db.models import PlanType
from generation.uploads import close_uploads, put_upload
from logging_setup import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

bot = Bot(token=settings.telegram_bot_token)

# ── ARQ pool — соединение с очередью Redis, к которой обращается worker.py ────
# Генерация (LLM → картинки → PDF) больше НЕ идёт в этом процессе: хендлер
# только кладёт job в очередь и сразу отвечает пользователю. Реальная работа —
# в worker.py (запускается отдельным процессом: `arq worker.WorkerSettings`).
arq_pool: ArqRedis | None = None
# FSM в Redis, а не в памяти процесса: диалог переживает рестарт бота, и бота
# можно масштабировать (ТЗ 4.4). Данные FSM сериализуются в JSON — поэтому в
# них больше нет байтов документа, только ссылка document_ref (см. on_material).
# TTL — чтобы брошенные диалоги не копились в Redis вечно.
FSM_TTL_SECONDS = 7 * 24 * 60 * 60
dp = Dispatcher(storage=RedisStorage.from_url(
    settings.redis_url, state_ttl=FSM_TTL_SECONDS, data_ttl=FSM_TTL_SECONDS,
))

# ── Цены в Telegram Stars ─────────────────────────────────────────────────────
PLANS = {
    "starter": {"stars": 400,  "label": "Starter — 400 ⭐",  "plan": PlanType.STARTER},
    "pro":     {"stars": 800,  "label": "Pro — 800 ⭐",      "plan": PlanType.PRO},
}
# ~$0.013 за звезду → Starter ≈ $5, Pro ≈ $10 (Railway валюта, не доллары)


# ── FSM ───────────────────────────────────────────────────────────────────────

class Gen(StatesGroup):
    """Короткий диалог (ТЗ 3.2, D-023): тип → тема или текст → материал/бриф → сводка.
    Параметры меняются из сводки; флаг from_summary в данных FSM — пользователь
    уже видел сводку, и после ввода нужно вернуться к ней."""
    choosing_type     = State()
    entering_topic    = State()
    material_choice   = State()  # DOKLAD: есть ли текст/документ по теме
    entering_material = State()  # DOKLAD: пользователь шлёт текст/документ по теме
    entering_brief    = State()  # PITCH_DECK: необязательный бриф
    summary           = State()


class Profile(StatesGroup):
    """ФИО/группа докладчика для титульного и финального слайдов DOKLAD.
    Не завязан на Gen — отдельный, самостоятельный маленький флоу, вызываемый
    из постоянного меню в любой момент (см. kb_reply_menu)."""
    entering_name  = State()
    entering_group = State()


# ── Клавиатуры ────────────────────────────────────────────────────────────────

def kb_types() -> InlineKeyboardMarkup:
    # Остальные типы (DIPLOMA, EDUCATIONAL, SALES, ROADMAP, CONFERENCE отдельным
    # пунктом) намеренно скрыты из выбора — код и enum-значения не трогаем,
    # они пригодятся, когда будем возвращать типы в меню по одному.
    # DOKLAD сейчас — единственная замена CONFERENCE в пользовательском флоу:
    # какой именно HTML-шаблон (conference/corp_report) получится, решает
    # source_type запроса, а не выбор пользователя здесь (см. template_engine).
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"type:{key}") for key, label in dialog.TYPE_BUTTONS.items()],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой 'Назад' — для шагов со свободным текстовым вводом."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


def kb_reply_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода — доступно в любой момент диалога,
    включая середину онбординга (см. Task 2: не должно быть тупиков)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Новая презентация")],
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💳 Тарифы")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def _kb_profile_skip(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Пропустить", callback_data=callback_data)],
    ])


def kb_paywall() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Starter — 400 звёзд", callback_data="pay:starter")],
        [InlineKeyboardButton(text="⭐ Pro — 800 звёзд",     callback_data="pay:pro")],
    ])


# ── Лейблы ────────────────────────────────────────────────────────────────────

MENU_TEXT = "📋 <b>Меню</b>\n\nВыберите действие:"

HELP_TEXT = (
    "❓ <b>Что умеет Fibonacci AI</b>\n\n"
    "Собираю презентацию в PDF за 60–90 секунд: питч-дек для инвесторов или доклад "
    "на любую тему — с нуля по названию темы или по вашему готовому тексту/документу "
    "(только по нему или с дополнением общими знаниями — на ваш выбор).\n\n"
    "Команды:\n"
    "/new — начать новую презентацию\n"
    "/plan — тарифы и лимит бесплатных презентаций\n"
    "/menu — открыть это меню"
)


def _topic_prompt_text(ptype: str) -> str:
    if ptype == "doklad":
        topic_prompt = (
            "✏️ Напишите тему презентации или пришлите готовый текст / документ.\n"
            "<i>Например: «Как работает нейросеть простыми словами». "
            "Длинный текст или файл станут материалом доклада.</i>"
        )
    else:
        topic_prompt = (
            "✏️ Напишите тему презентации.\n"
            "<i>Например: «Стартап по доставке еды для собак»</i>"
        )
    return f"Тип: <b>{dialog.TYPE_LABELS.get(ptype, ptype)}</b>\n\n{topic_prompt}"


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    # Регистрируем пользователя
    async with get_session() as session:
        if session:
            user = await get_or_create_user(
                session,
                user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                language_code=message.from_user.language_code,
            )
            left = user.presentations_left
            if user.plan != "free":
                plan_info = f"\n\n💎 План: {user.plan} · Презентации: ∞"
            else:
                plan_info = f"\n\n🎁 Бесплатно осталось: {left} из 2"
        else:
            plan_info = ""

    # Reply-клавиатура (постоянное меню) и inline-выбор типа — разные виды
    # reply_markup, в одно сообщение оба не помещаются, поэтому два сообщения:
    # первое закрепляет меню внизу экрана, второе — обычный шаг флоу.
    await message.answer(
        f"👋 Привет! Я <b>Fibonacci AI</b> — создаю профессиональные презентации за 60 секунд.{plan_info}",
        parse_mode="HTML",
        reply_markup=kb_reply_menu(),
    )
    await message.answer(
        "Выберите тип презентации:",
        reply_markup=kb_types(),
    )
    await state.set_state(Gen.choosing_type)


@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    # /menu должна прерывать любой FSM state и не оставлять бота в тупике —
    # поэтому всегда clear(), даже если пользователь застрял на середине
    # онбординга (например, в цикле "слишком коротко").
    await state.clear()
    await message.answer(MENU_TEXT, parse_mode="HTML", reply_markup=kb_reply_menu())


@dp.message(F.text == "🚀 Новая презентация")
async def on_menu_new_presentation(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите тип презентации:", reply_markup=kb_types())
    await state.set_state(Gen.choosing_type)


@dp.message(F.text == "💳 Тарифы")
async def on_menu_plans(message: Message, state: FSMContext):
    await state.clear()
    await cmd_plan(message)


@dp.message(F.text == "❓ Помощь")
async def on_menu_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, parse_mode="HTML")


# ── Профиль докладчика (ФИО / группа) ───────────────────────────────────────
# Отдельный маленький флоу, не связанный с Gen — доступен в любой момент из
# постоянного меню. Значения сохраняются в БД и подставляются в
# PresentationMeta.author_name/author_group для DOKLAD в worker.py — сама
# модель их не заполняет.

_PROFILE_FIELD_MAX = 150


@dp.message(F.text == "👤 Профиль")
async def on_menu_profile(message: Message, state: FSMContext):
    await state.clear()
    async with get_session() as session:
        current_name = None
        if session:
            user = await get_or_create_user(session, message.from_user.id)
            current_name = user.author_name
            current_group = user.author_group
        else:
            current_group = None

    current_line = ""
    if current_name or current_group:
        parts = [p for p in (current_name, current_group) if p]
        current_line = f"\n\nСейчас указано: <b>{' | '.join(parts)}</b>"

    await message.answer(
        "👤 <b>Профиль докладчика</b>\n\n"
        "ФИО и группа/организация будут на титульном и финальном слайде доклада. "
        f"Необязательно.{current_line}\n\nУкажите ФИО:",
        parse_mode="HTML",
        reply_markup=_kb_profile_skip("profile_skip_name"),
    )
    await state.set_state(Profile.entering_name)


async def _ask_profile_group(target: Message, state: FSMContext) -> None:
    await target.answer(
        "Укажите группу или организацию:",
        reply_markup=_kb_profile_skip("profile_skip_group"),
    )
    await state.set_state(Profile.entering_group)


@dp.message(Profile.entering_name)
async def on_profile_name_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()[:_PROFILE_FIELD_MAX]
    await state.update_data(author_name=text or None)
    await _ask_profile_group(message, state)


@dp.callback_query(F.data == "profile_skip_name")
async def on_profile_skip_name(call: CallbackQuery, state: FSMContext):
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.update_data(author_name=None)
    await _ask_profile_group(call.message, state)


async def _save_profile_and_confirm(target: Message, state: FSMContext, user_id: int, author_group: str | None) -> None:
    data = await state.get_data()
    author_name = data.get("author_name")
    await state.clear()

    async with get_session() as session:
        if session:
            user = await get_or_create_user(session, user_id)
            await update_user_profile(session, user, author_name, author_group)

    parts = [p for p in (author_name, author_group) if p]
    summary = f"\n\n<b>{' | '.join(parts)}</b>" if parts else "\n\nПоля не заполнены — доклад будет без подписи."
    await target.answer(
        f"✅ Профиль сохранён.{summary}",
        parse_mode="HTML",
        reply_markup=kb_reply_menu(),
    )


@dp.message(Profile.entering_group)
async def on_profile_group_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()[:_PROFILE_FIELD_MAX]
    await _save_profile_and_confirm(message, state, message.from_user.id, text or None)


@dp.callback_query(F.data == "profile_skip_group")
async def on_profile_skip_group(call: CallbackQuery, state: FSMContext):
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _save_profile_and_confirm(call.message, state, call.from_user.id, None)


@dp.message(Command("plan"))
async def cmd_plan(message: Message):
    async with get_session() as session:
        if not session:
            await message.answer("База данных недоступна.")
            return
        user = await get_or_create_user(session, message.from_user.id)
        if user.plan == "free":
            text = (
                f"📊 Ваш план: <b>Free</b>\n"
                f"Использовано: {user.presentations_count} из 2\n\n"
                f"Для продолжения работы оформите подписку:"
            )
            await message.answer(text, parse_mode="HTML", reply_markup=kb_paywall())
        else:
            # plan хранится в БД строкой ("starter"/"pro"), а не PlanType —
            # раньше здесь был user.plan.value, и /plan у платного падал.
            await message.answer(
                f"📊 Ваш план: <b>{str(user.plan).title()}</b>\n"
                f"Всего сгенерировано: {user.presentations_count} презентаций",
                parse_mode="HTML",
            )


@dp.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите тип презентации:", reply_markup=kb_types())
    await state.set_state(Gen.choosing_type)


@dp.callback_query(F.data.startswith("type:"))
async def on_type(call: CallbackQuery, state: FSMContext):
    ptype = call.data.split(":", 1)[1]
    await call.answer()
    if ptype not in dialog.TYPE_BUTTONS:
        return
    # Язык Telegram — запасной дефолт языка презентации (dialog.default_params)
    await state.update_data(presentation_type=ptype, tg_lang=call.from_user.language_code)
    await _edit(call.message, _topic_prompt_text(ptype), kb_back())
    await state.set_state(Gen.entering_topic)


def _text_material(text: str) -> dict:
    return dict(source_type="text", raw_text=text[:dialog.MATERIAL_MAX_CHARS], source_name=None,
                document_ref=None, document_mime_type=None, last_short_text_error_msg_id=None)


_NO_MATERIAL = dict(source_type="topic", raw_text=None, source_name=None,
                    document_ref=None, document_mime_type=None)


@dp.message(Gen.entering_topic)
async def on_topic(message: Message, state: FSMContext):
    """Шаг 2: тема или текст. Длинный текст — это материал доклада (или бриф
    питч-дека), тема берётся из его первой строки; файл к докладу — тоже сюда."""
    data = await state.get_data()
    ptype = data.get("presentation_type", "doklad")

    if message.document:
        if ptype != "doklad":
            await message.answer("Файл можно приложить к докладу. Для питч-дека напишите тему.")
            return
        if not await _store_document(message, state):
            return
        if not data.get("topic"):
            await state.update_data(topic=dialog.topic_from_file_name(message.document.file_name))
        await _show_summary(message, state, message.from_user.id, edit=False)
        return

    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Тема слишком короткая — напишите хотя бы несколько слов.")
        return
    if len(text) > dialog.TOPIC_MAX_CHARS:
        if ptype == "doklad":
            await state.update_data(topic=dialog.topic_from_text(text), **_text_material(text))
        else:
            await state.update_data(topic=dialog.topic_from_text(text), brief=text[:BRIEF_MAX_CHARS])
        await _show_summary(message, state, message.from_user.id, edit=False)
        return

    await state.update_data(topic=text)
    if data.get("from_summary"):
        await _show_summary(message, state, message.from_user.id, edit=False)
    else:
        await _ask_material_step(message, state)


def _kb_material_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Да, пришлю файл или текст", callback_data="mat:yes")],
        [InlineKeyboardButton(text="🆕 Нет, по теме", callback_data="mat:no")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


_BRIEF_PROMPT = (
    "📝 <b>Расскажите о проекте</b> — необязательно, но улучшит результат.\n\n"
    "<i>— Команда: имена, роли, опыт\n"
    "— Тракшн: пользователи, выручка, рост\n"
    "— Инвестиции: сколько ищете\n"
    "— Контакты: email, telegram</i>\n\n"
    "Или нажмите кнопку ниже чтобы пропустить."
)
BRIEF_MAX_CHARS = 2000


def _kb_brief(from_summary: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🗑 Без брифа" if from_summary else "⚡ Пропустить",
                                  callback_data="brief:skip")]]
    rows.append([dialog.BACK_TO_SUMMARY] if from_summary
                else [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_material_step(message: Message, state: FSMContext) -> None:
    """Шаг 3: материал для доклада, бриф для питч-дека."""
    data = await state.get_data()
    if data.get("presentation_type") == "pitch_deck":
        await message.answer(_BRIEF_PROMPT, parse_mode="HTML", reply_markup=_kb_brief(False))
        await state.set_state(Gen.entering_brief)
    else:
        await message.answer("📎 <b>Есть готовый текст или документ по теме?</b>",
                             parse_mode="HTML", reply_markup=_kb_material_choice())
        await state.set_state(Gen.material_choice)


_MATERIAL_PROMPT = "📎 Пришлите текст сообщением или документ файлом (.pdf, .docx, .pptx, .txt, до 20 МБ)."


@dp.callback_query(F.data.in_({"mat:yes", "mat:no"}))
async def on_material_choice(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not (await state.get_data()).get("topic"):
        await _restart(call.message, state)
        return
    if call.data == "mat:yes":
        await _edit(call.message, _MATERIAL_PROMPT, kb_back())
        await state.set_state(Gen.entering_material)
        return
    # «Нет» на первом проходе или «Без материала» из сводки: старый материал не уходит в генерацию
    await state.update_data(**_NO_MATERIAL)
    await _show_summary(call.message, state, call.from_user.id, edit=True)


@dp.message(Gen.entering_brief)
async def on_brief(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пришлите бриф текстом или нажмите «Пропустить».")
        return
    if len(text) > BRIEF_MAX_CHARS:
        await message.answer(f"Слишком длинный текст. Сократите до {BRIEF_MAX_CHARS} символов.")
        return
    await state.update_data(brief=text)
    await _show_summary(message, state, message.from_user.id, edit=False)


@dp.callback_query(F.data == "brief:skip")
async def on_brief_skip(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not (await state.get_data()).get("topic"):
        await _restart(call.message, state)
        return
    await state.update_data(brief=None)
    await _show_summary(call.message, state, call.from_user.id, edit=True)


# ── Сводка параметров ────────────────────────────────────────────────────────
# Все параметры на одном экране, каждый меняется кнопкой «Сменить…», после
# выбора — снова сводка в том же сообщении (edit_message). Тексты и клавиатуры —
# dialog.py. Дефолты — прошлый выбор из профиля (users.last_*), см. D-023.

async def _edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    """Редактирует сообщение бота; не получилось (удалено, слишком старое) — присылает новое."""
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        return message
    except Exception:
        return await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def _load_profile(user_id: int) -> tuple[dict, str]:
    """(прошлый выбор, тариф). Без БД — пустой профиль и free."""
    async with get_session() as session:
        if session:
            user = await get_or_create_user(session, user_id)
            return user_last_choices(user), str(user.plan)
    return {}, "free"


async def _save_last_choices(user_id: int, data: dict) -> None:
    async with get_session() as session:
        if session:
            user = await get_or_create_user(session, user_id)
            await update_user_last_choices(session, user, dialog.last_choices(data))


async def _show_summary(target: Message, state: FSMContext, user_id: int, edit: bool) -> None:
    data = await state.get_data()
    if not data.get("defaults_loaded"):
        profile, plan = await _load_profile(user_id)
        await state.update_data(**dialog.default_params(profile, data.get("tg_lang"), plan),
                                plan=plan, defaults_loaded=True)
        data = await state.get_data()
    text = dialog.summary_text(data, data.get("plan", "free"))
    kb = dialog.summary_keyboard(data)
    if edit:
        target = await _edit(target, text, kb)
    else:
        # После текстового ввода сводка приходит новым сообщением; у старой снимаем
        # кнопки, чтобы в чате была одна живая сводка.
        if data.get("summary_msg_id"):
            try:
                await bot.edit_message_reply_markup(chat_id=target.chat.id, message_id=data["summary_msg_id"],
                                                    reply_markup=None)
            except Exception:
                pass
        target = await target.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.update_data(summary_msg_id=target.message_id, from_summary=True)
    await state.set_state(Gen.summary)


async def _restart(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Сессия устарела. Начнём заново — выберите тип презентации:", reply_markup=kb_types())
    await state.set_state(Gen.choosing_type)


@dp.callback_query(F.data.startswith("sum:"))
async def on_summary_action(call: CallbackQuery, state: FSMContext):
    action = call.data.split(":", 1)[1]
    await call.answer()
    data = await state.get_data()
    if not data.get("topic") or not data.get("presentation_type"):
        await _restart(call.message, state)
        return

    if action == "back":
        await _show_summary(call.message, state, call.from_user.id, edit=True)
    elif action == "go":
        await _generate_from_summary(call, state)
    elif action == "topic":
        await _edit(call.message, "✏️ Напишите новую тему или пришлите текст для презентации.",
                    InlineKeyboardMarkup(inline_keyboard=[[dialog.BACK_TO_SUMMARY]]))
        await state.set_state(Gen.entering_topic)
    elif action == "material":
        rows = [[InlineKeyboardButton(text="🗑 Без материала — по теме", callback_data="mat:no")]] \
            if dialog.has_material(data) else []
        rows.append([dialog.BACK_TO_SUMMARY])
        await _edit(call.message, _MATERIAL_PROMPT, InlineKeyboardMarkup(inline_keyboard=rows))
        await state.set_state(Gen.entering_material)
    elif action == "brief":
        await _edit(call.message, _BRIEF_PROMPT, _kb_brief(True))
        await state.set_state(Gen.entering_brief)
    elif action in ("lang", "slides", "aud", "mode", "design", "type"):
        text, kb = dialog.change_screen(action, data, data.get("plan", "free"))
        await _edit(call.message, text, kb)


@dp.callback_query(F.data.startswith("set:"))
async def on_set_param(call: CallbackQuery, state: FSMContext):
    _, key, value = call.data.split(":", 2)
    data = await state.get_data()
    if key == "color_scheme" and (value == "locked" or not dialog.scheme_allowed(value, data.get("plan", "free"))):
        await call.answer("🔒 Доступно на платном плане. Используйте /plan", show_alert=True)
        return
    await call.answer()
    if value not in dialog.SETTABLE.get(key, ()):
        return
    if not data.get("topic"):
        await _restart(call.message, state)
        return
    update = {key: int(value) if key == "slide_count" else value}
    if key == "presentation_type" and value == "pitch_deck" and dialog.has_material(data):
        # Питч-дек не берёт материал: текст переходит в бриф, файл — нет (сводка это покажет)
        if data.get("source_type") == "text" and not data.get("brief"):
            update["brief"] = (data.get("raw_text") or "")[:BRIEF_MAX_CHARS]
        update.update(_NO_MATERIAL)
    await state.update_data(**update)
    await _show_summary(call.message, state, call.from_user.id, edit=True)


@dp.message(Gen.summary)
async def on_summary_text(message: Message, state: FSMContext):
    await message.answer("Параметры меняются кнопками в сводке выше. Чтобы сменить тему — «✏️ Сменить тему».")


@dp.callback_query(F.data.regexp(r"^(aud|lang|scheme|oq|oq_skip):"))
async def on_stale_button(call: CallbackQuery, state: FSMContext):
    """Кнопки старого пошагового диалога в истории чата."""
    await call.answer()
    await _restart(call.message, state)


# ── Кнопка "Назад" ───────────────────────────────────────────────────────────
# Шаги до сводки: тип → тема → материал/бриф. После сводки «Назад» возвращает к ней.

@dp.callback_query(F.data == "back")
async def on_back(call: CallbackQuery, state: FSMContext):
    await call.answer()
    current = await state.get_state()
    data = await state.get_data()
    from_summary = bool(data.get("from_summary"))

    if current == Gen.choosing_type.state:
        await state.clear()
        await call.message.answer(MENU_TEXT, parse_mode="HTML", reply_markup=kb_reply_menu())
        return

    if from_summary and current in (Gen.entering_topic.state, Gen.entering_material.state,
                                    Gen.entering_brief.state, Gen.material_choice.state, Gen.summary.state):
        await _show_summary(call.message, state, call.from_user.id, edit=True)
        return

    if current == Gen.entering_topic.state:
        await _edit(call.message, "Выберите тип презентации:", kb_types())
        await state.set_state(Gen.choosing_type)
        return

    if current in (Gen.material_choice.state, Gen.entering_brief.state):
        await _edit(call.message, _topic_prompt_text(data.get("presentation_type", "doklad")), kb_back())
        await state.set_state(Gen.entering_topic)
        return

    if current == Gen.entering_material.state:
        await _edit(call.message, "📎 <b>Есть готовый текст или документ по теме?</b>", _kb_material_choice())
        await state.set_state(Gen.material_choice)
        return

    # Неизвестное/устаревшее состояние — не оставляем пользователя в тупике.
    await _restart(call.message, state)


# ── DOKLAD: приём готового текста/документа по теме ────────────────────────────

_MATERIAL_MIME_BY_EXT = {
    "pdf":  "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt":  "text/plain",
}
_MATERIAL_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ


async def _store_document(message: Message, state: FSMContext) -> bool:
    """Файл пользователя → временное хранилище (ссылка в FSM). False — файл не принят."""
    doc = message.document
    ext = doc.file_name.rsplit(".", 1)[-1].lower() if doc.file_name and "." in doc.file_name else ""
    mime = _MATERIAL_MIME_BY_EXT.get(ext)
    if not mime:
        await message.answer(
            "Поддерживаются только .pdf, .docx, .pptx, .txt. "
            "Пришлите другой файл, или текст сообщением."
        )
        return False
    if doc.file_size and doc.file_size > _MATERIAL_MAX_FILE_SIZE:
        await message.answer("Файл слишком большой (максимум 20 МБ). Пришлите файл поменьше или текст сообщением.")
        return False

    file = await bot.get_file(doc.file_id)
    buf = await bot.download_file(file.file_path)
    try:
        document_ref = await put_upload(buf.read(), mime)
    except Exception:
        logger.exception("Failed to store uploaded document")
        await message.answer("Не удалось сохранить файл. Попробуйте прислать его ещё раз.")
        return False

    await state.update_data(
        source_type="document",
        document_ref=document_ref,
        document_mime_type=mime,
        source_name=(doc.file_name or "")[:255] or None,
        raw_text=None,
        last_short_text_error_msg_id=None,
    )
    return True


@dp.message(Gen.entering_material)
async def on_material(message: Message, state: FSMContext):
    data = await state.get_data()

    if message.document:
        if not await _store_document(message, state):
            return
    elif message.text:
        text = message.text.strip()
        if len(text) < 20:
            await _report_short_text_error(message, state, data)
            return
        await state.update_data(**_text_material(text))
    else:
        await message.answer("Пришлите текст сообщением или документ файлом.")
        return

    await _show_summary(message, state, message.from_user.id, edit=False)


# Повторная ошибка "слишком коротко" подряд — не спамим тем же текстом ещё раз,
# а редактируем предыдущее сообщение об ошибке. Плюс кнопки, чтобы не оставлять
# пользователя в тупике (тот же принцип, что и с "Назад" в задаче 1).
_SHORT_TEXT_ERROR = "Слишком коротко — пришлите более развёрнутый текст (от 20 символов) или документ."


def _kb_short_text_error() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Написать текст заново", callback_data="material_retry_text")],
        [InlineKeyboardButton(text="📄 Загрузить файл", callback_data="material_retry_file")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


async def _report_short_text_error(message: Message, state: FSMContext, data: dict) -> None:
    last_error_id = data.get("last_short_text_error_msg_id")
    if last_error_id:
        try:
            await bot.edit_message_text(
                _SHORT_TEXT_ERROR,
                chat_id=message.chat.id,
                message_id=last_error_id,
                reply_markup=_kb_short_text_error(),
            )
            return
        except Exception:
            pass  # сообщение могли удалить — просто пришлём новое ниже
    sent = await message.answer(_SHORT_TEXT_ERROR, reply_markup=_kb_short_text_error())
    await state.update_data(last_short_text_error_msg_id=sent.message_id)


@dp.callback_query(F.data == "material_retry_text")
async def on_material_retry_text(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text(
        "✍️ Пришлите текст по теме сообщением (от 20 символов).",
        reply_markup=kb_back(),
    )


@dp.callback_query(F.data == "material_retry_file")
async def on_material_retry_file(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text(
        "📄 Пришлите документ файлом (.pdf, .docx, .pptx, .txt, до 20 МБ).",
        reply_markup=kb_back(),
    )


@dp.callback_query(F.data == "action:new")
async def on_new(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("Выберите тип презентации:", reply_markup=kb_types())
    await state.set_state(Gen.choosing_type)
    await call.answer()


# ── Оплата через Telegram Stars ───────────────────────────────────────────────

@dp.callback_query(F.data.startswith("pay:"))
async def on_pay(call: CallbackQuery):
    plan_key = call.data.split(":")[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await call.answer("Неизвестный тариф")
        return

    # Только то, что реально работает. AI-изображения и приоритетная очередь
    # не реализованы — не обещаем их, пока не появятся (ТЗ, раздел 8, п. 3).
    plan_descriptions = {
        "starter": "15 презентаций · Все цветовые схемы · Без водяного знака",
        "pro": "50 презентаций · Все цветовые схемы · Без водяного знака",
    }

    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"Fibonacci AI — {plan_key.title()}",
        description=plan_descriptions.get(plan_key, ""),
        payload=f"plan:{plan_key}",
        currency="XTR",  # Telegram Stars
        prices=[LabeledPrice(label=plan["label"], amount=plan["stars"])],
    )
    await call.answer()


@dp.pre_checkout_query()
async def on_pre_checkout(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)


@dp.message(F.successful_payment)
async def on_successful_payment(message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload  # "plan:starter" или "plan:pro"
    plan_key = payload.split(":")[1] if ":" in payload else "starter"
    plan_info = PLANS.get(plan_key, PLANS["starter"])

    async with get_session() as session:
        if session:
            user = await get_or_create_user(session, message.from_user.id)
            await upgrade_user_plan(
                session,
                user=user,
                plan=plan_info["plan"].value,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                stars_amount=payment.total_amount,
            )

    plan_limits = {"starter": "15", "pro": "50"}
    await message.answer(
        f"🎉 Оплата прошла! Добро пожаловать в <b>{plan_key.title()}</b>.\n\n"
        f"Доступно презентаций: <b>{plan_limits.get(plan_key, '∞')}</b>\n"
        f"Водяной знак: <b>убран</b>\n\n"
        f"Создайте первую презентацию: /new",
        parse_mode="HTML",
    )


# ── Подтверждение и запуск ────────────────────────────────────────────────────

async def _generate_from_summary(call: CallbackQuery, state: FSMContext) -> None:
    """«✅ Сгенерировать презентацию»: запоминаем выбор в профиль и запускаем."""
    data = await state.get_data()
    try:
        await call.message.edit_reply_markup(reply_markup=None)  # повторное нажатие не поставит вторую задачу
    except Exception:
        pass
    try:
        await _save_last_choices(call.from_user.id, data)
    except Exception:
        logger.exception("Failed to save last choices")
    await _confirm_and_generate(call.message, data, state)


async def _confirm_and_generate(message: Message, data: dict, state: FSMContext):
    # Проверяем лимит
    async with get_session() as session:
        if session:
            user = await get_or_create_user(
                session,
                user_id=message.chat.id,
            )
            if not user.can_generate:
                await state.clear()
                await message.answer(
                    "😔 Вы использовали все бесплатные презентации (2 из 2).\n\n"
                    "Оформите подписку чтобы продолжить:",
                    reply_markup=kb_paywall(),
                )
                return
            watermark = user.plan == "free"
        else:
            watermark = True

    # Параметры пользователь только что видел в сводке — не повторяем их.
    await state.clear()
    await generate_and_send(message, data, watermark=watermark)


# ── Генерация ─────────────────────────────────────────────────────────────────
# Хендлер больше не генерирует презентацию сам — он кладёт job в очередь ARQ
# (worker.py, отдельный процесс) и сразу отвечает. Это единственная причина,
# по которой бот не виснет на 60-90 секунд генерации и продолжает отвечать на
# /start, /plan и прочие команды других пользователей, пока одна презентация
# ещё готовится.

BRIEF_TEMPLATE = load_template("brief.txt")


async def generate_and_send(message: Message, data: dict, watermark: bool = True):
    ptype = data["presentation_type"]
    # Бриф — только питч-деку (после «Сменить тип» в данных мог остаться)
    brief = data.get("brief") if ptype == "pitch_deck" else None
    extra = None
    if brief:
        extra = BRIEF_TEMPLATE.substitute(brief=brief)

    # Собираем request_data как обычный dict, а не через UserRequest(...) —
    # для DOKLAD+document source_type != "topic", а raw_text ещё не заполнен
    # (текст извлечёт content_extractor внутри воркера из документа по document_ref).
    # UserRequest-валидатор требует raw_text сразу, как только source_type
    # != topic, так что собирать полноценный Pydantic-объект здесь, до
    # экстракции, нельзя — тот же капкан, что уже чинили в worker.py.
    # Материал — только докладу; у питч-дека текст пользователя идёт брифом
    source_type = (data.get("source_type") or "topic") if ptype == "doklad" else "topic"
    request_data = {
        "topic": data["topic"],
        "presentation_type": data["presentation_type"],
        "audience": data["audience"],
        "language": data["language"],
        "extra_instructions": extra,
        "source_type": source_type,
        "raw_text": data.get("raw_text") if source_type == "text" else None,
        # Вопрос об объёме убран из диалога (D-023): объём задаёт число слайдов
        "content_volume": "medium",
        # У питч-дека число слайдов задаёт его структура — не передаём
        "slide_count_hint": int(data["slide_count"]) if ptype == "doklad" and data.get("slide_count") else None,
        "source_mode": data.get("source_mode") if source_type != "topic" else None,
        # Имя файла — для подписи источника чисел «по данным: …» (postprocess.assign_sources)
        "source_name": data.get("source_name") if source_type == "document" else None,
    }

    job_id = uuid.uuid4().hex[:12]

    status_msg = await message.answer(
        f"⏳ <b>Генерирую, обычно занимает 60–90 секунд.</b>\n"
        f"Пришлю сюда же, как только будет готово.\n\n"
        f"<code>job {job_id}</code>",
        parse_mode="HTML",
    )

    if arq_pool is None:
        logger.error("ARQ pool is not initialized — cannot enqueue job")
        await status_msg.edit_text("❌ Очередь генерации сейчас недоступна. Попробуйте через минуту.")
        return

    await arq_pool.enqueue_job(
        "generate_presentation_job",
        job_id=job_id,
        chat_id=message.chat.id,
        user_id=message.chat.id,
        status_message_id=status_msg.message_id,
        request_data=request_data,
        document_ref=data.get("document_ref") if source_type == "document" else None,
        document_mime_type=data.get("document_mime_type") if source_type == "document" else None,
        urls=None,
        watermark=watermark,
        color_scheme=data.get("color_scheme", "light"),
        _job_id=job_id,
    )


# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    global arq_pool

    logger.info("Starting Fibonacci AI bot...")
    await init_db()

    # Playwright/LLM/S3 больше не живут в этом процессе — только очередь.
    # Реальная генерация происходит в worker.py (`arq worker.WorkerSettings`),
    # который нужно запускать отдельным процессом рядом с ботом.
    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    logger.info("ARQ pool connected")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
        )
    finally:
        await arq_pool.aclose()
        await dp.storage.close()
        await close_uploads()
        await close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
