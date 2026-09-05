from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import LoadedConfig
from .errors import ValidationError
from .ooxml import read_xlsx
from .util import sha256_file


MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_ROWS = 250_000


def _read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration as exc:
                raise ValidationError(f"CSV is empty: {path}") from exc
            headers = [header.strip() for header in headers]
            _validate_headers(headers, path)
            rows: list[dict[str, Any]] = []
            for line_number, values in enumerate(reader, start=2):
                if len(values) != len(headers):
                    raise ValidationError(
                        f"CSV row {line_number} in {path.name} has {len(values)} cells; expected {len(headers)}"
                    )
                rows.append(dict(zip(headers, values)))
                if len(rows) > MAX_ROWS:
                    raise ValidationError(f"source exceeds {MAX_ROWS} row limit: {path}")
            return rows
    except UnicodeDecodeError as exc:
        raise ValidationError(f"CSV must be UTF-8: {path}") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read CSV {path}: {exc}") from exc


def _validate_headers(headers: list[str], path: Path) -> None:
    if not headers or any(not header for header in headers):
        raise ValidationError(f"source has an empty header: {path}")
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise ValidationError(f"source has duplicate headers ({', '.join(duplicates)}): {path}")


def _read_json(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValidationError(f"JSON must be UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON {path.name}: line {exc.lineno}, column {exc.colno}") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read JSON {path}: {exc}") from exc
    if isinstance(value, dict) and set(value) == {"records"}:
        value = value["records"]
    if not isinstance(value, list):
        raise ValidationError(f"JSON source must be an array or {{\"records\": [...]}}: {path}")
    if len(value) > MAX_ROWS:
        raise ValidationError(f"source exceeds {MAX_ROWS} row limit: {path}")
    if any(not isinstance(item, dict) for item in value):
        raise ValidationError(f"every JSON record must be an object: {path}")
    return value


def _read_source(path: Path, source_format: str) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValidationError(f"cannot stat source {path}: {exc}") from exc
    if not path.is_file():
        raise ValidationError(f"source is not a file: {path}")
    if size > MAX_SOURCE_BYTES:
        raise ValidationError(f"source exceeds 50 MiB limit: {path}")
    if source_format == "csv":
        return _read_csv(path)
    if source_format == "json":
        return _read_json(path)
    if source_format == "xlsx":
        rows = read_xlsx(path)
        if len(rows) > MAX_ROWS:
            raise ValidationError(f"source exceeds {MAX_ROWS} row limit: {path}")
        return rows
    raise ValidationError(f"unsupported source format: {source_format}")


def _typed(value: Any, field: dict[str, Any], source_id: str, row_number: int) -> Any:
    field_type = field["type"]
    if value is None or value == "":
        return None
    if field_type == "string":
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return str(value)
    if field_type == "decimal":
        try:
            result = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"{source_id} row {row_number} field {field['name']} is not decimal") from exc
        if not result.is_finite():
            raise ValidationError(f"{source_id} row {row_number} field {field['name']} is not finite")
        normalized = result.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if field_type == "integer":
        try:
            result = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValidationError(f"{source_id} row {row_number} field {field['name']} is not integer") from exc
        if not result.is_finite() or result != result.to_integral_value():
            raise ValidationError(f"{source_id} row {row_number} field {field['name']} is not integer")
        return str(int(result))
    if field_type == "boolean":
        normalized = str(value).strip().casefold()
        if normalized in {"1", "true", "yes", "y"}:
            return "true"
        if normalized in {"0", "false", "no", "n"}:
            return "false"
        raise ValidationError(f"{source_id} row {row_number} field {field['name']} is not boolean")
    if field_type == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError as exc:
            raise ValidationError(f"{source_id} row {row_number} field {field['name']} must be YYYY-MM-DD") from exc
    raise AssertionError(field_type)


def ingest(loaded: LoadedConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return records, deterministic input manifest, and mapping-quality issues."""

    config = loaded.data
    field_by_name = {item["name"]: item for item in config["schema"]["fields"]}
    records: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for source in config["sources"]:
        source_path = (loaded.base_dir / source["path"]).resolve()
        if not source_path.exists():
            raise ValidationError(f"source does not exist: {source_path}")
        rows = _read_source(source_path, source["format"])
        available = {key for row in rows for key in row}
        missing_columns = sorted(set(source["mapping"]) - available)
        if missing_columns:
            raise ValidationError(f"source {source['id']} misses mapped columns: {', '.join(missing_columns)}")
        source_hash = sha256_file(source_path)
        manifest.append(
            {
                "source_id": source["id"],
                "declared_path": source["path"],
                "format": source["format"],
                "sha256": source_hash,
                "row_count": len(rows),
                "byte_count": source_path.stat().st_size,
            }
        )
        inverse_mapping = {canonical: raw for raw, canonical in source["mapping"].items()}
        for row_number, raw in enumerate(rows, start=1):
            values: dict[str, Any] = {}
            for field_name, field in field_by_name.items():
                raw_column = inverse_mapping.get(field_name)
                raw_value = raw.get(raw_column) if raw_column else None
                values[field_name] = _typed(raw_value, field, source["id"], row_number)
                if field["required"] and values[field_name] is None:
                    raise ValidationError(f"{source['id']} row {row_number} required field {field_name} is empty")
            records.append(
                {
                    "record_id": f"{source['id']}:{row_number}",
                    "source_id": source["id"],
                    "source_row": row_number,
                    "source_format": source["format"],
                    "source_sha256": source_hash,
                    "raw": raw,
                    "values": values,
                }
            )
    return records, manifest, issues
