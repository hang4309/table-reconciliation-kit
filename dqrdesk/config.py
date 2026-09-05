from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import safe_name, stable_hash


SUPPORTED_TYPES = {"string", "decimal", "integer", "boolean", "date"}
SUPPORTED_FORMATS = {"csv", "json", "xlsx"}
SUPPORTED_RESOLUTION = {"source_priority", "consensus", "first_nonempty", "min", "max"}
SUPPORTED_NORMALIZERS = {"nfkc", "strip", "casefold", "upper", "digits", "alnum", "collapse_space", "none"}


@dataclass(frozen=True)
class LoadedConfig:
    data: dict[str, Any]
    path: Path
    base_dir: Path
    config_hash: str


def _need_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _need_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    return value


def _score(value: Any, label: str) -> str:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{label} must be a decimal score") from exc
    if not Decimal("0") <= result <= Decimal("1"):
        raise ValidationError(f"{label} must be between 0 and 1")
    return format(result, "f")


def load_config(path: str | Path) -> LoadedConfig:
    config_path = Path(path).resolve()
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read config {config_path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"config is not valid JSON: line {exc.lineno}, column {exc.colno}") from exc
    data = validate_config(_need_dict(data, "config"))
    return LoadedConfig(data=data, path=config_path, base_dir=config_path.parent, config_hash=stable_hash(data))


