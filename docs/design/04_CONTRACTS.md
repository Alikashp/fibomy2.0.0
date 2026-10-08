# 04. Контракты данных

**Статус:** утверждено 01.10.2026 · **Дата:** 30.09.2026, правка 01.10.2026 (тема до 200 знаков, четыре темы, поля бота в `client`, формат LayoutSpec; раздел 7 и 8.3 — API реализован в сессии 3, D-055…D-059) · **Опирается на:** `01_SCOPE.md`, `02_CJM.md`, `03_ARCHITECTURE.md`, ТЗ 3.10, 4.3, 4.8

Все контракты в коде — модели pydantic в `core/models/`. JSON Schema ниже — их описание: из моделей она генерируется автоматически (для structured outputs и тестов), здесь приведена для утверждения. Схемы — draft 2020-12, сокращены до значимых полей.

---

## 1. Соглашения

- **Идентификаторы:** колода `dk_<ULID>`, слайд `s01…s20` (стабилен при правках), фрагмент источника `f<N>`, набор данных `ds<N>`, колонка `c<N>`, строка `r<N>`, ячейка `ds2:r1c4`, картинка `img_<hash8>`.
- **Версии:**
  - `schema_version` (целое) у `DeckRequest`, `SourceDigest`, `DeckSpec` — версия структуры. При чтении старой версии код поднимает её функцией миграции (`core/models/upgrade.py`), в БД ничего не переписывается;
  - `meta.versions` — версии движка, промптов (`prompts/v2/VERSION`), набора макетов и тем. Нужны для разбора жалоб и воспроизведения;
  - `revision` — номер ревизии колоды (правки фазы 4).
- **Схемы ответов LLM** — strict: `additionalProperties: false`, все поля в `required`, необязательное — через `"type": ["string", "null"]`. Длины и число элементов подставляет код из LayoutSpec. Если провайдер не поддерживает `maxLength` в strict-режиме, ограничения остаются в тексте промпта, а соблюдение проверяет fitter (вопрос 6).
- **Числа.** В контенте число хранится строкой так, как написано на слайде (`"47,3"`), с разобранным значением для проверок в `DeckSpec` (`value_num`) и адресом источника `source_ref`.

---

## 2. DeckRequest — параметры колоды (`decks.request`)

```json
{
  "$id": "DeckRequest", "type": "object",
  "required": ["schema_version", "client", "presentation_type", "language", "audience", "theme_id", "input"],
  "properties": {
    "schema_version": {"const": 1},
    "client": {"type": "object", "properties": {
      "kind": {"enum": ["bot", "api"]},
      "user_id": {"type": ["integer", "null"]},
      "api_client_id": {"type": ["string", "null"]},
      "plan": {"enum": ["free", "starter", "pro", "team"]},
      "chat_id": {"type": ["integer", "null"], "description": "только бот: куда воркер отправляет файлы"},
      "status_message_id": {"type": ["integer", "null"], "description": "только бот: статусное сообщение, которое правит прогресс"}}},
    "presentation_type": {"enum": ["doklad", "pitch_deck"], "description": "фаза 2+: report, lecture, defense, general (ТЗ 3.2)"},
    "input": {"type": "object", "properties": {
      "topic": {"type": "string", "minLength": 3, "maxLength": 200, "description": "длиннее — текст-материал (D-039)"},
      "material": {"type": ["object", "null"], "properties": {
        "kind": {"enum": ["text", "document", "digest"]},
        "ref": {"type": "string", "description": "uploads/…, redis:… (D-008) или digests/{deck_id} при «Повторить»"},
        "mime": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]}}},
      "brief": {"type": ["string", "null"], "maxLength": 2000}}},
    "source_mode": {"enum": ["strict", "extend", null]},
    "language": {"enum": ["ru", "en", "uz", "kk"], "description": "фаза 2: 15 языков"},
    "audience": {"enum": ["general", "students", "colleagues", "management", "clients", "investors"]},
    "slides_count": {"type": ["integer", "null"], "minimum": 4, "maximum": 20, "description": "null — число задаёт сюжет (питч-дек, 11)"},
    "theme_id": {"enum": ["business_slate", "ember_dark", "sunny_cream", "mint_coral",
                          "graphite_light", "graphite_dark", "azure_coral", "fresh_green"],
                 "description": "сессия 4: старые id принимаются и переводятся на новые (D-063)"},
    "image_mode": {"enum": ["none", "web", "ai"], "default": "ai", "description": "web (Pexels) не используется (D-067)"},
    "author": {"type": ["object", "null"], "properties": {"name": {"type": ["string", "null"]}, "group": {"type": ["string", "null"]}}},
    "watermark": {"type": "boolean"},
    "seed": {"type": ["integer", "null"], "description": "null — код выбирает случайный и пишет в DeckSpec"},
    "parent_deck_id": {"type": ["string", "null"]},
    "webhook_url": {"type": ["string", "null"], "description": "фаза 3"}
  }
}
```

Текст пользователя не лежит в `request` — он, как и файл, передаётся ссылкой (`material.kind = text`, объект `uploads/`). В БД не остаётся исходного материала, только `SourceDigest` на 30 дней (вопрос 10).

---

