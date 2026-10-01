"""Тексты, которые ставит код, а не модель, на языках колоды (ru, en, uz, kk)."""

LANG_TAGS = {"ru": "ru-RU", "en": "en-US", "uz": "uz-Latn-UZ", "kk": "kk-KZ"}

THANKS = {
    "ru": "Спасибо за внимание",
    "en": "Thank you",
    "uz": "E'tiboringiz uchun rahmat",
    "kk": "Назарларыңызға рахмет",
}

# Сноска у слайдов с числами «по теме» (ТЗ 3.3.3, D-017, D-025)
FOOTNOTE_ESTIMATE = {
    "ru": "Оценочные данные — проверьте перед показом",
    "en": "Estimated figures — verify before presenting",
    "uz": "Taxminiy ma'lumotlar — namoyishdan oldin tekshiring",
    "kk": "Болжамды деректер — көрсетер алдында тексеріңіз",
}


def text(table: dict[str, str], language: str) -> str:
    return table.get(language) or table["ru"]


def lang_tag(language: str) -> str:
    return LANG_TAGS.get(language, "ru-RU")

# Сноска у слайдов с числами по материалу пользователя (ставит код)
FOOTNOTE_SOURCE_FILE = {
    "ru": "по данным: {name}",
    "en": "Source: {name}",
    "uz": "Manba: {name}",
    "kk": "Дереккөз: {name}",
}
FOOTNOTE_SOURCE_TEXT = {
    "ru": "по данным: ваш текст",
    "en": "Source: your text",
    "uz": "Manba: sizning matningiz",
    "kk": "Дереккөз: сіздің мәтініңіз",
}