def validate_config(data: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(data))
    project = result.get("project")
    if not isinstance(project, str) or not project.strip():
        raise ValidationError("project must be a non-empty string")
    result["project"] = project.strip()

    schema = _need_dict(result.get("schema"), "schema")
    fields = _need_list(schema.get("fields"), "schema.fields")
    if not fields:
        raise ValidationError("schema.fields must contain at least one field")
    names: list[str] = []
    normalized_fields: list[dict[str, Any]] = []
    for index, item in enumerate(fields):
        field = _need_dict(item, f"schema.fields[{index}]")
        name = field.get("name")
        try:
            safe_name(name, f"schema.fields[{index}].name")
        except (TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        if name in names:
            raise ValidationError(f"duplicate canonical field: {name}")
        field_type = field.get("type", "string")
        if field_type not in SUPPORTED_TYPES:
            raise ValidationError(f"unsupported type for {name}: {field_type}")
        resolution = field.get("resolution", "source_priority")
        if resolution not in SUPPORTED_RESOLUTION:
            raise ValidationError(f"unsupported resolution rule for {name}: {resolution}")
        normalizers = field.get("normalizers", ["nfkc", "strip", "casefold", "alnum"])
        if not isinstance(normalizers, list) or not normalizers or any(op not in SUPPORTED_NORMALIZERS for op in normalizers):
            raise ValidationError(f"invalid normalizers for {name}")
        normalized_fields.append(
            {
                "name": name,
                "type": field_type,
                "required": bool(field.get("required", False)),
                "resolution": resolution,
                "normalizers": normalizers,
                "label": str(field.get("label", name)),
            }
        )
        names.append(name)
    schema["fields"] = normalized_fields
    result["schema"] = schema

    match = _need_dict(result.get("match"), "match")
    exact_keys = match.get("exact_keys", [])
    normalized_keys = match.get("normalized_keys", [])
    for label, rules in (("exact_keys", exact_keys), ("normalized_keys", normalized_keys)):
        _need_list(rules, f"match.{label}")
        for index, key_fields in enumerate(rules):
            if not isinstance(key_fields, list) or not key_fields:
                raise ValidationError(f"match.{label}[{index}] must be a non-empty field array")
            if len(set(key_fields)) != len(key_fields) or any(field not in names for field in key_fields):
                raise ValidationError(f"match.{label}[{index}] contains duplicate or unknown fields")
    fuzzy_fields = _need_list(match.get("fuzzy_fields", []), "match.fuzzy_fields")
    normalized_fuzzy: list[dict[str, str]] = []
    total_weight = Decimal("0")
    for index, item in enumerate(fuzzy_fields):
        fuzzy = _need_dict(item, f"match.fuzzy_fields[{index}]")
        field = fuzzy.get("field")
        if field not in names:
            raise ValidationError(f"unknown fuzzy field: {field}")
        try:
            weight = Decimal(str(fuzzy.get("weight", "1")))
        except InvalidOperation as exc:
            raise ValidationError(f"invalid fuzzy weight for {field}") from exc
        if weight <= 0:
            raise ValidationError(f"fuzzy weight for {field} must be positive")
        total_weight += weight
        normalized_fuzzy.append({"field": field, "weight": format(weight, "f")})
    if not exact_keys and not normalized_keys and not fuzzy_fields:
        raise ValidationError("at least one exact, normalized, or fuzzy match rule is required")
    match["exact_keys"] = exact_keys
    match["normalized_keys"] = normalized_keys
    match["fuzzy_fields"] = normalized_fuzzy
    match["auto_threshold"] = _score(match.get("auto_threshold", "0.92"), "match.auto_threshold")
    match["review_threshold"] = _score(match.get("review_threshold", "0.72"), "match.review_threshold")
    match["tie_margin"] = _score(match.get("tie_margin", "0.03"), "match.tie_margin")
    if Decimal(match["review_threshold"]) > Decimal(match["auto_threshold"]):
        raise ValidationError("match.review_threshold cannot exceed auto_threshold")
    result["match"] = match

    conflict_fields = result.get("conflict_fields", [])
    amount_fields = result.get("amount_fields", [])
    for label, values in (("conflict_fields", conflict_fields), ("amount_fields", amount_fields)):
        if not isinstance(values, list) or len(values) != len(set(values)) or any(value not in names for value in values):
            raise ValidationError(f"{label} must contain unique canonical field names")
    for field in amount_fields:
        field_type = next(item["type"] for item in normalized_fields if item["name"] == field)
        if field_type not in {"decimal", "integer"}:
            raise ValidationError(f"amount field {field} must use decimal or integer type")
    result["conflict_fields"] = conflict_fields
    result["amount_fields"] = amount_fields

    sources = _need_list(result.get("sources"), "sources")
    if not 2 <= len(sources) <= 4:
        raise ValidationError("sources must contain between 2 and 4 items")
    source_ids: list[str] = []
    normalized_sources: list[dict[str, Any]] = []
    required_fields = {item["name"] for item in normalized_fields if item["required"]}
    match_fields = {field for group in exact_keys + normalized_keys for field in group}
    for item in fuzzy_fields:
        match_fields.add(item["field"])
    for index, item in enumerate(sources):
        source = _need_dict(item, f"sources[{index}]")
        source_id = source.get("id")
        try:
            safe_name(source_id, f"sources[{index}].id")
        except (TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        if source_id in source_ids:
            raise ValidationError(f"duplicate source id: {source_id}")
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path:
            raise ValidationError(f"sources[{index}].path must be non-empty")
        source_format = source.get("format", Path(source_path).suffix.lstrip(".").lower())
        if source_format not in SUPPORTED_FORMATS:
            raise ValidationError(f"unsupported source format: {source_format}")
        mapping = _need_dict(source.get("mapping"), f"mapping for {source_id}")
        if not mapping or any(not isinstance(key, str) or not key for key in mapping):
            raise ValidationError(f"mapping for {source_id} has an invalid source column")
        targets = list(mapping.values())
        if len(targets) != len(set(targets)) or any(target not in names for target in targets):
            raise ValidationError(f"mapping for {source_id} has duplicate or unknown canonical fields")
        missing = sorted((required_fields | match_fields) - set(targets))
        if missing:
            raise ValidationError(f"mapping for {source_id} misses required/match fields: {', '.join(missing)}")
        priority = source.get("priority", index + 1)
        if not isinstance(priority, int) or priority < 1:
            raise ValidationError(f"priority for {source_id} must be a positive integer")
        normalized_sources.append(
            {
                "id": source_id,
                "label": str(source.get("label", source_id)),
                "path": source_path.replace("\\", "/"),
                "format": source_format,
                "mapping": mapping,
                "priority": priority,
            }
        )
        source_ids.append(source_id)
    result["sources"] = normalized_sources

    checks = _need_list(result.get("balance_checks", []), "balance_checks")
    normalized_checks: list[dict[str, Any]] = []
    check_names: set[str] = set()
    for index, item in enumerate(checks):
        check = _need_dict(item, f"balance_checks[{index}]")
        name = check.get("name")
        try:
            safe_name(name, f"balance_checks[{index}].name")
        except (TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        if name in check_names:
            raise ValidationError(f"duplicate balance check: {name}")
        field = check.get("field")
        if field not in amount_fields:
            raise ValidationError(f"balance check {name} references a non-amount field")
        selected_sources = check.get("sources", source_ids)
        if not isinstance(selected_sources, list) or len(selected_sources) < 2 or any(source not in source_ids for source in selected_sources):
            raise ValidationError(f"balance check {name} must reference at least two known sources")
        try:
            tolerance = Decimal(str(check.get("tolerance", "0")))
        except InvalidOperation as exc:
            raise ValidationError(f"invalid tolerance for balance check {name}") from exc
        if tolerance < 0:
            raise ValidationError(f"tolerance for {name} cannot be negative")
        normalized_checks.append({"name": name, "field": field, "sources": selected_sources, "tolerance": format(tolerance, "f")})
        check_names.add(name)
    result["balance_checks"] = normalized_checks
    result["config_version"] = 1
    return result

