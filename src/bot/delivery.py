"""Прогресс генерации и выдача PPTX + PDF в Telegram (02_CJM.md, 1.2, шаги 7–8).

Вызывается из воркера: бот и воркер — разные процессы, файлы отправляет воркер
через Bot API (03_ARCHITECTURE.md, 3.2).
"""

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument

logger = logging.getLogger(__name__)

PROGRESS_MIN_INTERVAL = 3.0   # правка статусного сообщения не чаще раза в 3 с (кроме смены этапа)
SEND_ATTEMPTS = 3

STAGE_TEXT = {
    "ingest": "📄 Читаю материал",
    "outline": "🧭 Составляю план",
    "content": "✍️ Пишу слайды",
    "render": "🎨 Собираю PPTX",
    "convert": "📑 Делаю PDF",
}

# Код для поддержки — короткий хвост номера колоды (полный dk_… — в логах): пользователю
# внутренний номер не показываем (замечание владельца 01.10.2026)
ERROR_TEXT = {
    "OUTLINE_FAILED": "❌ Не удалось составить план презентации. Попробуйте ещё раз: /new",
    "RENDER_FAILED": "❌ Что-то пошло не так при сборке файла. Мы уже разбираемся. Код для поддержки: {code}",
    "BAD_REQUEST": "❌ Не получилось разобрать параметры. Начните заново: /new",
    "SCAN_WITHOUT_TEXT": ("❌ В PDF нет текста — похоже на скан. Пришлите файл с текстом или вставьте текст "
                          "сообщением: /new"),
    "BAD_FILE": "❌ Не получилось прочитать файл. Сохраните его заново или пришлите текст сообщением: /new",
    "PARSE_TIMEOUT": "❌ Файл читается слишком долго. Пришлите файл поменьше или текст сообщением: /new",
    "FILE_UNSUPPORTED": "❌ Такой формат не поддерживается. Пришлите .pdf, .docx, .pptx или .txt: /new",
    "UPLOAD_EXPIRED": "❌ Файл устарел — пришлите его ещё раз через /new",
}
DEFAULT_ERROR = "❌ Что-то пошло не так. Попробуйте ещё раз через /new. Код для поддержки: {code}"


def support_code(deck_id: str) -> str:
    return deck_id[-6:]


def error_text(code: str | None, deck_id: str) -> str:
    return ERROR_TEXT.get(code or "", DEFAULT_ERROR).format(code=support_code(deck_id))


def kb_after_delivery() -> InlineKeyboardMarkup:
    # «🔁 Повторить с другими параметрами» — сессия 4 (docs/design/09_PLAN.md)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новая презентация", callback_data="action:new")],
    ])


class StatusMessage:
    """Правит одно статусное сообщение по этапам генерации."""

    def __init__(self, bot: Bot, chat_id: int | None, message_id: int | None, deck_id: str):
        self.bot, self.chat_id, self.message_id, self.deck_id = bot, chat_id, message_id, deck_id
        self._last_text = None
        self._last_stage = None
        self._last_at = 0.0

    def text_for(self, stage: str, done: int | None = None, total: int | None = None) -> str:
        line = STAGE_TEXT.get(stage, "⏳ Работаю")
        if stage == "content" and total:
            line += f": {done or 0} из {total}"
        return f"{line}…"

    async def __call__(self, stage: str, done: int | None = None, total: int | None = None) -> None:
        if self.chat_id is None or self.message_id is None:
            return
        text = self.text_for(stage, done, total)
        now = time.monotonic()
        if text == self._last_text:
            return
        if stage == self._last_stage and now - self._last_at < PROGRESS_MIN_INTERVAL:
            return
        self._last_text, self._last_stage, self._last_at = text, stage, now
        await self.show(text)

    async def show(self, text: str) -> None:
        try:
            await self.bot.edit_message_text(text, chat_id=self.chat_id, message_id=self.message_id,
                                             parse_mode="HTML")
        except Exception:
            logger.debug("Could not edit status message")

    async def delete(self) -> None:
        if self.chat_id is None or self.message_id is None:
            return
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except Exception:
            pass


def safe_filename(title: str, ext: str) -> str:
    stem = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip()[:40] or "presentation"
    return f"{stem}.{ext}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def material_notes(warnings: list[str]) -> list[str]:
    """Предупреждения колоды → строки подписи (02_CJM.md, 1.2, шаг 8)."""
    notes = []
    for w in warnings:
        kind, _, value = w.partition(":")
        if kind == "slides_short":
            got, wanted = value.split("/")
            notes.append(f"В материале хватило на {got} слайдов из {wanted} — добавьте текст, если нужно больше.")
        elif kind == "truncated":
            used, total = value.split("/")
            notes.append(f"Вошло начало документа: {int(used):,} знаков из {int(total):,}.".replace(",", " "))
    return notes


def caption(title: str, slides: int, theme_name: str, watermark: bool, pdf_missing: bool,
            notes: list[str] | None = None) -> str:
    text = f"✨ <b>{_escape(title)}</b>\n{slides} слайдов · {theme_name}\nPPTX — редактируемый файл, PDF — для показа."
    for note in notes or []:
        text += f"\n\n{note}"
    if pdf_missing:
        text += "\n\nPDF не получился. PPTX — полноценный файл: его можно сохранить в PDF из PowerPoint."
    if watermark:
        text += "\n\n<i>Бесплатная версия: водяной знак в PDF · /plan</i>"
    return text


async def send_files(bot: Bot, chat_id: int, title: str, pptx: bytes, pdf: bytes | None, text: str) -> None:
    """PPTX + PDF одним альбомом, подпись — у последнего файла; 3 попытки."""
    media = [InputMediaDocument(media=BufferedInputFile(pptx, filename=safe_filename(title, "pptx")))]
    if pdf:
        media.append(InputMediaDocument(media=BufferedInputFile(pdf, filename=safe_filename(title, "pdf"))))
    media[-1].caption, media[-1].parse_mode = text, "HTML"
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            if len(media) == 1:
                await bot.send_document(chat_id, document=media[0].media, caption=text, parse_mode="HTML")
            else:
                await bot.send_media_group(chat_id, media=media)
            break
        except Exception:
            if attempt == SEND_ATTEMPTS:
                raise
            logger.warning("Send failed, retrying", extra={"attempt": attempt})
            await asyncio.sleep(2 * attempt)
    await bot.send_message(chat_id, "Что дальше?", reply_markup=kb_after_delivery())
