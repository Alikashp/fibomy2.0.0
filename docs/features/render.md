# Рендер, макеты и темы

**Назначение:** нарисовать `DeckSpec` в редактируемый PPTX из нативных объектов (фигуры, текст, диаграммы) и сконвертировать его в PDF. Вид слайда задают декларативные файлы, а не код.

## Как работает

1. **Макет варианта** — `layouts/<kind>/<variant>.yaml`:
   - элементы с рамками на сетке 1920×1080, стилями текста (`style` → `min_style`), числом строк;
   - повторяющиеся элементы (`items.arrangements` — раскладка на 3, 4, 6 пунктов);
   - условия применимости (`applies_when`), семья (`family`), запасной вариант (`fallback`).

   Код загружает их в `LayoutSpec` (`core.models.layout.load_layouts`), из них же считается вместимость слотов для промпта (`capacity`).
2. **Тема** — `themes/<id>.yaml`: цвета-токены (`bg`, `surface`, `primary`, `brand`, `brand_2`…), шрифты, стиль оформления (заголовок, карточки, декор), `free`, `enabled`. Старые id тем принимаются через `replaces` (D-063).
3. **RENDER** (`core.render.pptx.renderer.render_pptx`) рисует слайды по `DeckSpec` и итогам подгонки (`Slide.fit`):
   - фон — цвет или градиент `brand → brand_2` (титул, финал, пауза);
   - оформление темы — `core/render/pptx/shapes.py`;
   - нативные диаграммы column, line, donut — `core/render/pptx/charts.py`;
   - иконки — PNG-маски из `assets/icons/`, перекрашенные в цвет токена (`core/render/pptx/icons.py`);
   - картинки — файлом внутри PPTX.
4. **Колонтитул:** справа номер «N / M» на содержательных слайдах; слева «Fibonacci.» — только в копии PPTX для PDF бесплатного тарифа (`watermark=True`).
5. **CONVERT** (`core.render.pdf.convert.pptx_to_pdf`) — LibreOffice headless, у каждой конвертации свой профиль; одновременно не больше `LIBREOFFICE_MAX_PARALLEL` на процесс. Воркер прогревает LibreOffice при старте.

## Где в коде

- `src/core/render/pptx/renderer.py`, `shapes.py`, `charts.py`, `icons.py`.
- `src/core/render/pdf/convert.py`.
- `src/core/models/layout.py` — `LayoutSpec`, `capacity`; `src/core/models/theme.py` — `Theme`, `enabled_theme_ids`.
- `layouts/`, `themes/`, `fonts/`, `assets/icons/` (набор иконок — `prompts/v2/icons.yaml`, сборка — `scripts/build_icons.py`).
- Превью тем для `GET /v1/themes` — `assets/theme_previews/`, сборка — `scripts/build_theme_previews.py` (D-077).
- Каталог вариантов с вайрфреймами и правилами оформления — `docs/design/05_LAYOUTS.md` (черновик; правда — YAML).

## Нюансы

- **Рендерер ничего не решает.** Выбор варианта, кегли, сокращения и замены делают SELECT и FIT; рендерер рисует результат как есть. Логику вида кладите в YAML или в FIT, не в рендерер.
- **Правила совместимости PPTX из спайка** (`docs/SPIKE_PPTX.md`): у фигур удаляется `<p:style>` и цвета заданы явно (без теней), автоподбор размера выключен, поля рамок нулевые, у текста атрибут `lang` по языку колоды. Нарушите — файл может открываться с предупреждением «восстановить» или выглядеть иначе, чем PDF.
- **PDF не получился — колода отдаётся с одним PPTX** и пометкой в подписи; деградация `convert` в `DeckSpec`.
- **Водяной знак только в PDF:** PPTX пользователь получает без него; для PDF рендерится отдельная копия PPTX.
- **Новая тема или вариант** — новый YAML. Галерея (`quality.md`) прогонит его на всех заполнениях и темах; без зелёной галереи и одобрения владельцем по артефакту `gallery-pdf` дизайн не меняется.
- **Шрифт — Arial** (в образе и CI — Liberation Sans с теми же метриками). Другой шрифт требует метрик в `fonts/` для подгонки (`fitting.md`).
