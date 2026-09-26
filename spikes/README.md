# spikes/

Одноразовые эксперименты фазы 0. Код здесь **не импортируется** из `src/` и не попадает в Docker-образ.

| Скрипт | Что делает | Результат в `out/` |
|---|---|---|
| `pptx_spike.py` | 5 слайдов на python-pptx из нативных объектов (column-диаграмма, таблица, карточки с иконками, кольцевая диаграмма, процесс фигурами) → PDF через LibreOffice, замер времени на 5 и 20 слайдах | `spike_deck.pptx`, `spike_deck.pdf`, `previews/spike_deck-*.png`, `pptx_report.json` |
| `fonts_spike.py` | 8 шрифтов-кандидатов × 15 языков + строка символов, подстановка для hy/ka → PDF; покрытие глифов у шрифтов-замен на сервере | `fonts_matrix.pptx`, `fonts_matrix.pdf`, `previews/fonts_matrix-*.png`, `fonts_report.json`, `fonts_coverage.md` |
| `common.py` | сетка 1920×1080 → EMU, пробная тема, иконки Tabler → PNG, конвертация | — |

Отчёт и чек-лист ручной проверки — `docs/SPIKE_PPTX.md`.

## Как перезапустить

```bash
pip install python-pptx==0.6.23 Pillow fonttools brotli pymupdf
# Ubuntu: LibreOffice Impress и шрифты, которые понадобятся воркеру
apt-get install --no-install-recommends libreoffice-impress \
    fonts-liberation fonts-crosextra-carlito fonts-dejavu-core fonts-noto-core
python spikes/pptx_spike.py
python spikes/fonts_spike.py
```