## 3. SourceDigest — что извлечено из источника

Создаёт INGEST; поле `analysis` дописывает OUTLINE. По теме digest содержит только тему, `fragments` и `datasets` пусты.

```json
{
  "$id": "SourceDigest", "type": "object",
  "required": ["schema_version", "mode", "fragments", "datasets"],
  "properties": {
    "schema_version": {"const": 1},
    "mode": {"enum": ["topic", "material"]},
    "topic": {"type": "string"},
    "source": {"type": ["object", "null"], "properties": {
      "kind": {"enum": ["text", "pdf", "docx", "pptx", "txt"]},
      "name": {"type": ["string", "null"]},
      "pages": {"type": ["integer", "null"]},
      "chars_total": {"type": "integer"}, "chars_used": {"type": "integer"},
      "truncated": {"type": "boolean"}}},
    "fragments": {"type": "array", "items": {"type": "object",
      "required": ["id", "text"],
      "properties": {
        "id": {"type": "string", "pattern": "^f\\d+$"},
        "text": {"type": "string"},
        "kind": {"enum": ["heading", "paragraph", "list_item", "note"]},
        "loc": {"type": "object", "properties": {"page": {"type": "integer"}, "slide": {"type": "integer"}, "para": {"type": "integer"}}}}}},
    "datasets": {"type": "array", "items": {"$ref": "#/$defs/Dataset"}},
    "analysis": {"$ref": "#/$defs/Analysis"},
    "warnings": {"type": "array", "items": {"type": "string"}},
    "chunks": {"type": "array", "description": "фаза 2: выжимки частей длинного документа", "items": {"type": "object"}}
  },
  "$defs": {
    "Dataset": {"type": "object", "required": ["id", "columns", "rows"], "properties": {
      "id": {"type": "string", "pattern": "^ds\\d+$"},
      "title": {"type": ["string", "null"], "description": "подпись таблицы или ближайший заголовок"},
      "origin": {"enum": ["docx_table", "pdf_table", "pptx_table", "pptx_chart", "text_series"]},
      "loc": {"type": "object"},
      "columns": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "name": {"type": "string"},
        "unit": {"type": ["string", "null"], "description": "«млн руб.», «%», из заголовка колонки"},
        "type": {"enum": ["label", "number", "percent", "date", "text"]}}}},
      "rows": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "label": {"type": "string"},
        "is_total": {"type": "boolean", "description": "«Итого» — не часть ряда для диаграммы долей"},
        "cells": {"type": "object", "additionalProperties": {"type": "object", "properties": {
          "raw": {"type": "string", "description": "как в источнике: «−6», «новый канал»"},
          "value": {"type": ["number", "null"]}}}}}}},
      "axis": {"enum": ["time", "category", null], "description": "time — месяцы, кварталы, годы (для line_chart)"},
      "sums": {"type": "object", "description": "для колонок-процентов: сумма без is_total (проверка chart_share 95–105%)"}}},
    "Analysis": {"type": "object", "properties": {
      "genre": {"enum": ["topic", "report", "requirements", "plan", "article", "other"]},
      "theses": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "text": {"type": "string"},
        "status": {"enum": ["fact", "plan", "requirement", "constraint", "opinion"]},
        "refs": {"type": "array", "items": {"type": "string"}}}}},
      "asks": {"type": "array", "description": "запросы и решения к аудитории: «утвердить бюджет …»", "items": {"type": "object"}},
      "goals": {"type": "array", "items": {"type": "object"}},
      "missing": {"type": "array", "items": {"type": "string"}, "description": "чего нет в источнике: «рост общей выручки в %», «даты этапов»"}}}
  }
}
```

Пример набора данных в промпте (ТЗ 4.8.2) код строит из `Dataset`:
```text
[ds2] Выручка по каналам
колонки: c1 Канал | c2 Выручка, млн руб. | c3 Доля, % | c4 Рост к прошлому году, %
r1: Зал | 30,7 | 65 | +8
r3: Предзаказ | 4,7 | 10 | новый канал
```

---

## 4. Ответы LLM по этапам

### 4.1 OUTLINE — анализ и план

Один вызов возвращает анализ источника и план **содержательных** слайдов. Титул и финальный слайд модель не планирует: заголовок титула — тема пользователя дословно (код), подзаголовок титула модель даёт в `deck.subtitle`; финальный слайд собирает код (`closing`).

