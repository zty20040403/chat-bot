from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolValidation:
    ok: bool
    errors: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return "；".join(self.errors) if self.errors else "工具参数无效。"


class ToolCatalog:
    """Host-owned lookup and a conservative JSON-schema validator."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self._functions: dict[str, dict[str, Any]] = {}
        for tool in tools:
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                self._functions[name] = function

    def contains(self, name: str) -> bool:
        return name in self._functions

    def validate(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        parse_error: str = "",
    ) -> ToolValidation:
        if parse_error:
            return ToolValidation(False, (parse_error,))
        function = self._functions.get(name)
        if function is None:
            return ToolValidation(
                False,
                (f"工具 {name or '[空名称]'} 未向本轮开放。",),
            )
        schema = function.get("parameters")
        if not isinstance(schema, dict):
            return ToolValidation(False, ("工具参数 schema 缺失。",))
        errors: list[str] = []
        _validate_value(arguments, schema, "$", errors, depth=0)
        return ToolValidation(not errors, tuple(errors[:8]))

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._functions)


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    path: str,
    errors: list[str],
    *,
    depth: int,
) -> None:
    if depth > 12:
        errors.append(f"{path} 嵌套过深。")
        return
    expected = schema.get("type")
    if isinstance(expected, list):
        allowed_types = tuple(str(item) for item in expected)
    elif isinstance(expected, str):
        allowed_types = (expected,)
    else:
        allowed_types = ()
    if allowed_types and not any(
        _matches_type(value, item) for item in allowed_types
    ):
        errors.append(
            f"{path} 类型应为 {'/'.join(allowed_types)}，"
            f"实际是 {_type_name(value)}。"
        )
        return

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} 不在允许值 {enum!r} 中。")
        return

    if isinstance(value, dict):
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required_names = required if isinstance(required, list) else []
        for name in required_names:
            if isinstance(name, str) and name not in value:
                errors.append(f"{path}.{name} 是必填字段。")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(property_map))
            if unknown:
                errors.append(
                    f"{path} 包含未声明字段：{', '.join(unknown[:8])}。"
                )
        for key, item in value.items():
            child_schema = property_map.get(key)
            if isinstance(child_schema, dict):
                _validate_value(
                    item,
                    child_schema,
                    f"{path}.{key}",
                    errors,
                    depth=depth + 1,
                )
        return

    if isinstance(value, list):
        minimum_items = _safe_number(schema.get("minItems"))
        maximum_items = _safe_number(schema.get("maxItems"))
        if minimum_items is not None and len(value) < minimum_items:
            errors.append(f"{path} 至少需要 {int(minimum_items)} 项。")
        if maximum_items is not None and len(value) > maximum_items:
            errors.append(f"{path} 最多允许 {int(maximum_items)} 项。")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value[:100]):
                _validate_value(
                    item,
                    item_schema,
                    f"{path}[{index}]",
                    errors,
                    depth=depth + 1,
                )
        return

    if isinstance(value, str):
        minimum_length = _safe_number(schema.get("minLength"))
        maximum_length = _safe_number(schema.get("maxLength"))
        if minimum_length is not None and len(value) < minimum_length:
            errors.append(f"{path} 至少需要 {int(minimum_length)} 个字符。")
        if maximum_length is not None and len(value) > maximum_length:
            errors.append(f"{path} 最多允许 {int(maximum_length)} 个字符。")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matched = re.search(pattern, value) is not None
            except re.error:
                errors.append(f"{path} 的宿主 schema 正则无效。")
            else:
                if not matched:
                    errors.append(f"{path} 格式不符合宿主 schema。")
        return

    if _is_number(value):
        minimum = _safe_number(schema.get("minimum"))
        maximum = _safe_number(schema.get("maximum"))
        if minimum is not None and value < minimum:
            errors.append(f"{path} 不能小于 {minimum:g}。")
        if maximum is not None and value > maximum:
            errors.append(f"{path} 不能大于 {maximum:g}。")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _is_number(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_number(value: Any) -> float | None:
    if not _is_number(value):
        return None
    return float(value)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__
