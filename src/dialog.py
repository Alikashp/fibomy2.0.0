"""
Короткий диалог создания презентации: сводка параметров и экраны «Сменить…».

Флоу (main.py): тип → тема или текст → материал (доклад) / бриф (питч-дек) →
сводка. В сводке видны все параметры, каждый меняется кнопкой, после выбора —
возврат к сводке в том же сообщении. Значения по умолчанию — последний выбор
пользователя из профиля (users.last_*, source_mode), иначе дефолты ниже.

Здесь только чистые функции: тексты, клавиатуры, дефолты. Обработчики aiogram,
FSM и БД — в main.py.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ── Параметры и подписи ──────────────────────────────────────────────────────

TYPE_LABELS = {
    "pitch_deck":  "Питч-дек",
    "diploma":     "Диплом",
    "corp_report": "Корпоративный отчёт",
    "educational": "Обучающая",
    "sales":       "Продажная",
    "conference":  "Конференция",
    "roadmap":     "Роадмап",
    "doklad":      "Доклад",
}
TYPE_BUTTONS = {"pitch_deck": "🚀 Питч-дек", "doklad": "🎤 Доклад"}

LANGUAGES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "uz": "🇺🇿 O'zbek", "kk": "🇰🇿 Қазақша"}
DEFAULT_LANGUAGE = "ru"

AUDIENCES = {
    "general":    "🌍 Общая",
    "students":   "🎓 Студенты",
    "colleagues": "👥 Коллеги",
    "management": "👔 Руководство",
    "clients":    "🤝 Клиенты",
    "investors":  "💰 Инвесторы",
}
DEFAULT_AUDIENCE = "general"

# Темы оформления нового движка (themes/<id>.yaml, D-037). В фазе 1 все темы всем (D-040).
# В данных сводки и в профиле (users.last_color_scheme) хранится id темы.
THEMES = {
    "graphite_light": "☀️ Графит светлая",
    "graphite_dark":  "🌙 Графит тёмная",
    "azure_coral":    "🌊 Лазурь",
    "fresh_green":    "🌿 Свежая зелёная",
}
DEFAULT_THEME = "graphite_light"
# Цветовые схемы старого движка (до 01.10.2026) → темы (04_CONTRACTS.md, 8.2)
THEME_BY_LEGACY_SCHEME = {"light": "graphite_light", "dark": "graphite_dark", "forest": "fresh_green",
                          "ember": "azure_coral"}
# Питч-дек до сессии 5 (D-054) рисует старый движок: тема → его цветовая схема
LEGACY_SCHEME_BY_THEME = {"graphite_light": "light", "graphite_dark": "dark", "azure_coral": "light",
                          "fresh_green": "forest"}

# Число слайдов доклада. Границы — UserRequest.slide_count_hint (5–20).
DOKLAD_SLIDE_COUNTS = (5, 7, 9, 12, 15)
DOKLAD_DEFAULT_SLIDE_COUNT = 9
PITCH_SLIDE_COUNT = 11  # у питч-дека структура из 11 слайдов, число не выбирается

SOURCE_MODES = {
    "strict": "📎 Только мой материал",
    "extend": "🧠 Дополнить общими знаниями",
}
DEFAULT_SOURCE_MODE = "strict"

# Сообщение длиннее — это не тема, а текст для презентации (вопрос 8, D-039;
# DeckRequest.input.topic ≤ 200)
TOPIC_MAX_CHARS = 200
MATERIAL_MAX_CHARS = 30000  # ТЗ 3.1: текст в сообщении — до 30 000 знаков


def has_material(data: dict) -> bool:
    return data.get("source_type") in ("text", "document")


# ── Движок (D-038) ───────────────────────────────────────────────────────────
# Доклад — по теме и по материалу — новый движок (PPTX + PDF). Питч-дек — старый
# движок до сессии 5, где появляется его сюжет (docs/design/08_MIGRATION.md, 2.1; D-054).

def engine_for(data: dict) -> str:
    """«new» — колода нового движка (PPTX + PDF), «old» — старый HTML → PDF."""
    return "new" if data.get("presentation_type", "doklad") == "doklad" else "old"


def theme_for(value: str | None, enabled: list[str] | tuple[str, ...] | None = None) -> str:
    """id темы из сводки или профиля; старые схемы переводятся, неизвестное — тема по умолчанию."""
    theme = value if value in THEMES else THEME_BY_LEGACY_SCHEME.get(value or "", DEFAULT_THEME)
    return theme if enabled is None or theme in enabled else DEFAULT_THEME


def theme_allowed(theme: str, plan: str) -> bool:
    return theme in THEMES  # фаза 1: все темы всем (вопрос 12, D-040)


# ── Значения по умолчанию ────────────────────────────────────────────────────

def telegram_language(language_code: str | None) -> str | None:
    """«en-US» → «en», если язык поддерживается."""
    code = (language_code or "").split("-")[0].lower()
    return code if code in LANGUAGES else None


def default_params(profile: dict, telegram_lang: str | None, plan: str) -> dict:
    """Параметры сводки по умолчанию: прошлый выбор из профиля, иначе дефолты.

    profile — {"last_language", "last_slide_count", "last_audience",
    "last_color_scheme", "source_mode"}; ключей может не быть.
    """
    lang = profile.get("last_language")
    slides = profile.get("last_slide_count")
    audience = profile.get("last_audience")
    scheme = profile.get("last_color_scheme")
    mode = profile.get("source_mode")
    return {
        "language": lang if lang in LANGUAGES else (telegram_language(telegram_lang) or DEFAULT_LANGUAGE),
        "slide_count": slides if slides in DOKLAD_SLIDE_COUNTS else DOKLAD_DEFAULT_SLIDE_COUNT,
        "audience": audience if audience in AUDIENCES else DEFAULT_AUDIENCE,
        # Платная схема у пользователя, который вернулся на бесплатный тариф, — не подставляем
        "color_scheme": theme_for(scheme),
        "source_mode": mode if mode in SOURCE_MODES else DEFAULT_SOURCE_MODE,
    }


def last_choices(data: dict) -> dict:
    """Что сохранить в профиль после генерации (поля users)."""
    out = {
        "last_language": data.get("language"),
        "last_audience": data.get("audience"),
        "last_color_scheme": data.get("color_scheme"),
    }
    if data.get("presentation_type") == "doklad":
        out["last_slide_count"] = data.get("slide_count")
    if has_material(data) and data.get("presentation_type") == "doklad":
        out["source_mode"] = data.get("source_mode")
    return out


# ── Тема из длинного текста ──────────────────────────────────────────────────

def topic_from_text(text: str, limit: int = 100) -> str:
    """Тема для колоды из присланного текста: первая непустая строка, до limit символов."""
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if len(line) > limit:
        cut = line[:limit].rsplit(" ", 1)[0]
        line = (cut or line[:limit]).rstrip(" ,.;:—-") + "…"
    return line if len(line) >= 3 else "Презентация по вашему тексту"


def topic_from_file_name(name: str | None) -> str:
    stem = (name or "").rsplit(".", 1)[0].replace("_", " ").strip()
    return stem[:100] if len(stem) >= 3 else "Презентация по вашему документу"


# ── Сводка ───────────────────────────────────────────────────────────────────

def _short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _material_line(data: dict) -> str:
    if data.get("source_type") == "document":
        return f"файл «{_short(data.get('source_name') or 'документ', 40)}»"
    if data.get("source_type") == "text":
        return f"ваш текст, {len(data.get('raw_text') or ''):,} симв.".replace(",", " ")
    return "нет — соберём по теме"


def summary_text(data: dict, plan: str) -> str:
    ptype = data.get("presentation_type", "doklad")
    lines = ["📋 <b>Проверьте параметры</b>", ""]
    lines.append(f"📝 Тема: <b>{_escape(_short(data.get('topic', '')))}</b>")
    lines.append(f"📂 Тип: <b>{TYPE_LABELS.get(ptype, ptype)}</b>")
    if ptype == "doklad":
        lines.append(f"📎 Материал: <b>{_escape(_material_line(data))}</b>")
        if has_material(data):
            lines.append(f"📚 Режим: <b>{SOURCE_MODES[data.get('source_mode', DEFAULT_SOURCE_MODE)]}</b>")
    elif ptype == "pitch_deck":
        lines.append(f"🗒 Бриф: <b>{'добавлен ✓' if data.get('brief') else 'нет'}</b>")
    lines.append(f"🌐 Язык: <b>{LANGUAGES.get(data.get('language'), data.get('language'))}</b>")
    if ptype == "doklad":
        lines.append(f"🔢 Слайдов: <b>{data.get('slide_count')}</b> (вместе с титульным и финальным)")
    else:
        lines.append(f"🔢 Слайдов: <b>{PITCH_SLIDE_COUNT}</b> (структура питч-дека)")
    lines.append(f"👥 Аудитория: <b>{AUDIENCES.get(data.get('audience'), data.get('audience'))}</b>")
    lines.append(f"🎨 Дизайн: <b>{THEMES[theme_for(data.get('color_scheme'))]}</b>")
    lines.append("📦 Файлы: <b>PPTX + PDF</b>" if engine_for(data) == "new" else "📦 Файлы: <b>PDF</b>")
    if has_material(data) and ptype == "doklad" and data.get("source_mode") == "strict":
        lines += ["", "<i>Если материала мало, слайдов будет меньше выбранного — бот скажет, сколько вышло.</i>"]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def summary_keyboard(data: dict) -> InlineKeyboardMarkup:
    ptype = data.get("presentation_type", "doklad")
    rows = [
        [_btn("✏️ Сменить тему", "sum:topic"), _btn("📂 Сменить тип", "sum:type")],
    ]
    if ptype == "doklad":
        row = [_btn("📎 Сменить материал", "sum:material")]
        if has_material(data):
            row.append(_btn("📚 Сменить режим", "sum:mode"))
        rows.append(row)
    elif ptype == "pitch_deck":
        rows.append([_btn("🗒 Сменить бриф", "sum:brief")])
    lang_row = [_btn("🌐 Сменить язык", "sum:lang")]
    if ptype == "doklad":
        lang_row.append(_btn("🔢 Сменить число слайдов", "sum:slides"))
    rows.append(lang_row)
    rows.append([_btn("👥 Сменить аудиторию", "sum:aud"), _btn("🎨 Сменить дизайн", "sum:design")])
    rows.append([_btn("✅ Сгенерировать презентацию", "sum:go")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Экраны «Сменить…» ────────────────────────────────────────────────────────

BACK_TO_SUMMARY = _btn("⬅️ К сводке", "sum:back")


def _mark(label: str, selected: bool) -> str:
    return f"✓ {label}" if selected else label


def change_screen(param: str, data: dict, plan: str) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура экрана выбора параметра. Выбор — callback set:<param>:<value>."""
    if param == "lang":
        buttons = [_btn(_mark(l, data.get("language") == k), f"set:language:{k}") for k, l in LANGUAGES.items()]
        return "🌐 <b>На каком языке сделать презентацию?</b>", _grid(buttons, 2)
    if param == "slides":
        buttons = [_btn(_mark(str(n), data.get("slide_count") == n), f"set:slide_count:{n}")
                   for n in DOKLAD_SLIDE_COUNTS]
        return ("🔢 <b>Сколько слайдов сделать?</b>\n\n<i>Вместе с титульным и финальным.</i>",
                _grid(buttons, len(buttons)))
    if param == "aud":
        buttons = [_btn(_mark(l, data.get("audience") == k), f"set:audience:{k}") for k, l in AUDIENCES.items()]
        return "👥 <b>Кто будет смотреть презентацию?</b>", _grid(buttons, 2)
    if param == "mode":
        buttons = [_btn(_mark(l, data.get("source_mode") == k), f"set:source_mode:{k}") for k, l in SOURCE_MODES.items()]
        return ("📚 <b>Как работать с вашим материалом?</b>\n\n"
                "<i>📎 Только мой материал — все факты и числа из вашего текста. "
                "Если материала мало, слайдов будет меньше выбранного.\n"
                "🧠 Дополнить общими знаниями — добавим пояснения и контекст, "
                "но числа, даты и источники — только из вашего материала.</i>"), _grid(buttons, 1)
    if param == "design":
        current = theme_for(data.get("color_scheme"))
        buttons = [_btn(_mark(label, current == key), f"set:color_scheme:{key}") for key, label in THEMES.items()]
        return ("🎨 <b>Выберите дизайн</b>\n\n<i>Графит — сдержанные светлая и тёмная, Лазурь и Свежая зелёная — "
                "яркие. Все темы доступны бесплатно.</i>"), _grid(buttons, 2)
    if param == "type":
        buttons = [_btn(_mark(l, data.get("presentation_type") == k), f"set:presentation_type:{k}")
                   for k, l in TYPE_BUTTONS.items()]
        return "📂 <b>Тип презентации</b>", _grid(buttons, 2)
    raise ValueError(f"unknown param {param}")


def _grid(buttons: list[InlineKeyboardButton], per_row: int) -> InlineKeyboardMarkup:
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    rows.append([BACK_TO_SUMMARY])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Допустимые значения для set:<param>:<value> — всё прочее игнорируется
SETTABLE = {
    "language": set(LANGUAGES),
    "slide_count": {str(n) for n in DOKLAD_SLIDE_COUNTS},
    "audience": set(AUDIENCES),
    "source_mode": set(SOURCE_MODES),
    "color_scheme": set(THEMES),
    "presentation_type": set(TYPE_BUTTONS),
}
