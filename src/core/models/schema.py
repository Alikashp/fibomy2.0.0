"""Конструктор строгих JSON Schema для ответов LLM (04_CONTRACTS.md, 1).

structured outputs strict: у каждого объекта additionalProperties = false и все
поля в required; необязательное — через тип с null.

Пределов длины и числа элементов (maxLength, maxItems) в схеме нет (D-071). При strict
провайдер ограничивает генерацию грамматикой: на пределе строка принудительно закрывается
посреди слова, а последним токеном нередко встаёт чужой знак — golden PR #83: ровно 64 знака
«…мобильные устройства не требу», «…до 子», «…люди и за다». Пределы стоят в тексте задания;
после ответа длину проверяет код (повтор «сократи»), а вписывание — fitter и
core.fitting.resolve по метрикам шрифта. minItems остаётся: он не обрывает текст.
"""

from typing import Any


def obj(properties: dict[str, dict]) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def string(description: str | None = None) -> dict:
    s: dict[str, Any] = {"type": "string"}
    if description:
        s["description"] = description
    return s


def enum(values: list[str], description: str | None = None) -> dict:
    s: dict[str, Any] = {"type": "string", "enum": list(values)}
    if description:
        s["description"] = description
    return s


def array(items: dict, min_items: int | None = None) -> dict:
    s: dict[str, Any] = {"type": "array", "items": items}
    if min_items is not None:
        s["minItems"] = int(min_items)
    return s


def nullable(schema: dict) -> dict:
    """Тот же тип или null. Для enum null добавляется и в список значений."""
    s = dict(schema)
    t = s.get("type")
    if isinstance(t, str):
        s["type"] = [t, "null"]
    if "enum" in s:
        s["enum"] = [*s["enum"], None]
    return s


def integer_or_null() -> dict:
    return {"type": ["integer", "null"]}


def is_strict(schema: dict) -> bool:
    """Проверка для тестов: все объекты закрыты и все их поля обязательны."""
    if schema.get("type") == "object" or schema.get("properties"):
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False or set(schema.get("required", [])) != set(props):
            return False
        return all(is_strict(p) for p in props.values())
    if "items" in schema:
        return is_strict(schema["items"])
    return True
