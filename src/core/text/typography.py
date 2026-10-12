"""Типографика текста слайдов (замечание владельца, сессия 5; D-071).

Неразрывность — число не отрывается от своего диапазона, единицы и знака процента:
- «60–90 секунд», «92–98 %» — между числами диапазона word joiner (U+2060), между числом и
  единицей — неразрывный пробел (U+00A0);
- разряды «1 250 000» — неразрывные пробелы;
- «№ 5», «$ 40», «+18 %», «−6,2 %» — тоже одной строкой.

Формат чисел по языку колоды: в ru, kk, uz десятичная запятая — «98.79» → «98,79» (кроме
версий, дат, адресов: «Python 3.11», «12.05.2026», «v2.0»).

fitter измеряет текст с этими знаками (core.fitting.metrics), рендеры их не рвут — сверено на
LibreOffice; word joiner не рисуется.
"""

import re

WJ = "⁠"
NBSP = " "
COMMA_DECIMAL = {"ru", "kk", "uz"}

# Единицы после числа (ru, kk, uz, en): проценты, валюты, счёт, время, меры
_UNITS = (
    r"%|‰|₽|\$|€|£|¥|₸|so['ʻ’]?m|сум|руб\.?|рубл\w*|тенге|тг\.?|долл\.?|доллар\w*|евро|юан\w*|"
    r"млн|млрд|трлн|тыс\.?|mln|bn|k|mln\.?|"
    r"шт\.?|чел\.?|человек\w*|сотрудник\w*|пациент\w*|раз(?:а)?|п\.\s?п\.|п\.п\.|pp|"
    r"сек(?:\.|унд\w*)?|с\.?|мин(?:\.|ут\w*)?|ч\.?|час\w*|дн\.?|дн(?:ей|я)|сут\w*|недел\w*|мес\.?|месяц\w*|"
    r"лет|год\w*|г\.|гг\.|кв\.?|квартал\w*|"
    r"мм|см|м|км|кг|г|т|мл|л|га|м²|м2|км²|°C|°|кВт|МВт|ГВт|Вт|кВт·ч|ГБ|ТБ|МБ|Гб|Мб|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|people|users|patients|times|"
    r"soniya|daqiqa|soat|kun|oy|yil|ta|kishi|"
    r"секунд|минут|сағат|күн|ай|жыл|адам"
)
_NUM = r"[+\-−–]?\d[\d  ]*(?:[.,]\d+)?"
_RANGE_RE = re.compile(r"(?<![\w.,+\-–])(\d+(?:[.,]\d+)?)\s?([–—-])\s?(\d+(?:[.,]\d+)?)(?![\w.,]*\d[.:]\d)(?![\-–]\d)")
_UNIT_RE = re.compile(r"(\d(?:[\d" + WJ + r"–—\-.,]*\d)?)[ \t](" + _UNITS + r")(?=$|[\s.,;:!?)»\"'%])", re.I)
_PREFIX_RE = re.compile(r"(№|\$|€|£|₽|₸|[+−]|~|≈|<|>|≤|≥)[ \t](?=\d)")
_THOUSANDS_RE = re.compile(r"(?<![\d.,])(\d{1,3})((?:[  ]\d{3})+)(?![\d])")
_DECIMAL_RE = re.compile(r"(?<![\w.,/:])(\d+)\.(\d+)(?![\w.,/:]*\d)")
_LATIN_BEFORE_RE = re.compile(r"(?:[A-Za-z][\w\-]*|v|V|версия|version|versiya)\s*$")


def _thousands(text: str) -> str:
    return _THOUSANDS_RE.sub(lambda m: m.group(1) + m.group(2).replace(" ", NBSP), text)


def _ranges(text: str) -> str:
    def repl(m: re.Match) -> str:
        a, dash, b = m.group(1), m.group(2), m.group(3)
        if dash == "-" and (len(a) > 4 or len(b) > 4):
            return m.group(0)                      # не диапазон: телефон, артикул
        return f"{a}{WJ}{'–' if dash == '-' else dash}{WJ}{b}"
    return _RANGE_RE.sub(repl, text)


def _decimal(text: str) -> str:
    def repl(m: re.Match) -> str:
        if _LATIN_BEFORE_RE.search(text[:m.start()]):
            return m.group(0)                      # «Python 3.11», «GPT-4.5», «v2.0»
        return f"{m.group(1)},{m.group(2)}"
    return _DECIMAL_RE.sub(repl, text)


def typeset(text: str, language: str = "ru") -> str:
    """Неразрывные числа, диапазоны и единицы; десятичная запятая по языку."""
    if not text or not any(ch.isdigit() for ch in text):
        return text
    if language in COMMA_DECIMAL:
        text = _decimal(text)
    text = _thousands(text)
    text = _ranges(text)
    text = _UNIT_RE.sub(lambda m: f"{m.group(1)}{NBSP}{m.group(2)}", text)
    text = _PREFIX_RE.sub(lambda m: f"{m.group(1)}{NBSP}", text)
    return text


def plain(text: str) -> str:
    """Текст без служебных знаков типографики — для сравнения с источником и проверок."""
    return text.replace(WJ, "").replace(NBSP, " ").replace(" ", " ")


SKIP_KEYS = {"icon", "status", "tag", "source_ref", "polarity", "row"}


def typeset_value(value, language: str):
    """typeset для строк в произвольной структуре content (кроме служебных полей)."""
    if isinstance(value, str):
        return typeset(value, language)
    if isinstance(value, list):
        return [typeset_value(v, language) for v in value]
    if isinstance(value, dict):
        return {k: (v if k in SKIP_KEYS else typeset_value(v, language)) for k, v in value.items()}
    return value
