from __future__ import annotations

from copy import deepcopy
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .audit import append_event, entity_state_hash, make_event, read_events, verify_events
from .engine import _review_transaction_paths, load_state, recover_review_transaction, save_state, verify_run
from .errors import IntegrityError, ReviewError
from .exporters import refresh_reports
from .invariants import compute_invariants
from .matching import recalculate_entity
from .util import run_lock, stable_hash, utc_now


def _entity_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entity["entity_id"]: entity for entity in state["entities"]}


def _new_batch_id(state: dict[str, Any]) -> str:
    return f"batch_{len(state.get('batches', [])) + 1:04d}"


def _critical_invariants(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in compute_invariants(state) if item["kind"] != "balance" and not item["passed"]]


def _commit(
    run_dir: Path,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    event_type: str,
    actor: str,
    batch: dict[str, Any],
) -> dict[str, Any]:
    critical = _critical_invariants(new_state)
    if critical:
        raise IntegrityError(f"review action would break critical invariants: {critical}")
    events = read_events(run_dir / "audit.ndjson")
    status = verify_events(events)
    event = make_event(
        len(events) + 1,
        event_type,
        actor,
        {
            "action": batch["action"],
            "reason": batch["reason"],
            "before_entity_ids": batch["before_entity_ids"],
            "after_entity_ids": batch["after_entity_ids"],
            "before_entity_state_hash": batch["before_entity_state_hash"],
            "after_entity_state_hash": batch["after_entity_state_hash"],
        },
        status["head_hash"],
        batch_id=batch["batch_id"],
    )
    # Construct and mechanically verify a complete successor run before any
    # live ledger/state/report byte is changed. Then swap the whole run as one
    # directory transaction. This prevents an export failure from advancing
    # the audit head while leaving stale state or reports.
    staging = Path(tempfile.mkdtemp(prefix=f".{run_dir.name}.review-staging-", dir=run_dir.parent))
    shutil.rmtree(staging)
    backup, journal = _review_transaction_paths(run_dir)
    if backup.exists() or journal.exists():
        recover_review_transaction(run_dir)
    if backup.exists() or journal.exists():
        raise IntegrityError("review transaction recovery did not clear prior artifacts")
    try:
        shutil.copytree(run_dir, staging, ignore=shutil.ignore_patterns(".review.lock", ".reports-staging-*", ".reports-backup"))
        append_event(staging / "audit.ndjson", event)
        save_state(staging, new_state)
        refresh_reports(staging, new_state)
        verify_run(staging)
        transaction = {
            "version": 1,
            "phase": "prepared",
            "run_name": run_dir.name,
            "staging_name": staging.name,
            "backup_name": backup.name,
            "before_entity_state_hash": batch["before_entity_state_hash"],
            "after_entity_state_hash": batch["after_entity_state_hash"],
        }
        from .util import atomic_write_text, canonical_json

        atomic_write_text(journal, canonical_json(transaction, pretty=True))
        os.replace(run_dir, backup)
        transaction["phase"] = "backup_created"
        atomic_write_text(journal, canonical_json(transaction, pretty=True))
        try:
            os.replace(staging, run_dir)
        except BaseException:
            os.replace(backup, run_dir)
            raise
        transaction["phase"] = "installed"
        atomic_write_text(journal, canonical_json(transaction, pretty=True))
        shutil.rmtree(backup)
        journal.unlink(missing_ok=True)
    except BaseException:
        if not run_dir.exists() and backup.exists():
            os.replace(backup, run_dir)
        elif run_dir.exists() and backup.exists():
            # If the successor reached the live name, journal-based recovery
            # can safely decide whether to finalize it or retain the old run.
            recover_review_transaction(run_dir)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if journal.exists() and run_dir.exists() and not backup.exists():
            journal.unlink(missing_ok=True)
        raise
    public_batch = {key: value for key, value in batch.items() if key not in {"before_entities", "after_entities"}}
    return {
        "batch": public_batch,
        "verification": verify_run(run_dir),
    }


def _finish_batch(
    state: dict[str, Any],
    action: str,
    actor: str,
    reason: str,
    before_entities: list[dict[str, Any]],
    after_entities: list[dict[str, Any]],
    before_hash: str,
) -> dict[str, Any]:
    batch_id = _new_batch_id(state)
    batch = {
        "batch_id": batch_id,
        "action": action,
        "actor": actor,
        "reason": reason,
        "occurred_at": utc_now(),
        "status": "active",
        "before_entity_ids": [item["entity_id"] for item in before_entities],
        "after_entity_ids": [item["entity_id"] for item in after_entities],
        "before_entities": before_entities,
        "after_entities": after_entities,
        "before_entity_state_hash": before_hash,
        "after_entity_state_hash": entity_state_hash(state),
    }
    state.setdefault("batches", []).append(batch)
    return batch


def merge(run_dir: str | Path, entity_ids: list[str], *, actor: str, reason: str) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    recover_review_transaction(run_path)
    if len(set(entity_ids)) < 2:
        raise ReviewError("merge requires at least two distinct entity ids")
    with run_lock(run_path):
        old_state = load_state(run_path)
        before_hash = entity_state_hash(old_state)
        state = deepcopy(old_state)
        entities = _entity_map(state)
        missing = sorted(set(entity_ids) - set(entities))
        if missing:
            raise ReviewError(f"unknown entity ids: {', '.join(missing)}")
        selected = [deepcopy(entities[item]) for item in sorted(set(entity_ids))]
        if any(item.get("review_disposition") == "ignored" for item in selected):
            raise ReviewError("ignored entities must be restored by rollback before merging")
        record_by_id = {record["record_id"]: record for record in state["records"]}
        records = [record_by_id[record_id] for entity in selected for record_id in entity["member_record_ids"]]
        batch_id = _new_batch_id(state)
        merged = recalculate_entity(
            state["config"],
            records,
            "manual_merge",
            {"batch_id": batch_id, "actor": actor, "reason": reason, "input_entity_ids": sorted(set(entity_ids))},
        )
        state["entities"] = sorted(
            [entity for entity in state["entities"] if entity["entity_id"] not in set(entity_ids)] + [merged],
            key=lambda item: item["entity_id"],
        )
        batch = _finish_batch(state, "merge", actor, reason, selected, [deepcopy(merged)], before_hash)
        return _commit(run_path, old_state, state, "review_batch_applied", actor, batch)


