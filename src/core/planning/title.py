"""Тема на титуле без пожеланий (замечание владельца, сессия 5; D-076; G-05).

Пользователь часто пишет в теме пожелания к презентации: «Управление требованиями в ит-стартапах
со статистикой аналитикой и тж» → титул «Управление требованиями в ИТ-стартапах», а «со
статистикой», «подробно», «для защиты» — в план. OUTLINE возвращает deck.title_topic (тема
словами пользователя, без пожеланий, с исправленным регистром) и deck.user_wishes.

Код не доверяет title_topic вслепую: каждое слово должно быть взято из темы пользователя (без
учёта регистра, ё/е и опечаток в одну-две буквы), порядок — как в теме. Иначе на титул идёт
исходная тема с исправленным регистром: заглавная первая буква, сокращения («ИТ», «ИИ», «РФ»),
имена собственные — как их написала модель в title_topic или подзаголовке («России»).
"""

import re

_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)
# Сокращения, которые пользователи пишут строчными: ит-стартап → ИТ-стартап
ABBREVIATIONS = {
    "ит": "ИТ", "ии": "ИИ", "рф": "РФ", "сша": "США", "ес": "ЕС", "ооо": "ООО", "ип": "ИП", "оао": "ОАО",
    "пао": "ПАО", "смб": "СМБ", "мсб": "МСБ", "вуз": "вуз", "сво": "СВО", "жкх": "ЖКХ", "мвд": "МВД",
    "ит-": "ИТ-", "it": "IT", "ai": "AI", "ml": "ML", "hr": "HR", "crm": "CRM", "erp": "ERP", "api": "API",
    "kpi": "KPI", "b2b": "B2B", "b2c": "B2C", "saas": "SaaS", "ui": "UI", "ux": "UX", "seo": "SEO", "smm": "SMM",
    "mvp": "MVP", "qa": "QA", "devops": "DevOps", "esg": "ESG", "vr": "VR", "ar": "AR", "iot": "IoT",
}


def _key(word: str) -> str:
    return word.lower().replace("ё", "е")


def _close(a: str, b: str) -> bool:
    """Слова совпадают с точностью до опечатки: 1 правка до 7 букв, 2 — длиннее."""
    if a == b:
        return True
    limit = 1 if max(len(a), len(b)) < 8 else 2
    if abs(len(a) - len(b)) > limit or min(len(a), len(b)) < 4:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= limit


def words_from_topic(title: str, topic: str) -> bool:
    """Все слова title — из topic, в том же порядке (пропускать слова темы можно)."""
    t_words = [_key(w) for w in _WORD_RE.findall(topic)]
    pos = 0
    for w in (_key(x) for x in _WORD_RE.findall(title)):
        while pos < len(t_words) and not _close(w, t_words[pos]):
            pos += 1
        if pos == len(t_words):
            return False
        pos += 1
    return True


def _case_hints(*texts: str) -> dict[str, str]:
    """Написание слов с заглавной или капсом из текстов модели: «россии» → «России»."""
    hints = {}
    for text in texts:
        for i, w in enumerate(_WORD_RE.findall(text or "")):
            if w != w.lower() and (i > 0 or w.isupper()):
                hints.setdefault(_key(w), w)
    return hints


def fix_case(topic: str, *hints: str) -> str:
    """Исходная тема: заглавная первая буква, сокращения капсом, имена — как у модели."""
    known = _case_hints(*hints)

    def one(m: re.Match) -> str:
        word = m.group(0)
        if word != word.lower():
            return word                                     # пользователь сам написал с заглавной
        if _key(word) in known:
            return known[_key(word)]
        parts = word.split("-")
        fixed = [ABBREVIATIONS.get(p.lower(), p) for p in parts]
        return "-".join(fixed)

    text = _WORD_RE.sub(one, topic.strip())
    return text[:1].upper() + text[1:]


def deck_title(topic: str, title_topic: str | None = None, subtitle: str | None = None) -> tuple[str, str | None]:
    """→ (заголовок титула, причина замены или None). title_topic — от OUTLINE."""
    topic = re.sub(r"\s+", " ", topic.strip())
    candidate = re.sub(r"\s+", " ", (title_topic or "").strip()).strip(" .,;:")
    if candidate and len(candidate) >= 3 and words_from_topic(candidate, topic):
        title = fix_case(candidate, candidate)
        return title, None if _key(title) == _key(topic) else "title_topic"
    reason = "title_topic_rejected" if candidate else None
    return fix_case(topic, candidate, subtitle or ""), reason
