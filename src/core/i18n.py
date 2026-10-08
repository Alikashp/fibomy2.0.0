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

# Заметки последнего слайда, если в колоде есть ИИ-картинки (сессия 4)
IMAGES_AI = {
    "ru": "Изображения сгенерированы ИИ",
    "en": "Images are AI-generated",
    "uz": "Rasmlar sun'iy intellekt tomonidan yaratilgan",
    "kk": "Суреттерді жасанды интеллект жасаған",
}