```json
{
  "$id": "OutlineResponse", "type": "object", "additionalProperties": false,
  "required": ["analysis", "deck"],
  "properties": {
    "analysis": {"type": "object", "additionalProperties": false,
      "required": ["genre", "theses", "asks", "missing"],
      "properties": {
        "genre": {"enum": ["topic", "report", "requirements", "plan", "article", "other"]},
        "theses": {"type": "array", "maxItems": 20, "items": {"type": "object", "additionalProperties": false,
          "required": ["text", "status", "refs"],
          "properties": {"text": {"type": "string"}, "status": {"enum": ["fact", "plan", "requirement", "constraint", "opinion"]},
                         "refs": {"type": "array", "items": {"type": "string"}}}}},
        "asks": {"type": "array", "items": {"type": "object", "additionalProperties": false,
          "required": ["text", "refs"], "properties": {"text": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}}}},
        "missing": {"type": "array", "items": {"type": "string"}}}},
    "deck": {"type": "object", "additionalProperties": false,
      "required": ["subtitle", "slides"],
      "properties": {
        "subtitle": {"type": "string", "description": "подзаголовок титула — здесь допускается переформулировка темы"},
        "slides": {"type": "array", "minItems": 2, "maxItems": 18, "items": {"$ref": "#/$defs/PlannedSlide"}}}}
  },
  "$defs": {"PlannedSlide": {"type": "object", "additionalProperties": false,
    "required": ["kind", "role", "title", "key_message", "items_planned", "refs", "dataset", "image_query"],
    "properties": {
      "kind": {"enum": ["statement", "bullets", "comparison", "process", "metrics", "chart_series", "chart_share", "conclusion"],
               "description": "код подставляет только kinds, доступные в режиме: «по теме» — без chart_*"},
      "role": {"enum": ["context", "problem", "goal", "results", "details", "requirements", "constraints", "criteria", "plan", "risks", "ask", "conclusion", "team", "other"]},
      "title": {"type": "string", "description": "заголовок-вывод; для statement — само утверждение"},
      "key_message": {"type": "string", "description": "одна мысль слайда — на ней строится запасной слайд без LLM"},
      "items_planned": {"type": ["integer", "null"], "description": "сколько элементов будет (пунктов, шагов, чисел) — нужно SELECT до наполнения"},
      "refs": {"type": "array", "items": {"type": "string"}, "description": "id фрагментов и ячеек источника"},
      "dataset": {"type": ["object", "null"], "additionalProperties": false,
        "required": ["id", "label_column", "value_columns", "rows"],
        "properties": {"id": {"type": "string"}, "label_column": {"type": "string"},
                       "value_columns": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                       "rows": {"type": ["array", "null"], "items": {"type": "string"}, "description": "null — все строки, кроме «Итого»"}}},
      "image_query": {"type": ["string", "null"], "description": "на английском; только если картинка уместна"}}}}
}
```

Проверки кода после ответа (ошибка — повтор вызова с перечнем ошибок, 2 попытки всего):
- число слайдов: `strict` — от 3 до N−2 содержательных (минимум колоды — 5), `extend` и «по теме» — ровно N−2; питч-дек — ровно 9;
- заголовки уникальны; нет двух слайдов с одним `key_message`;
- `chart_*` только с `dataset`, существующим в digest, колонки и строки существуют; `chart_share` — колонка с суммой 95–105% или значения, из которых доля считается;
- если в `analysis.asks` что-то есть, в плане есть слайд `role = ask`, и он **последний** (G-04). Если модель его не поставила — код добавляет `statement` с `role = ask` из текста запроса и сдвигает остальное;
- «по теме» `chart_*` запрещены (ТЗ 3.3.3) — код меняет kind на `bullets`.

### 4.2 CONTENT — наполнение слайда

Схема собирается кодом **для конкретного слайда**: kind задаёт структуру, выбранный вариант — пределы (`maxItems`, `maxLength`). Заголовок слайда берётся из плана и в CONTENT не переписывается (сокращает его, если нужно, FIT).

| kind | Схема ответа (поля) | Пределы из LayoutSpec |
|---|---|---|
| `statement` | `{"body": string\|null}` — утверждение уже в `title` плана | `body.maxLength` |
| `bullets` | `{"items": [{"heading", "text", "icon"}]}` | `minItems/maxItems` по варианту, `heading.maxLength`, `text.maxLength`; `icon` — enum иконок фазы 1 |
| `comparison` | `{"polarity": "neutral"\|"pros_cons"\|"before_after", "left": {"header", "points": [string]}, "right": {…}}` | `points.maxItems` (4), `maxLength` пункта |
| `process` | `{"steps": [{"label", "text"}], "cyclic": false}` | 3–6 шагов по варианту, длины |
| `metrics` | `{"items": [{"value", "unit"\|null, "label", "status", "source_ref"\|null}], "body": string\|null}` | 1 или 2–4 элемента; `value.maxLength` (7), `label.maxLength` |
| `chart_series` | `{"insight": string, "value_axis_title": string\|null, "category_labels": {rowId: shortLabel}\|null}` | `insight.maxLength`; **значений нет** — их ставит код из dataset |
| `chart_share` | `{"insight": string, "labels": {rowId: shortLabel}\|null}` | то же |
| `conclusion` | `{"items": [{"text"}]}` | 2–5 элементов, длина |

`status` у чисел: `fact` | `plan` | `requirement` | `constraint` | `estimate`. Рендерер подписывает не-факты: «цель», «требование», «не более», а `estimate` даёт сноску (ТЗ 4.8.3).

### 4.3 Точечные вызовы FIT
- **Сократи:** вход — текст слота, лимит знаков, язык; выход `{"text": string}` с `maxLength`.
- **Исправь факт:** тот же вызов CONTENT слайда + блок «Ошибки проверки: число 12% не найдено в источнике» — ответ по схеме слайда.

---

## 5. DeckSpec — колода

