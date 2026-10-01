"""Перенесено в core/llm/models.py (docs/design/03_ARCHITECTURE.md, 2.1).

Модуль оставлен для старого движка (generation/llm.py, worker.py) до его
удаления в сессии 6 — один код параметров и учёта стоимости на оба движка.
"""
from core.llm.models import (  # noqa: F401
    DEFAULT_TEMPERATURE, ModelProfile, UsageTotals, completion_params, cost_rub, profile_for,
    reasoning_effort, record_usage, track_usage,
)
