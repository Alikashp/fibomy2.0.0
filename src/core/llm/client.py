"""Клиент LLM нового движка: structured outputs, повторы с текстом ошибки, учёт токенов.

Ответ запрашивается как json_schema strict (04_CONTRACTS.md, 1). Если провайдер
отвечает 400 на response_format (вопрос 6: proxyapi + gpt-6-luna), клиент
запоминает это для модели на весь процесс и переходит на json_object: схема
уходит текстом в системный промпт, ответ проверяет pydantic. Пределы
(maxLength, maxItems) в любом случае дублируются в тексте промпта и
проверяются fitter'ом.

Повтор (attempts): невалидный JSON, ответ не по схеме или не прошёл проверку
validate() — следующий запрос содержит прошлый ответ и список ошибок.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from config import settings
from core.llm.models import UsageTotals, add_usage, completion_params

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

# Модели, для которых провайдер отказал в json_schema (на время жизни процесса)
_SCHEMA_REJECTED: set[str] = set()
_semaphore: asyncio.Semaphore | None = None


class LLMError(RuntimeError):
    """Не удалось получить валидный ответ за отведённые попытки."""


@dataclass
class DeckUsage:
    """Токены и стоимость колоды по этапам (decks.usage, decks.cost_rub)."""
    by_stage: dict[str, UsageTotals] = field(default_factory=dict)

    def stage(self, name: str) -> UsageTotals:
        return self.by_stage.setdefault(name, UsageTotals())

    @property
    def cost_rub(self) -> float | None:
        costs = [t.cost_rub for t in self.by_stage.values()]
        return None if any(c is None for c in costs) else round(sum(costs), 4)

    def as_dict(self) -> dict:
        return {name: totals.as_dict() for name, totals in self.by_stage.items()}


def stage_model(stage: str) -> tuple[str, str]:
    """(модель, reasoning_effort) этапа: LLM_MODEL_<STAGE> / LLM_EFFORT_<STAGE> или общие."""
    model = getattr(settings, f"llm_model_{stage}", "") or settings.openai_model
    effort = getattr(settings, f"llm_effort_{stage}", "") or settings.openai_reasoning_effort
    return model, effort


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.llm_max_concurrency))
    return _semaphore


def schema_mode(model: str) -> str:
    forced = (settings.llm_structured_outputs or "auto").strip().lower()
    if forced in ("json_schema", "json_object"):
        return forced
    return "json_object" if model in _SCHEMA_REJECTED else "json_schema"


def _is_response_format_rejection(error: APIStatusError) -> bool:
    if error.status_code != 400:
        return False
    text = str(error).lower()
    return any(word in text for word in ("response_format", "json_schema", "schema", "strict"))


class LLMClient:
    def __init__(self, client: AsyncOpenAI | None = None):
        self._client = client or AsyncOpenAI(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_seconds, max_retries=0,
        )

    async def structured(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        schema_name: str,
        schema: dict,
        model_cls: type[M],
        visible_tokens: int,
        timeout: float,
        attempts: int = 2,
        usage: DeckUsage | None = None,
        validate: Callable[[M], list[str]] | None = None,
        soft: Callable[[M], list[str]] | None = None,
        log_fields: dict | None = None,
    ) -> M:
        """validate — ошибки, без исправления которых ответ не принимается; soft — замечания,
        ради которых делается повтор, но на последней попытке ответ принимается с ними."""
        model, effort = stage_model(stage)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = "no attempts"
        attempt = 0
        while attempt < attempts:
            attempt += 1
            mode = schema_mode(model)
            params = completion_params(visible_tokens, model=model, effort=effort)
            sent = list(messages)
            if mode == "json_schema":
                params["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema_name, "strict": True, "schema": schema}}
            else:
                params["response_format"] = {"type": "json_object"}
                sent[0] = {"role": "system", "content": system + "\n\nФОРМАТ ОТВЕТА\nОдин JSON-объект строго "
                           "по этой JSON Schema (все поля обязательны, лишних полей нет):\n"
                           + json.dumps(schema, ensure_ascii=False)}
            started = time.perf_counter()
            try:
                async with _get_semaphore():
                    response = await asyncio.wait_for(
                        self._client.chat.completions.create(messages=sent, **params), timeout=timeout)
            except APIStatusError as e:
                if mode == "json_schema" and _is_response_format_rejection(e) \
                        and (settings.llm_structured_outputs or "auto") == "auto":
                    _SCHEMA_REJECTED.add(model)
                    logger.warning("Provider rejected json_schema — falling back to json_object",
                                   extra={"stage": stage, "model": model, "error": str(e)[:300]})
                    attempt -= 1  # отказ формата — не попытка модели
                    continue
                last_error = f"API {e.status_code}: {str(e)[:300]}"
                logger.warning("LLM call failed", extra={"stage": stage, "attempt": attempt, "error": last_error,
                                                         **(log_fields or {})})
                continue
            except (asyncio.TimeoutError, Exception) as e:  # сеть, таймаут
                last_error = f"{type(e).__name__}: {str(e)[:300]}"
                logger.warning("LLM call failed", extra={"stage": stage, "attempt": attempt, "error": last_error,
                                                         **(log_fields or {})})
                continue

            if usage is not None:
                add_usage(usage.stage(stage), response, model)
            choice = response.choices[0]
            content = choice.message.content or ""
            self._log_call(stage, model, mode, response, started, attempt, log_fields)

            errors = self._check(content, model_cls, validate, choice.finish_reason)
            if isinstance(errors, BaseModel):
                remarks = soft(errors) if soft else []
                if not remarks or attempt >= attempts:
                    if remarks:
                        logger.warning("LLM answer accepted with remarks", extra={
                            "stage": stage, "remarks": "; ".join(remarks)[:500], **(log_fields or {})})
                    return errors
                errors = remarks
            last_error = "; ".join(errors)[:1000]
            logger.warning("LLM answer rejected", extra={"stage": stage, "attempt": attempt, "errors": last_error,
                                                         **(log_fields or {})})
            messages = messages[:2] + [
                {"role": "assistant", "content": content[:20000]},
                {"role": "user", "content": "Ответ не прошёл проверку:\n- " + "\n- ".join(errors)
                 + "\nИсправь и пришли JSON целиком заново."},
            ]
        raise LLMError(f"{stage}: no valid answer after {attempts} attempts ({last_error})")

    @staticmethod
    def _check(content: str, model_cls, validate, finish_reason) -> list[str] | BaseModel:
        if not content.strip():
            return [f"пустой ответ (finish_reason={finish_reason})"]
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return [f"невалидный JSON: {e.msg} (finish_reason={finish_reason})"]
        try:
            parsed = model_cls.model_validate(data)
        except ValidationError as e:
            return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:10]]
        problems = validate(parsed) if validate else []
        return problems or parsed

    @staticmethod
    def _log_call(stage, model, mode, response, started, attempt, log_fields) -> None:
        one = UsageTotals()
        add_usage(one, response, model)
        logger.info("LLM call", extra={
            "stage": stage, "model": model, "schema_mode": mode, "attempt": attempt,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "finish_reason": response.choices[0].finish_reason,
            **{k: v for k, v in one.as_dict().items() if k not in ("calls", "models")},
            **(log_fields or {}),
        })


_default: LLMClient | None = None


def get_client() -> LLMClient:
    global _default
    if _default is None:
        _default = LLMClient()
    return _default


def set_client(client: LLMClient | None) -> None:
    """Для тестов: подменить клиент по умолчанию."""
    global _default
    _default = client