```json
{
  "$id": "DeckSpec", "type": "object",
  "required": ["schema_version", "id", "revision", "meta", "slides"],
  "properties": {
    "schema_version": {"const": 1},
    "id": {"type": "string", "pattern": "^dk_[0-9A-HJKMNP-TV-Z]{26}$"},
    "revision": {"type": "integer", "minimum": 0},
    "meta": {"type": "object", "properties": {
      "title": {"type": "string", "description": "тема пользователя дословно"},
      "subtitle": {"type": ["string", "null"]},
      "language": {"type": "string"}, "presentation_type": {"type": "string"}, "audience": {"type": "string"},
      "mode": {"enum": ["topic", "material"]}, "source_mode": {"enum": ["strict", "extend", null]}, "genre": {"type": "string"},
      "theme_id": {"type": "string"}, "seed": {"type": "integer"}, "image_mode": {"type": "string"}, "watermark": {"type": "boolean"},
      "author": {"type": ["object", "null"]},
      "slides_requested": {"type": ["integer", "null"]},
      "versions": {"type": "object", "properties": {"engine": {"type": "string"}, "prompts": {"type": "string"}, "layouts": {"type": "string"}, "themes": {"type": "string"}}},
      "created_at": {"type": "string", "format": "date-time"}}},
    "slides": {"type": "array", "minItems": 4, "maxItems": 20, "items": {"$ref": "#/$defs/Slide"}},
    "assets": {"type": "object", "additionalProperties": {"$ref": "#/$defs/ImageAsset"}},
    "degradations": {"type": "array", "items": {"type": "object", "properties": {"stage": {"type": "string"}, "slide_id": {"type": ["string", "null"]}, "reason": {"type": "string"}}}}
  },
  "$defs": {
    "Slide": {"type": "object", "required": ["id", "index", "kind", "role", "variant", "title", "content"], "properties": {
      "id": {"type": "string", "pattern": "^s\\d{2}$"},
      "index": {"type": "integer", "minimum": 1},
      "kind": {"enum": ["title", "statement", "bullets", "comparison", "process", "metrics", "chart_series", "chart_share", "conclusion", "closing"]},
      "role": {"type": "string"},
      "variant": {"type": "string", "description": "id LayoutSpec: «metrics.kpi_cards»"},
      "plan": {"type": "object", "description": "PlannedSlide из OUTLINE — нужен для перегенерации слайда и запасного слайда"},
      "title": {"type": "string"},
      "content": {"type": "object", "description": "по схеме kind (4.2), числа — с value_num и source_ref"},
      "data": {"type": ["object", "null"], "description": "chart_*: снимок данных из dataset", "properties": {
        "dataset_id": {"type": "string"},
        "categories": {"type": "array", "items": {"type": "object", "properties": {"row": {"type": "string"}, "label": {"type": "string"}}}},
        "series": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
          "column": {"type": "string"}, "name": {"type": "string"}, "unit": {"type": ["string", "null"]},
          "values": {"type": "array", "items": {"type": ["number", "null"]}}}}},
        "chart": {"enum": ["column", "line", "donut"]}}},
      "image": {"type": ["string", "null"], "description": "id из assets"},
      "footnote": {"type": ["string", "null"], "description": "ставит код: «Оценочные данные — проверьте перед показом» или «по данным: <файл>»"},
      "notes": {"type": "string"},
      "fit": {"type": "object", "properties": {
        "styles": {"type": "object", "description": "слот → итоговый стиль типошкалы"},
        "shortened": {"type": "array", "items": {"type": "string"}},
        "variant_from": {"type": ["string", "null"]},
        "truncated": {"type": "boolean"}}},
      "checks": {"type": "array", "items": {"type": "object", "properties": {"code": {"type": "string"}, "severity": {"enum": ["fixed", "warning", "error"]}}}},
      "user_locked": {"type": "boolean", "default": false, "description": "фаза 4: слайд правил пользователь — массовые операции его не перегенерируют"}}},
    "ImageAsset": {"type": "object", "description": "сессия 4 (D-067): файл не в DeckSpec — передаётся рендереру отдельно",
      "properties": {
      "provider": {"enum": ["siliconflow"]}, "model": {"type": "string"}, "prompt": {"type": "string"},
      "ms": {"type": "integer"}, "cost_rub": {"type": "number"}}}
  }
}
```

### 5.1 Как DeckSpec выдерживает правки фазы 4

