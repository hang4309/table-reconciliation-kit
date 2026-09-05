from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .errors import IntegrityError, ReviewError


DETERMINISTIC_TIME = "1970-01-01T00:00:00Z"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def canonical_json(value: Any, *, pretty: bool = False) -> str:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "default": json_default,
    }
    if pretty:
        options.update(indent=2)
    else:
        options.update(separators=(",", ":"))
    return json.dumps(value, **options) + ("\n" if pretty else "")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_name(value: str, label: str = "name") -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label} must match {SAFE_NAME.pattern}")
    return value


def formula_safe(value: Any) -> Any:
    """Make untrusted text inert in spreadsheet-oriented exports.

    Numeric values stay numeric. Text beginning with a spreadsheet control
    character, including leading whitespace tricks, is prefixed by an
    apostrophe. The untouched value remains available in state/report JSON.
    """

    if not isinstance(value, str) or not value:
        return value
    stripped = value
    while True:
        reduced = stripped.lstrip().lstrip("\ufeff\u200b\u2060")
        if reduced == stripped:
            break
        stripped = reduced
    if value[0] in "\t\r\n" or (stripped and stripped[0] in "=+-@"):
        return "'" + value
    return value


def normalize_text(value: Any, operations: list[str] | None = None) -> str:
    text = "" if value is None else str(value)
    operations = operations or ["nfkc", "strip", "casefold", "alnum"]
    for operation in operations:
        if operation == "nfkc":
            text = unicodedata.normalize("NFKC", text)
        elif operation == "strip":
            text = text.strip()
        elif operation == "casefold":
            text = text.casefold()
        elif operation == "upper":
            text = text.upper()
        elif operation == "digits":
            text = "".join(char for char in text if char.isdigit())
        elif operation == "alnum":
            text = "".join(char for char in text if char.isalnum())
        elif operation == "collapse_space":
            text = " ".join(text.split())
        elif operation == "none":
            pass
        else:
            raise ValueError(f"unsupported normalizer: {operation}")
    return text


@contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    lock_path = run_dir / ".review.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ReviewError(f"run is locked by another operation: {lock_path}") from exc
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def verify_within(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise IntegrityError(f"path escapes workspace: {candidate}") from exc
    return candidate
