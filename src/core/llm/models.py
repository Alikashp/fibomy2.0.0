"""
Параметры запроса к LLM под конкретную модель и учёт токенов/стоимости.

Модели отличаются набором параметров: gpt-6-luna и другие модели с
рассуждениями не принимают max_tokens (нужен max_completion_tokens, и в него
входят токены рассуждений), temperature принимают только при
reasoning_effort=none. Что принимает каждая модель и сколько стоит — в
prompts/models.yaml; здесь только сборка аргументов для chat.completions.create.

openai==1.35.0 не знает аргументов max_completion_tokens и reasoning_effort,
поэтому они уходят через extra_body — в теле запроса это те же поля.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import yaml

from config import settings
from core.paths import PROMPTS_DIR

logger = logging.getLogger(__name__)

# Файл остаётся в prompts/ до удаления старого движка (сессия 6: → prompts/v2/)
with (PROMPTS_DIR / "models.yaml").open(encoding="utf-8") as _f:
    _CONFIG = yaml.safe_load(_f)
_RESERVE = _CONFIG["reasoning_reserve_tokens"]
DEFAULT_TEMPERATURE = 0.7


@dataclass(frozen=True)
class ModelProfile:
    name: str
    token_param: str = "max_tokens"
    reasoning: bool = False
    reasoning_efforts: tuple[str, ...] | None = None
    temperature: str = "always"          # always | effort_none | never
    json_mode: bool = True
    max_output_tokens: int = 16384
    price_rub_per_1m: dict | None = None


def profile_for(model: str) -> ModelProfile:
    models: dict = _CONFIG.get("models") or {}
    key = model if model in models else max(
        (k for k in models if model.startswith(k)), key=len, default=None,
    )
    data = {**_CONFIG["defaults"], **(models.get(key) or {})}
    efforts = data.get("reasoning_efforts")
    return ModelProfile(
        name=model,
        token_param=data["token_param"],
        reasoning=bool(data["reasoning"]),
        reasoning_efforts=tuple(efforts) if efforts else None,
        temperature=data["temperature"],
        json_mode=bool(data["json_mode"]),
        max_output_tokens=int(data["max_output_tokens"]),
        price_rub_per_1m=data.get("price_rub_per_1m"),
    )


def reasoning_effort(profile: ModelProfile, configured: str | None) -> str | None:
    """Значение reasoning_effort для запроса; None — параметр не передаём."""
    if not profile.reasoning or not configured:
        return None
    effort = configured.strip().lower()
    if profile.reasoning_efforts and effort not in profile.reasoning_efforts:
        logger.warning(
            f"OPENAI_REASONING_EFFORT={effort!r} is not supported by {profile.name} "
            f"(allowed: {', '.join(profile.reasoning_efforts)}) — not sending it, model default applies"
        )
        return None
    return effort


def completion_params(
    visible_tokens: int,
    *,
    model: str | None = None,
    effort: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    json_mode: bool = True,
) -> dict:
    """Аргументы chat.completions.create (кроме messages) для текущей модели.

    visible_tokens — бюджет видимого ответа (JSON). У моделей с рассуждениями
    к нему добавляется запас на рассуждения по уровню effort.
    """
    model = model or settings.openai_model
    profile = profile_for(model)
    effort = reasoning_effort(profile, settings.openai_reasoning_effort if effort is None else effort)

    params: dict = {"model": model}
    extra: dict = {}

    if profile.token_param == "max_completion_tokens":
        reserve = _RESERVE.get(effort or "default", _RESERVE["default"]) if profile.reasoning else 0
        extra["max_completion_tokens"] = min(profile.max_output_tokens, visible_tokens + reserve)
    else:
        params["max_tokens"] = min(profile.max_output_tokens, visible_tokens)

    if effort:
        extra["reasoning_effort"] = effort

    if profile.temperature == "always" or (profile.temperature == "effort_none" and effort == "none"):
        params["temperature"] = temperature

    if json_mode and profile.json_mode:
        params["response_format"] = {"type": "json_object"}

    if extra:
        params["extra_body"] = extra
    return params


# ── Учёт токенов и стоимости ─────────────────────────────────────────────────

def _get(obj, name: str, default=0):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default) or default
    value = getattr(obj, name, None)
    if value is None and getattr(obj, "model_extra", None):
        value = obj.model_extra.get(name)  # поля, которых нет в старом SDK
    return value or default


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0          # включая токены рассуждений
    reasoning_tokens: int = 0       # справочно: уже входят в output_tokens
    cost_rub: float | None = 0.0
    models: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "calls": self.calls, "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens, "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_rub": round(self.cost_rub, 4) if self.cost_rub is not None else None,
            "models": sorted(self.models),
        }


def cost_rub(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float | None:
    """Стоимость в ₽ по prompts/models.yaml; None — цены модели нет в конфиге."""
    price = profile_for(model).price_rub_per_1m
    if not price:
        return None
    fresh = max(0, input_tokens - cached_input_tokens)
    return (fresh * price["input"] + cached_input_tokens * price.get("cached_input", price["input"])
            + output_tokens * price["output"]) / 1_000_000


_usage_var: ContextVar[UsageTotals | None] = ContextVar("llm_usage", default=None)


@contextmanager
def track_usage():
    """Суммирует токены всех вызовов LLM внутри блока (включая дозапросы в gather)."""
    totals = UsageTotals()
    token = _usage_var.set(totals)
    try:
        yield totals
    finally:
        _usage_var.reset(token)


def record_usage(response, model: str | None = None) -> None:
    """Добавляет токены ответа в счётчик текущего track_usage() (старый движок)."""
    totals = _usage_var.get()
    if totals is not None:
        add_usage(totals, response, model)


def add_usage(totals: UsageTotals, response, model: str | None = None) -> None:
    """Добавляет токены и стоимость ответа в totals."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    model = model or getattr(response, "model", None) or settings.openai_model
    prompt = _get(usage, "prompt_tokens")
    cached = _get(_get(usage, "prompt_tokens_details", None), "cached_tokens")
    # completion_tokens у OpenAI уже включает рассуждения; reasoning_tokens — разбивка
    completion = _get(usage, "completion_tokens")
    reasoning = _get(_get(usage, "completion_tokens_details", None), "reasoning_tokens")

    totals.calls += 1
    totals.input_tokens += prompt
    totals.cached_input_tokens += cached
    totals.output_tokens += completion
    totals.reasoning_tokens += reasoning
    totals.models.add(model)
    cost = cost_rub(model, prompt, cached, completion)
    totals.cost_rub = None if cost is None or totals.cost_rub is None else totals.cost_rub + cost
