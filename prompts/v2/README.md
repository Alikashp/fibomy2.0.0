# prompts/v2 — промпты нового движка

Проект — `docs/design/06_PROMPTS.md`. Какой блок куда подставить, решает код (`src/core/llm/prompts.py`, `src/core/planning`, `src/core/content`) по режиму, `source_mode`, типу презентации и жанру: условий «если есть материал» внутри текстов нет (ТЗ 4.8.6).

- Подстановки — `$имя` (`string.Template`), чтобы фигурные скобки JSON в тексте не мешали.
- `.txt` — цельный текст, последний перевод строки отбрасывается; `.yaml` — словари блоков.
- Примеры «плохо / хорошо» — только из областей вне golden-корпуса (школа, склад, библиотека, транспорт). Лексика кейсов G-01…G-05 запрещена — проверяет тест `test_prompts_v2.py`.
- Любая правка — новая строка в `VERSION` и прогон golden (ТЗ 4.8.7).

| Файл | Где используется |
|---|---|
| `common/language.txt`, `input_safety.txt`, `data_blocks.yaml`, `audience.yaml` | OUTLINE и CONTENT |
| `outline/system.txt`, `rules_topic.txt`, `slide_count.yaml`, `storylines/*.yaml`, `user.txt` | OUTLINE |
| `content/system.txt`, `task.txt`, `capacity.txt`, `kinds/*.txt` | CONTENT |
| `icons.yaml` | enum поля `icon` в схеме CONTENT и PNG в `assets/icons/` (`system` — иконки макетов, модель их не выбирает) |
| `images/config.yaml`, `style.yaml`, `appearance.yaml` | ИИ-картинки (`core/images`, D-067): модель, число на колоду, таймаут, цена; стиль темы и правила промпта; внешность людей по языку |