def split(
    run_dir: str | Path,
    entity_id: str,
    groups: list[list[str]] | None,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    recover_review_transaction(run_path)
    with run_lock(run_path):
        old_state = load_state(run_path)
        before_hash = entity_state_hash(old_state)
        state = deepcopy(old_state)
        entities = _entity_map(state)
        if entity_id not in entities:
            raise ReviewError(f"unknown entity id: {entity_id}")
        original = deepcopy(entities[entity_id])
        members = original["member_record_ids"]
        if len(members) < 2:
            raise ReviewError("cannot split a single-record entity")
        if groups is None:
            groups = [[member] for member in members]
        flattened = [record_id for group in groups for record_id in group]
        if len(groups) < 2 or any(not group for group in groups) or sorted(flattened) != sorted(members) or len(flattened) != len(set(flattened)):
            raise ReviewError("split groups must be a disjoint, complete partition of entity members")
        record_by_id = {record["record_id"]: record for record in state["records"]}
        batch_id = _new_batch_id(state)
        replacements = [
            recalculate_entity(
                state["config"],
                [record_by_id[record_id] for record_id in group],
                "manual_split",
                {"batch_id": batch_id, "actor": actor, "reason": reason, "source_entity_id": entity_id, "group_index": index},
            )
            for index, group in enumerate(groups, start=1)
        ]
        state["entities"] = sorted(
            [entity for entity in state["entities"] if entity["entity_id"] != entity_id] + replacements,
            key=lambda item: item["entity_id"],
        )
        batch = _finish_batch(state, "split", actor, reason, [original], deepcopy(replacements), before_hash)
        return _commit(run_path, old_state, state, "review_batch_applied", actor, batch)


def ignore(run_dir: str | Path, entity_ids: list[str], *, actor: str, reason: str) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    recover_review_transaction(run_path)
    if not entity_ids:
        raise ReviewError("ignore requires at least one entity id")
    with run_lock(run_path):
        old_state = load_state(run_path)
        before_hash = entity_state_hash(old_state)
        state = deepcopy(old_state)
        entities = _entity_map(state)
        missing = sorted(set(entity_ids) - set(entities))
        if missing:
            raise ReviewError(f"unknown entity ids: {', '.join(missing)}")
        before = [deepcopy(entities[item]) for item in sorted(set(entity_ids))]
        for entity_id in set(entity_ids):
            entities[entity_id]["review_disposition"] = "ignored"
        state["entities"] = sorted(entities.values(), key=lambda item: item["entity_id"])
        after = [deepcopy(entities[item]) for item in sorted(set(entity_ids))]
        batch = _finish_batch(state, "ignore", actor, reason, before, after, before_hash)
        return _commit(run_path, old_state, state, "review_batch_applied", actor, batch)


def rollback(run_dir: str | Path, batch_id: str, *, actor: str, reason: str) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    recover_review_transaction(run_path)
    with run_lock(run_path):
        old_state = load_state(run_path)
        before_hash = entity_state_hash(old_state)
        state = deepcopy(old_state)
        active = [batch for batch in state.get("batches", []) if batch["status"] == "active"]
        if not active:
            raise ReviewError("there is no active batch to roll back")
        latest = active[-1]
        if latest["batch_id"] != batch_id:
            raise ReviewError(f"rollback is LIFO; latest active batch is {latest['batch_id']}")
        entities = _entity_map(state)
        expected_after = {item["entity_id"]: item for item in latest["after_entities"]}
        for entity_id, snapshot in expected_after.items():
            if entity_id not in entities or stable_hash(entities[entity_id]) != stable_hash(snapshot):
                raise ReviewError(f"entity {entity_id} changed since batch; refusing unsafe rollback")
        after_ids = set(expected_after)
        restored = deepcopy(latest["before_entities"])
        state["entities"] = sorted(
            [entity for entity in state["entities"] if entity["entity_id"] not in after_ids] + restored,
            key=lambda item: item["entity_id"],
        )
        latest["status"] = "rolled_back"
        latest["rolled_back_at"] = utc_now()
        latest["rollback_actor"] = actor
        latest["rollback_reason"] = reason
        after_hash = entity_state_hash(state)
        if after_hash != latest["before_entity_state_hash"]:
            raise IntegrityError("rollback did not restore the exact pre-batch entity state")
        rollback_record = {
            "batch_id": batch_id,
            "action": "rollback",
            "actor": actor,
            "reason": reason,
            "occurred_at": utc_now(),
            "status": "completed",
            "before_entity_ids": latest["after_entity_ids"],
            "after_entity_ids": latest["before_entity_ids"],
            "before_entities": deepcopy(latest["after_entities"]),
            "after_entities": deepcopy(latest["before_entities"]),
            "before_entity_state_hash": before_hash,
            "after_entity_state_hash": after_hash,
        }
        return _commit(run_path, old_state, state, "review_batch_rolled_back", actor, rollback_record)


def queue(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entity
        for entity in state["entities"]
        if entity["status"] in {"unmatched", "ambiguous", "conflict"} and entity.get("review_disposition") != "ignored"
    ]
