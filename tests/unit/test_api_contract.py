"""Обратная совместимость API v1: текущая схема OpenAPI сверяется со снимком
api_contract_v1.json (CLAUDE.md: изменения API — только обратно совместимые).

Ломает второй бот и запрещено:
- удалить путь, метод или успешный код ответа;
- удалить или переименовать поле (в запросе и в ответе), сменить его тип;
- сделать обязательным поле запроса, которое было необязательным, или добавить новое
  обязательное поле запроса;
- убрать значение из перечисления в запросе (язык, тема, аудитория…);
- сделать необязательным (убрать из required) поле ответа.

Можно: новые пути, новые необязательные поля запроса, новые поля ответа, новые
значения перечислений в запросе. После такой правки снимок обновляется:
    python tests/unit/test_api_contract.py --update
и обновлённый файл попадает в тот же PR.

Второй тест: каждый код ошибки и предупреждения из кода описан в docs/API.md.
"""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

import _helpers  # noqa: F401
from _helpers import ROOT

SNAPSHOT = Path(__file__).with_name("api_contract_v1.json")
REQUEST_SCHEMAS = {"DeckCreate", "DeckInput", "Author"}
IGNORED_KEYS = {"description", "title", "examples", "default"}


def current_schema() -> dict:
    from api.app import create_app
    spec = create_app(manage_resources=False).openapi()
    return {"paths": spec["paths"], "schemas": spec["components"]["schemas"]}


def _shape(node):
    """Схема поля без описаний и перечислений — для сравнения типа."""
    if isinstance(node, dict):
        return {k: _shape(v) for k, v in node.items() if k not in IGNORED_KEYS | {"enum", "maxLength", "minLength",
                                                                                   "maximum", "minimum"}}
    if isinstance(node, list):
        return [_shape(v) for v in node]
    return node


def _enums(node, path="") -> dict[str, set]:
    found = {}
    if isinstance(node, dict):
        if "enum" in node:
            found[path] = set(map(str, node["enum"]))
        if "const" in node:
            found[path] = {str(node["const"])}
        for k, v in node.items():
            found.update(_enums(v, f"{path}/{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.update(_enums(v, f"{path}/{i}"))
    return found


def breaking_changes(old: dict, new: dict) -> list[str]:
    problems = []
    for path, methods in old["paths"].items():
        if path not in new["paths"]:
            problems.append(f"удалён путь {path}")
            continue
        for method, op in methods.items():
            new_op = new["paths"][path].get(method)
            if new_op is None:
                problems.append(f"удалён метод {method.upper()} {path}")
                continue
            for code in op.get("responses", {}):
                if code.startswith("2") and code not in new_op.get("responses", {}):
                    problems.append(f"{method.upper()} {path}: удалён ответ {code}")
            old_params = {(p["name"], p["in"]) for p in op.get("parameters", [])}
            new_required = {(p["name"], p["in"]) for p in new_op.get("parameters", []) if p.get("required")}
            for p in new_required - old_params:
                problems.append(f"{method.upper()} {path}: новый обязательный параметр {p[0]}")

    for name, schema in old["schemas"].items():
        new_schema = new["schemas"].get(name)
        if new_schema is None:
            problems.append(f"удалена схема {name}")
            continue
        old_props, new_props = schema.get("properties", {}), new_schema.get("properties", {})
        for prop, prop_schema in old_props.items():
            if prop not in new_props:
                problems.append(f"{name}.{prop}: поле удалено")
            elif _shape(prop_schema) != _shape(new_props[prop]):
                problems.append(f"{name}.{prop}: изменился тип поля")
        old_req, new_req = set(schema.get("required", [])), set(new_schema.get("required", []))
        if name in REQUEST_SCHEMAS:
            for prop in new_req - old_req:
                problems.append(f"{name}.{prop}: поле запроса стало обязательным")
            old_enums, new_enums = _enums(schema), _enums(new_schema)
            for path, values in old_enums.items():
                lost = values - new_enums.get(path, values)
                if lost:
                    problems.append(f"{name}{path}: из перечисления запроса удалены {sorted(lost)}")
        else:
            for prop in old_req - new_req:
                problems.append(f"{name}.{prop}: поле ответа перестало быть обязательным")
    return problems


class Contract(unittest.TestCase):

    def test_no_breaking_changes(self):
        old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        problems = breaking_changes(old, current_schema())
        self.assertEqual(problems, [], "Изменение API ломает обратную совместимость (CLAUDE.md). Если правка "
                                       "совместима и тест ошибается — обновите снимок: "
                                       "python tests/unit/test_api_contract.py --update")

    def test_checker_catches_breaks(self):
        old = current_schema()
        new = copy.deepcopy(old)
        del new["schemas"]["DeckStatus"]["properties"]["files"]
        new["schemas"]["DeckCreate"].setdefault("required", []).append("language")
        new["paths"].pop("/v1/themes")
        problems = breaking_changes(old, new)
        self.assertTrue(any("DeckStatus.files" in p for p in problems))
        self.assertTrue(any("DeckCreate.language" in p for p in problems))
        self.assertTrue(any("/v1/themes" in p for p in problems))
        # новое необязательное поле запроса и новое поле ответа — совместимо
        compatible = copy.deepcopy(old)
        compatible["schemas"]["DeckCreate"]["properties"]["image_mode"] = {"type": "string"}
        compatible["schemas"]["DeckStatus"]["properties"]["queue_position"] = {"type": "integer"}
        self.assertEqual(breaking_changes(old, compatible), [])

    def test_error_codes_documented(self):
        from api import errors
        doc = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
        source = (ROOT / "src" / "api" / "app.py").read_text(encoding="utf-8")
        codes = set(re.findall(r'ApiError\(\d{3}, "([A-Z_]+)"', source))
        codes |= set(errors.DECK_ERROR_TEXT)
        codes |= {errors.deck_warning(w)["code"] for w in ("slides_short:1/2", "truncated:1/2", "pdf_failed")}
        codes |= {"METHOD_NOT_ALLOWED", "NOT_FOUND"}
        missing = sorted(c for c in codes if f"`{c}`" not in doc)
        self.assertEqual(missing, [], "Коды не описаны в docs/API.md")


if __name__ == "__main__":
    if "--update" in sys.argv:
        SNAPSHOT.write_text(json.dumps(current_schema(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"Снимок обновлён: {SNAPSHOT}")
    else:
        unittest.main()
