"""
Ядро нового движка генерации (фаза 1, docs/design/03_ARCHITECTURE.md).

Единственная точка входа для адаптеров (бот, воркер, API фазы 3) —
core.pipeline.generate_deck. Ядро не знает про Telegram и HTTP.
Модули этапов зависят от models, llm, checks, но не друг от друга:
порядок этапов знает только pipeline.
"""
