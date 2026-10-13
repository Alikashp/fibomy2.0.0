"""Склейка текста, который Telegram разрезал на несколько сообщений (замечание владельца, сессия 5; D-075).

Telegram режет сообщение длиннее 4096 знаков на части, и они приходят подряд отдельными
сообщениями: из текста о 15 компаниях в колоду попали 5 — бот взял только первую часть.

На шагах темы и материала бот копит текстовые сообщения одного пользователя и обрабатывает их
одним текстом, когда после последнего прошло JOIN_WINDOW секунд. Короткое одиночное сообщение
(тема) обрабатывается сразу, без ожидания: окно открывает только длинная часть (≥ SPLIT_HINT
знаков — Telegram режет у 4096), а пока окно открыто, к нему добавляется всё, что пришло подряд.

Буфер — в памяти процесса бота: бот один, апдейты aiogram обрабатывает параллельно, поэтому
ожидание одного сообщения не задерживает следующее.
"""

import asyncio
import time
from dataclasses import dataclass, field

JOIN_WINDOW = 2.5          # секунд после последней части (окно 2–3 с — требование владельца)
SPLIT_HINT = 3000          # часть такой длины — вероятно, не последняя
SEPARATOR = "\n"


@dataclass
class _Buffer:
    parts: list[str] = field(default_factory=list)
    last: float = 0.0


class TextJoiner:
    def __init__(self, window: float = JOIN_WINDOW, split_hint: int = SPLIT_HINT):
        self.window = window
        self.split_hint = split_hint
        self._buffers: dict[tuple, _Buffer] = {}

    def pending(self, key: tuple) -> bool:
        return key in self._buffers

    async def feed(self, key: tuple, text: str) -> tuple[str, int] | None:
        """Часть текста пользователя key. → (весь текст, число частей), когда пора обрабатывать;
        None — текст забрал другой вызов (пришла следующая часть)."""
        if key not in self._buffers and len(text) < self.split_hint:
            return text, 1
        buf = self._buffers.setdefault(key, _Buffer())
        buf.parts.append(text)
        mine = len(buf.parts)
        buf.last = time.monotonic()
        while True:
            await asyncio.sleep(max(0.0, buf.last + self.window - time.monotonic()))
            if self._buffers.get(key) is not buf or len(buf.parts) != mine:
                return None                       # пришла следующая часть — её вызов и закончит
            if time.monotonic() >= buf.last + self.window - 0.01:
                break
        del self._buffers[key]
        return SEPARATOR.join(buf.parts), len(buf.parts)


joiner = TextJoiner()
