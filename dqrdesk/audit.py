from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .errors import IntegrityError
from .util import DETERMINISTIC_TIME, canonical_json, stable_hash, utc_now


GENESIS_HASH = "0" * 64


def make_event(
    sequence: int,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
    previous_hash: str,
    *,
    batch_id: str | None = None,
    deterministic: bool = False,
) -> dict[str, Any]:
    event = {
        "sequence": sequence,
        "event_type": event_type,
        "batch_id": batch_id,
        "actor": actor,
        "occurred_at": DETERMINISTIC_TIME if deterministic else utc_now(),
        "payload": payload,
        "previous_hash": previous_hash,
    }
    event["event_hash"] = stable_hash(event)
    return event


def initial_event(state: dict[str, Any]) -> dict[str, Any]:
    return make_event(
        1,
        "run_created",
        "system",
        {
            "run_id": state["run_id"],
            "config_hash": state["config_hash"],
            "input_hashes": {item["source_id"]: item["sha256"] for item in state["input_manifest"]},
            "source_row_count": len(state["records"]),
            "captured_records_hash": stable_hash(state["records"]),
            "entity_state_hash": entity_state_hash(state),
        },
        GENESIS_HASH,
        deterministic=True,
    )


def entity_state_hash(state: dict[str, Any]) -> str:
    # Hash the complete entity representation, including match evidence and
    # field-level provenance—not just members and selected values.
    material = sorted(state["entities"], key=lambda item: item["entity_id"])
    return stable_hash(material)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise IntegrityError(f"blank audit line at {line_number}")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise IntegrityError(f"invalid audit JSON at line {line_number}") from exc
                events.append(event)
    except OSError as exc:
        raise IntegrityError(f"cannot read audit ledger: {exc}") from exc
    return events


def verify_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    previous = GENESIS_HASH
    for index, event in enumerate(events, start=1):
        if event.get("sequence") != index:
            raise IntegrityError(f"audit sequence mismatch at event {index}")
        if event.get("previous_hash") != previous:
            raise IntegrityError(f"audit chain mismatch at event {index}")
        material = dict(event)
        claimed = material.pop("event_hash", None)
        observed = stable_hash(material)
        if claimed != observed:
            raise IntegrityError(f"audit event hash mismatch at event {index}")
        previous = claimed
    return {"event_count": len(events), "head_hash": previous, "valid": True}


def append_event(path: Path, event: dict[str, Any]) -> None:
    existing = read_events(path)
    status = verify_events(existing)
    if event.get("sequence") != len(existing) + 1 or event.get("previous_hash") != status["head_hash"]:
        raise IntegrityError("new audit event does not extend the current ledger")
    line = canonical_json(event) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def write_initial(path: Path, event: dict[str, Any]) -> None:
    if path.exists():
        raise IntegrityError(f"refusing to replace existing audit ledger: {path}")
    path.write_text(canonical_json(event) + "\n", encoding="utf-8", newline="")