| Правка | Что меняется в DeckSpec | Что запускается | LLM |
|---|---|---|---|
| Сменить тему | `meta.theme_id` | FIT (стили слотов) → RENDER → CONVERT | нет |
| «Другой вид» слайда | `slides[i].variant` | SELECT с исключением текущего варианта; кандидат проходит, только если `content` помещается (проверка fitter'а); затем FIT → RENDER | нет |
| Перегенерировать слайд | `slides[i].content` (и `title`, если просили) | CONTENT по `slides[i].plan` + `SourceDigest` + новый seed слайда → FIT | 1 вызов |
| Сократить / подробнее | `slides[i].content` | CONTENT с указанием «короче / подробнее» → FIT | 1 вызов |
| Заменить картинку | `slides[i].image`, `assets` | IMAGES (следующий результат поиска) → RENDER | нет |

Почему это работает:
- `content` описан схемой **kind**, одинаковой для всех вариантов kind. Вариант — только раскладка. Поэтому смена варианта не требует нового текста.
- `plan` каждого слайда сохранён, поэтому слайд можно наполнить заново без повторного OUTLINE.
- `seed` колоды и `id` слайдов стабильны — «другой вид» и перегенерация воспроизводимы и не сдвигают остальные слайды.
- Каждая правка — новая ревизия: `revision += 1`, прошлый снимок — в `deck_revisions`.

### 5.2 Версионирование
- Новое поле без смены смысла — та же `schema_version`, поле необязательное.
- Смена смысла или структуры — `schema_version + 1` и функция `upgrade_vN_to_vN+1`. Тест: все DeckSpec из фикстур поднимаются до текущей версии и рендерятся.
- Удалённый вариант макета (переименовали в фазе 4): таблица алиасов `layouts/aliases.yaml`; при загрузке старой колоды вариант заменяется по алиасу.

---

## 6. LayoutSpec и Theme (YAML)

### 6.1 LayoutSpec — пример `layouts/bullets/cards_grid.yaml`

Формат уточнён при реализации в сессии 1 (D-044): элементы слайда — один упорядоченный список `elements` (порядок = z-порядок), повторяющиеся элементы — `items.arrangements` по числу элементов, вместимость в YAML не хранится — её считает код. Полное описание формата — docstring `src/core/models/layout.py`.

```yaml
id: bullets.cards_grid
kind: bullets
family: cards                 # штраф соседства одинаковых семей (ТЗ 3.4.3)
version: 1
fallback: true                # самый вместительный вариант kind
applies_when: {image: forbidden, items: {min: 3, max: 6}}
elements:
  - {name: title, type: text, box: [80, 80, 1760, 176], style: h1, min_style: h2, max_lines: 2,
     color: text, bind: title, anchor: middle}
  - {name: footnote, type: text, box: [80, 944, 1760, 40], style: small, min_style: min, max_lines: 1,
     color: text_muted, bind: footnote, optional: true}
items:
  bind: content.items
  arrangements:               # раскладка на каждое число элементов
    3:
      grid: {area: [80, 280, 1760, 640], cols: 3, rows: 1, gap: 40}
      elements:
        - {name: card, type: rect, box: [0, 0, w, h], fill: surface, radius: true}
        - {name: icon, type: icon, box: [32, 32, 64, 64], color: primary, bind: icon}
        - {name: heading, type: text, box: [32, 120, w-64, 96], style: h3, min_style: body, max_lines: 2,
           color: text, bind: heading}
        - {name: text, type: text, box: [32, 232, w-64, h-264], style: body, min_style: small, max_lines: 8,
           color: text_muted, bind: text}
    4: { … }                  # см. layouts/bullets/cards_grid.yaml
```

Правила формата:
- координаты — на сетке 1920×1080; внутри `arrangements` — относительно ячейки (`w`, `h` — размер ячейки), `center_last_row` центрирует неполный последний ряд;
- цвет — только токен темы (ТЗ 3.4.2);
- вместимость для промпта и схемы — `capacity()` по формуле `05_LAYOUTS.md`, 2; `min_style` — нижний шаг fitter'а; `never_truncate` — текст не обрезается (тема на титуле);
- `bind` — путь в `Slide` / `DeckSpec` (`title`, `meta.title`, `footnote`, `content.<поле>`, поле пункта, `$index`, `const:<текст>`).

### 6.2 Theme — `themes/<id>.yaml`

С сессии 4 (D-063, D-064) — четыре темы `business_slate`, `ember_dark`, `sunny_cream`, `mint_coral`; полный пример — `themes/business_slate.yaml`, токены и стиль всех тем — `05_LAYOUTS.md`, 6. Поля:

```yaml
id, name: {ru, en}, mode: light | dark, tags, free, enabled
replaces: [graphite_light]     # старые id, которые переводятся на эту тему (бот, API, DeckSpec)
fonts: {heading: Arial, body: Arial}
colors:                        # bg, surface, surface_alt, primary, on_primary, brand, brand_2, on_brand,
                               # accent, on_accent, paper, on_paper, text, text_muted, border,
                               # on_status, positive, negative
chart: [6 цветов]
typescale: {hero: 96, display: 60, h1: 36, h2: 26, h3: 20, body: 18, small: 14, min: 12}
style:                         # radius, image_radius, gradient_angle, card (fill | outline), card_line,
                               # card_stripe (left | top | none), stripe_fill, header (plate | underline |
                               # marker | bar), badge (circle | square | pennant | flag | chevron), badge_fill,
                               # badge_text, icon_plate, quote_plate, quote_text, pause, pause_text,
                               # accent_fill, title_color
decor: {cover: [...], band: [...], content: [...], header: [...], pause: [...]}   # фигуры custGeom
contrast_pairs: [[text, bg], ...]                  # тест ≥ 4.5:1
```

Остальные три темы — `themes/graphite_dark.yaml`, `azure_coral.yaml`, `fresh_green.yaml`; токены и контраст — `05_LAYOUTS.md`, 6.

---

## 7. Контракт API v1

Реализован в сессии 3 (перенесён из фазы 3, D-054): `src/api/`, отдельный сервис Railway «api». Руководство разработчика клиента — `docs/API.md`; машинная схема — `/v1/openapi.json`, её снимок — `tests/unit/api_contract_v1.json` (тест не пропускает несовместимые изменения, D-059). Ниже — контракт и что изменено относительно первоначального описания (7.5).

### 7.1 Эндпоинты

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| `POST` | `/v1/decks` | JSON `DeckCreate` или `multipart/form-data` (поле `params` с JSON `DeckCreate` + поле `file`). Заголовки `Authorization: Bearer <key>`, `Idempotency-Key` (необязательно) | `202` + `DeckStatus` (`status = queued`), `Location`, `X-Daily-Remaining`; повтор с тем же ключом — `200` + `DeckStatus` той же колоды |
| `GET` | `/v1/decks/{id}` | — | `DeckStatus` (7.2); чужая колода — `404` |
| `GET` | `/v1/decks/{id}/files/{pptx\|pdf}` | — | `200` — байты файла (скачивание через API с ключом) |
| `GET` | `/v1/themes` | — | `{"themes": [{"id", "name", "mode", "tags"}]}` |
| `GET` | `/v1/usage` | — | лимиты ключа и израсходовано за сутки |
| `GET` | `/v1/health` | без ключа | `{"status": "ok" \| "degraded", "version", "engine", "db", "redis"}`; `503` при `degraded` |

`DeckCreate` — `DeckRequest` (раздел 2) без полей, которые задаёт сервер:

```json
{
  "$id": "DeckCreate", "type": "object", "additionalProperties": false,
  "required": ["input"],
  "properties": {
    "presentation_type": {"type": "string", "default": "doklad", "description": "pitch_deck и прочие — 400 UNSUPPORTED_TYPE (до сессии 5)"},
    "input": {"type": "object", "additionalProperties": false, "required": ["topic"], "properties": {
      "topic": {"type": "string", "minLength": 3, "maxLength": 200},
      "text": {"type": ["string", "null"], "maxLength": 200000, "description": "текст-материал; сервер кладёт его в uploads/, в decks — ссылка"}}},
    "source_mode": {"enum": ["strict", "extend", null], "description": "только с материалом; по умолчанию strict"},
    "language": {"enum": ["ru", "en", "uz", "kk"], "default": "ru"},
    "audience": {"enum": ["general", "students", "colleagues", "management", "clients", "investors"], "default": "general"},
    "slides_count": {"type": ["integer", "null"], "minimum": 4, "maximum": 20},
    "theme_id": {"enum": ["business_slate", "ember_dark", "sunny_cream", "mint_coral", "graphite_light", "graphite_dark", "azure_coral", "fresh_green"], "default": "graphite_light"},
    "image_mode": {"enum": ["ai", "none"], "default": "ai"},
    "author": {"type": ["object", "null"], "properties": {"name": {"type": ["string", "null"]}, "group": {"type": ["string", "null"]}}},
    "webhook_url": {"type": ["string", "null"], "maxLength": 2000},
    "seed": {"type": ["integer", "null"]}
  }
}
```

Сервер подставляет в `DeckRequest`: `client = {"kind": "api", "api_client_id", "plan"}`, `watermark` — из настройки ключа, `image_mode` — из необязательного поля запроса (`ai` по умолчанию, `none`; сессия 4), `theme_id` — новый id (старые переводятся, D-063), материал — ссылкой `uploads/` (файл — по расширению имени: pdf, docx, pptx, txt, до 20 МБ). Профиля у клиента API нет: значения по умолчанию не запоминаются.

### 7.2 DeckStatus

```json
{
  "id": "dk_01J…",
  "status": "queued | processing | done | done_pdf_pending | failed",
  "stage": "ingest | outline | content | render | convert | null",
  "progress": {"slides_done": 4, "slides_total": 9},
  "title": "тема дословно", "slides": 9,
  "created_at": "…", "started_at": "…", "finished_at": "…",
  "files": {"pptx": "https://<api>/v1/decks/dk_…/files/pptx", "pdf": "… | null", "expires_at": "…"},
  "warnings": [{"code": "SLIDES_SHORT", "message": "В материале хватило на 7 слайдов из 9. …"}],
  "error": {"code": "SCAN_WITHOUT_TEXT", "message": "…"}
}
```

- `stage` — только при `processing`; `progress` — только на этапе `content` (Redis, не `decks`: правится на каждом слайде).
- `warnings` — из `decks.warnings` (`slides_short:7/9`, `truncated:40000/62000`, `pdf_failed` → `SLIDES_SHORT`, `MATERIAL_TRUNCATED`, `PDF_FAILED`).
- Колода `queued` / `processing` дольше 15 минут отдаётся как `failed` / `DEADLINE_EXCEEDED` (воркер её потерял); строку правит сторож зависших колод (сессия 5).
- Файлы колод API: S3 (`decks/ГГГГ/ММ/ДД/<id>.<ext>`, 24 ч), без S3 — Redis на 1 ч (D-058); `decks.files = {"pptx": ref, "pdf": ref | null, "expires_at", "backend"}`.

### 7.3 Коды ошибок (общие для бота и API)

Формат ответа — `{"error": {"code", "message"}}`, `message` — по-русски для разработчика и пользователя. Коды — часть контракта: добавлять можно, переименовывать и удалять — нет.

| Код | HTTP (API) | Когда |
|---|---|---|
| `BAD_REQUEST` | 400 | неверные параметры, неизвестное поле, не JSON (ошибки валидации — 400, а не 422 FastAPI) |
| `UNSUPPORTED_TYPE` | 400 | `presentation_type` не `doklad` |
| `FILE_UNSUPPORTED`, `FILE_TOO_LARGE` | 400 / 413 | расширение файла; файл > 20 МБ или JSON > 1 МБ |
| `UNAUTHORIZED` | 401 | нет, неверный или отозванный ключ |
| `NOT_FOUND`, `METHOD_NOT_ALLOWED` | 404 / 405 | нет колоды (или чужая), неверный адрес |
| `DECK_NOT_READY`, `DECK_FAILED` | 409 | файлы запрошены до `done` или у упавшей колоды |
| `FILE_NOT_AVAILABLE` | 404 | PDF не получился |
| `FILE_EXPIRED` | 410 | срок хранения файлов истёк |
| `RATE_LIMITED` | 429 | превышены запросы или новые колоды в минуту на ключ |
| `DAILY_LIMIT_EXCEEDED` | 429 | исчерпан суточный лимит колод ключа |
| `SERVICE_UNAVAILABLE` | 503 | недоступны БД, очередь или хранилище |
| `SCAN_WITHOUT_TEXT`, `BAD_FILE`, `PARSE_TIMEOUT`, `UPLOAD_EXPIRED` | — (в статусе) | INGEST |
| `OUTLINE_FAILED` | — | OUTLINE |
| `RENDER_FAILED` | — | RENDER |
| `STORAGE_FAILED` | — | файлы колоды API не сохранились |
| `DEADLINE_EXCEEDED` | — | колода не собралась даже с деградацией или потеряна воркером |
| `INTERNAL` | 500 / в статусе | прочее |

### 7.4 Лимиты и webhook

- Лимиты — на ключ, в `api_clients` (D-057): `daily_limit` колод за календарные сутки UTC (упавшие не считаются; проверка и вставка — под блокировкой строки клиента), `rate_limit_per_min` любых запросов и `decks_per_min` новых колод — окно календарной минуты в Redis. Ответы с ключом несут `X-RateLimit-Limit / Remaining / Reset`, `429` — `Retry-After`.
- Webhook: `POST webhook_url`, тело — `DeckStatus`, заголовок `X-Fibonacci-Signature: sha256=<HMAC-SHA256 тела секретом webhook клиента>`, `X-Fibonacci-Deck-Id`, `X-Fibonacci-Attempt`; при `done` и `failed`; ответ не `2xx` — 3 повтора через 10 с, 1 мин, 5 мин (задачи ARQ `deliver_webhook_job`), без редиректов.

### 7.5 Что изменено относительно первоначального описания (сессия 3) и почему

| Было в контракте | Стало | Почему |
|---|---|---|
| `GET …/files/{fmt}` → `302` на подписанную ссылку S3 (24 ч) | `200` и байты файла через API с ключом; хранение S3 (24 ч) или Redis (1 ч) | S3 на проде ещё нет (сессия 5); ссылка через API работает с любым хранилищем и не раздаёт файл без ключа; клиенту всё равно — он скачивает URL из `files` (D-058) |
| `POST` → `202 {"id", "status", "created_at"}` | `202` + полный `DeckStatus` | один парсер ответа у клиента для `POST`, повтора по `Idempotency-Key` и `GET`; поля — надмножество прежних |
| `GET /v1/themes` → массив | `{"themes": [...]}` | в объект можно добавлять поля (например, тема по умолчанию) без нарушения совместимости — с сессии 3 у API живые клиенты |
| `presentation_type` — перечисление | строка; не `doklad` — `400 UNSUPPORTED_TYPE` с понятным текстом | питч-дек пока не на новом движке; ошибка валидации перечисления не объясняет, что тип появится позже |
| `DeckCreate` = `DeckRequest` без `client` | ещё без `watermark`, `image_mode`, `parent_deck_id`, `schema_version` | водяной знак — свойство ключа, а не запроса; картинок ещё нет (поле добавится необязательным); «Повторить» — только в боте |
| `warnings` — строки `"material_short: 7 of 9"` | объекты `{"code", "message"}` | код — для логики клиента, текст — показать пользователю второго бота |
| `DeckStatus` | + `title`, `slides`, `started_at`; `progress` только на этапе `content` | клиенту нужна подпись к файлу и число слайдов без разбора PPTX |
| `RATE_LIMITED` — «10 колод в минуту» | три лимита ключа: колоды в сутки (`DAILY_LIMIT_EXCEEDED`), колоды в минуту, запросы в минуту | требование владельца: лимит генераций в сутки на ключ и частота запросов |
| — | `GET /v1/usage` | второй бот может показать остаток лимита и не упираться в `429` |
| Ошибок «нет колоды», «не готова», «истёк срок» нет | `NOT_FOUND`, `DECK_NOT_READY`, `DECK_FAILED`, `FILE_NOT_AVAILABLE`, `FILE_EXPIRED`, `SERVICE_UNAVAILABLE`, `UPLOAD_EXPIRED`, `STORAGE_FAILED` | понадобились при реализации скачивания и хранения |
| Подпись webhook — «ключом клиента» | отдельным секретом `whsec_…` (показывается вместе с ключом) | ключ хранится только хэшем — подписать им тело нельзя; отдельный секрет можно сменить, не меняя ключ |
| `api_clients.webhook_secret_hash`, `rate_limit_per_min` | `webhook_secret` (как есть), `key_prefix`, `daily_limit`, `rate_limit_per_min`, `decks_per_min`, `watermark`, `revoked_at` (8.3) | хэш секрета не годится для HMAC; лимиты и водяной знак — по требованию владельца |

---

## 8. Изменения схемы БД

### 8.1 Миграция `0005_decks` (фаза 1, сессия 1)

**Таблица `decks`:**

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | `varchar(32)` PK | `dk_<ULID>` |
| `user_id` | `bigint` FK `users.user_id`, NULL | NULL у колод API |
| `api_client_id` | `varchar(32)`, NULL | колоды API; FK на `api_clients` — миграция `0006` (сессия 3) |
| `idempotency_key` | `varchar(128)`, NULL | уникален в паре с `api_client_id` |
| `parent_deck_id` | `varchar(32)`, NULL | «Повторить с другими параметрами» |
| `status` | `varchar(24)` | `queued`, `processing`, `done`, `done_pdf_pending`, `failed` |
| `stage` | `varchar(16)`, NULL | текущий этап |
| `request` | JSONB | `DeckRequest` |
| `spec` | JSONB, NULL | `DeckSpec` последней ревизии |
| `revision` | `int` default 0 | |
| `files` | JSONB, NULL | `{"pptx": key, "pdf": key}` |
| `digest_key` | `varchar(256)`, NULL | ключ `SourceDigest` в S3 / Redis |
| `durations_ms` | JSONB | по этапам |
| `usage` | JSONB | токены по этапам и вызовам |
| `cost_rub` | `numeric(10,4)` | накопительно, включая правки |
| `degradations` | JSONB | |
| `error_code` | `varchar(32)`, NULL | 7.3 |
| `counted` | `boolean` default false | списана ли генерация с лимита (списывается после успешной отправки) |
| `created_at`, `started_at`, `finished_at` | `timestamptz` | |

Индексы: `(user_id, created_at desc)`, `(status, started_at)` для сторожа, уникальный `(api_client_id, idempotency_key)`.

**Таблица `deck_revisions`** (создаётся сразу, пишется в фазе 4): `id` serial PK, `deck_id` FK, `revision`, `action` (`theme`, `variant`, `regen_slide`, `shorten`, `image`), `slide_id` NULL, `spec` JSONB, `cost_rub`, `created_at`. Хранятся последние 10 на колоду.

### 8.2 Что происходит со старыми таблицами
- `presentations` — перестаёт пополняться с сессии 1. Счётчик `users.presentations_count` остаётся источником лимита. Таблица удаляется миграцией в фазе 2, когда `decks` проработает неделю (`08_MIGRATION.md`) — это исключение из правила «миграции только добавляющие» (`CLAUDE.md`, с сессии 3), только отдельным решением владельца.
- `users` — без изменений (колонки `last_*` из `0004` используются сводкой). `last_color_scheme` хранит `theme_id`: старые значения (`light`, `dark`, `forest`, `ember`) читаются через таблицу соответствия (`light` → `business_slate`, `dark` и `ember` → `ember_dark`, `forest` → `mint_coral`; темы сессий 1–3 — по полю `replaces` темы; прочие → тема по умолчанию; сессия 4, D-063).

### 8.3 Миграция `0006_api_clients` (сессия 3)

**Таблица `api_clients`:**

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | `varchar(32)` PK | `ac_<ULID>` |
| `name` | `varchar(128)` | «Второй бот» |
| `key_hash` | `varchar(64)`, уникальный | SHA-256 ключа `fib_…`; сам ключ не хранится (D-056) |
| `key_prefix` | `varchar(16)` | начало ключа — узнать его в списке клиентов |
| `webhook_secret` | `varchar(64)` | `whsec_…`, как есть: им подписывается webhook (HMAC) |
| `daily_limit` | `int` default 100 | колод за сутки UTC |
| `rate_limit_per_min` | `int` default 120 | любых запросов в минуту |
| `decks_per_min` | `int` default 10 | новых колод в минуту |
| `watermark` | `boolean` default true | водяной знак в PDF колод клиента |
| `plan` | `varchar(16)` default `free` | `free` при водяном знаке, иначе `pro`; тарифы — фаза 3 |
| `active` | `boolean` default true | `false` — ключ отозван (`401`) |
| `created_at`, `revoked_at` | `timestamptz` | |

**`decks`:** + `warnings` JSONB (предупреждения колоды для статуса API); индекс `(api_client_id, created_at)` для суточного лимита; FK `decks.api_client_id → api_clients.id` (на Postgres — `NOT VALID`: проверяются новые строки, накат после отката не падает).

Миграция только добавляющая: откат кода без отката БД безопасен.
