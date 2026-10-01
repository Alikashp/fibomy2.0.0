"""Ошибки API: единый формат {"error": {"code", "message"}} (04_CONTRACTS.md, 7.3).

Коды — часть контракта: второй бот ветвится по ним. Новые коды добавлять можно,
переименовывать и удалять — нет (CLAUDE.md, правило обратной совместимости).
"""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


# Ошибки колоды (в статусе, а не HTTP-кодом): код воркера → текст для разработчика и
# пользователя второго бота. Неизвестный код — INTERNAL.
DECK_ERROR_TEXT = {
    "SCAN_WITHOUT_TEXT": "В PDF нет текстового слоя — похоже на скан. Пришлите файл с текстом "
                         "или передайте текст в input.text.",
    "BAD_FILE": "Не удалось прочитать файл: он повреждён или сохранён в неподдерживаемом варианте формата.",
    "PARSE_TIMEOUT": "Файл читается слишком долго (больше 15 секунд). Пришлите файл поменьше или текст.",
    "FILE_UNSUPPORTED": "Формат файла не поддерживается: нужен .pdf, .docx, .pptx или .txt.",
    "UPLOAD_EXPIRED": "Колода ждала в очереди дольше часа, и материал удалён. Создайте колоду заново.",
    "OUTLINE_FAILED": "Не удалось составить план презентации. Повторите запрос; если ошибка повторяется — "
                      "сообщите id колоды.",
    "RENDER_FAILED": "Не удалось собрать файл презентации. Сообщите id колоды.",
    "STORAGE_FAILED": "Колода собрана, но файлы не удалось сохранить. Повторите запрос.",
    "DEADLINE_EXCEEDED": "Колода не собралась за отведённое время. Повторите запрос.",
    "INTERNAL": "Внутренняя ошибка. Повторите запрос; если ошибка повторяется — сообщите id колоды.",
}


def deck_error(code: str | None) -> dict:
    code = code if code in DECK_ERROR_TEXT else "INTERNAL"
    return {"code": code, "message": DECK_ERROR_TEXT[code]}


# Предупреждения колоды (decks.warnings, строки воркера) → {"code", "message"}
def deck_warning(raw: str) -> dict:
    kind, _, value = raw.partition(":")
    if kind == "slides_short" and "/" in value:
        got, wanted = value.split("/", 1)
        return {"code": "SLIDES_SHORT",
                "message": f"В материале хватило на {got} слайдов из {wanted}. Добавьте текст, если нужно больше."}
    if kind == "truncated" and "/" in value:
        used, total = value.split("/", 1)
        try:
            used, total = f"{int(used):,}".replace(",", " "), f"{int(total):,}".replace(",", " ")
        except ValueError:
            pass
        return {"code": "MATERIAL_TRUNCATED", "message": f"Вошло начало материала: {used} знаков из {total}."}
    if kind == "pdf_failed":
        return {"code": "PDF_FAILED",
                "message": "PDF не получился. PPTX — полноценный файл, его можно сохранить в PDF из PowerPoint."}
    return {"code": kind.upper() or "WARNING", "message": raw}
