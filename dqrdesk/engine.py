from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from . import __version__
from .audit import entity_state_hash, initial_event, read_events, verify_events, write_initial
from .config import load_config
from .errors import IntegrityError, ValidationError
from .exporters import export_to_directory, report_payload
from .ingest import ingest
from .invariants import compute_invariants, summarize
from .matching import build_entities
from .util import DETERMINISTIC_TIME, atomic_write_text, canonical_json, formula_safe, sha256_file, stable_hash


STATE_FILE = "state.json"


def _review_transaction_paths(run_path: Path) -> tuple[Path, Path]:
    return (
        run_path.parent / f".{run_path.name}.review-backup",
        run_path.parent / f".{run_path.name}.review-journal.json",
    )


def _state_hash_from_directory(directory: Path) -> str:
    try:
        state = json.loads((directory / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"cannot inspect recovery state in {directory}: {exc}") from exc
    return entity_state_hash(state)


def recover_review_transaction(run_dir: str | Path) -> dict[str, Any] | None:
    """Recover the narrow two-rename window of a review directory swap.

    A verified successor is staged before a journal is written. On restart we
    either restore the old backup when no live run exists, or finish cleanup
    when the live run has the journal's intended state hash.
    """

    run_path = Path(run_dir).resolve()
    backup, journal = _review_transaction_paths(run_path)
    if not journal.exists():
        if not run_path.exists() and backup.exists():
            os.replace(backup, run_path)
            return {"recovered": True, "outcome": "restored_backup_without_journal"}
        if run_path.exists() and backup.exists():
            raise IntegrityError(f"orphaned review backup requires inspection: {backup}")
        return None
    try:
        transaction = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid review transaction journal: {journal}") from exc
    if transaction.get("version") != 1:
        raise IntegrityError("unsupported review transaction journal version")
    staging_name = transaction.get("staging_name")
    if not isinstance(staging_name, str) or not staging_name.startswith(f".{run_path.name}.review-staging-") or Path(staging_name).name != staging_name:
        raise IntegrityError("unsafe staging name in review transaction journal")
    staging = run_path.parent / staging_name
    before_hash = transaction.get("before_entity_state_hash")
    after_hash = transaction.get("after_entity_state_hash")

    def cleanup_staging() -> None:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    if not run_path.exists():
        if not backup.exists():
            raise IntegrityError("review recovery has neither live run nor backup")
        if _state_hash_from_directory(backup) != before_hash:
            raise IntegrityError("review backup state hash does not match journal")
        os.replace(backup, run_path)
        cleanup_staging()
        journal.unlink(missing_ok=True)
        return {"recovered": True, "outcome": "rolled_back_to_previous_run"}

    current_hash = _state_hash_from_directory(run_path)
    if current_hash == after_hash:
        if backup.exists():
            if _state_hash_from_directory(backup) != before_hash:
                raise IntegrityError("review backup state hash does not match journal")
            shutil.rmtree(backup)
        cleanup_staging()
        journal.unlink(missing_ok=True)
        return {"recovered": True, "outcome": "completed_installed_run"}
    if current_hash == before_hash:
        if backup.exists():
            if _state_hash_from_directory(backup) != before_hash:
                raise IntegrityError("unexpected backup during review recovery")
            shutil.rmtree(backup)
        cleanup_staging()
        journal.unlink(missing_ok=True)
        return {"recovered": True, "outcome": "kept_previous_run"}
    raise IntegrityError("live run state matches neither side of the review transaction journal")


def _run_id(config_hash: str, input_manifest: list[dict[str, Any]]) -> str:
    material = {
        "tool_version": __version__,
        "config_hash": config_hash,
        "inputs": [{"source_id": item["source_id"], "sha256": item["sha256"]} for item in input_manifest],
    }
    return stable_hash(material)[:20]


def prepare_state(config_path: str | Path) -> dict[str, Any]:
    loaded = load_config(config_path)
    records, input_manifest, issues = ingest(loaded)
    entities = build_entities(loaded.data, records)
    state: dict[str, Any] = {
        "state_version": 1,
        "tool_version": __version__,
        "run_id": _run_id(loaded.config_hash, input_manifest),
        "created_at": DETERMINISTIC_TIME,
        "config_hash": loaded.config_hash,
        "config": loaded.data,
        "input_manifest": input_manifest,
        "records": records,
        "entities": entities,
        "issues": issues,
        "batches": [],
    }
    critical = [item for item in compute_invariants(state) if item["kind"] != "balance" and not item["passed"]]
    if critical:
        raise IntegrityError(f"internal reconciliation invariant failed: {critical}")
    return state


def run_reconciliation(config_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Validate completely in memory/staging, then atomically publish a new run."""

    output = Path(output_path).resolve()
    if output.exists():
        raise ValidationError(f"output already exists; refusing to overwrite: {output}")
    # All user input is parsed, typed, matched, and checked before the output
    # parent is touched. This is part of the invalid-input atomicity contract.
    state = prepare_state(config_path)
    parent_existed = output.parent.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        event = initial_event(state)
        atomic_write_text(staging / STATE_FILE, canonical_json(state, pretty=True))
        atomic_write_text(staging / "config.resolved.json", canonical_json(state["config"], pretty=True))
        atomic_write_text(staging / "input_manifest.json", canonical_json(state["input_manifest"], pretty=True))
        write_initial(staging / "audit.ndjson", event)
        export_to_directory(state, [event], staging / "reports")
        verification = verify_run(staging, check_reports=True)
        if not verification["critical_invariants_passed"]:
            raise IntegrityError("critical invariants did not pass before publication")
        os.replace(staging, output)
        return {
            "run_dir": str(output),
            "run_id": state["run_id"],
            "summary": summarize(state),
            "verification": verification,
        }
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if not parent_existed:
            try:
                output.parent.rmdir()
            except OSError:
                pass
        raise


def load_state(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    recover_review_transaction(run_path)
    path = run_path / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot read run state {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"run state is invalid JSON: {path}") from exc
    if not isinstance(state, dict) or state.get("state_version") != 1:
        raise IntegrityError("unsupported or invalid run state")
    return state


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    atomic_write_text(run_dir / STATE_FILE, canonical_json(state, pretty=True))


def _scan_formula_safety(reports: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(reports.glob("*.csv")):
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row_number, row in enumerate(csv.reader(handle), start=1):
                for column_number, value in enumerate(row, start=1):
                    if value and formula_safe(value) != value:
                        violations.append(f"{path.name}:{row_number}:{column_number}")
    xlsx = reports / "report.xlsx"
    if xlsx.exists():
        try:
            with zipfile.ZipFile(xlsx) as archive:
                for name in archive.namelist():
                    if name.startswith("xl/worksheets/") and b"<f" in archive.read(name):
                        violations.append(f"report.xlsx:{name}:formula-node")
        except zipfile.BadZipFile:
            violations.append("report.xlsx:invalid-archive")
    return violations


def verify_run(run_dir: str | Path, *, check_reports: bool = True) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    state = load_state(run_path)
    if stable_hash(state["config"]) != state.get("config_hash"):
        raise IntegrityError("resolved config hash does not match state")
    expected_run_id = _run_id(state["config_hash"], state["input_manifest"])
    if expected_run_id != state.get("run_id"):
        raise IntegrityError("run id does not match config/input hashes")
    record_ids = [record.get("record_id") for record in state.get("records", [])]
    if len(record_ids) != len(set(record_ids)):
        raise IntegrityError("record ids are not unique")
    events = read_events(run_path / "audit.ndjson")
    audit_status = verify_events(events)
    if not events:
        raise IntegrityError("audit ledger is empty")
    initial_payload = events[0].get("payload", {})
    if initial_payload.get("captured_records_hash") != stable_hash(state["records"]):
        raise IntegrityError("captured source records do not match the initial audit event")
    expected_input_hashes = {item["source_id"]: item["sha256"] for item in state["input_manifest"]}
    if initial_payload.get("input_hashes") != expected_input_hashes:
        raise IntegrityError("input manifest hashes do not match the initial audit event")
    last_payload = events[-1].get("payload", {})
    claimed_state_hash = last_payload.get("after_entity_state_hash", last_payload.get("entity_state_hash"))
    observed_state_hash = entity_state_hash(state)
    if claimed_state_hash != observed_state_hash:
        raise IntegrityError("audit head does not describe current entity state")
    invariants = compute_invariants(state)
    critical = [item for item in invariants if item["kind"] != "balance" and not item["passed"]]
    report_checks: dict[str, Any] = {"checked": check_reports}
    if check_reports:
        reports = run_path / "reports"
        expected_files = {
            "report.json",
            "report.html",
            "report.xlsx",
            "entities.csv",
            "source_records.csv",
            "provenance.csv",
            "issues.csv",
            "invariants.csv",
            "REPORT_CONTENT.json",
        }
        missing = sorted(name for name in expected_files if not (reports / name).is_file())
        if missing:
            raise IntegrityError(f"missing reports: {', '.join(missing)}")
        expected_json = canonical_json(report_payload(state, events), pretty=True)
        observed_json = (reports / "report.json").read_text(encoding="utf-8")
        if expected_json != observed_json:
            raise IntegrityError("report.json is stale or not reproducible from state + ledger")
        formula_violations = _scan_formula_safety(reports)
        if formula_violations:
            raise IntegrityError(f"formula safety violations: {formula_violations}")
        comparison_root = Path(tempfile.mkdtemp(prefix=".verify-reports-", dir=run_path))
        shutil.rmtree(comparison_root)
        try:
            export_to_directory(state, events, comparison_root)
            stale = sorted(
                name
                for name in expected_files
                if (reports / name).read_bytes() != (comparison_root / name).read_bytes()
            )
            if stale:
                raise IntegrityError(f"stale or substituted deterministic reports: {', '.join(stale)}")
        finally:
            if comparison_root.exists():
                shutil.rmtree(comparison_root, ignore_errors=True)
        report_checks = {
            "checked": True,
            "file_count": len(expected_files),
            "byte_reproduced_file_count": len(expected_files),
            "formula_safety_violations": formula_violations,
            "report_json_sha256": sha256_file(reports / "report.json"),
            "report_xlsx_sha256": sha256_file(reports / "report.xlsx"),
        }
    return {
        "run_id": state["run_id"],
        "entity_state_hash": observed_state_hash,
        "source_row_count": len(state["records"]),
        "entity_count": len(state["entities"]),
        "critical_invariants_passed": not critical,
        "invariants": invariants,
        "audit": audit_status,
        "reports": report_checks,
    }


def validate_only(config_path: str | Path) -> dict[str, Any]:
    state = prepare_state(config_path)
    return {
        "valid": True,
        "run_id": state["run_id"],
        "summary": summarize(state),
        "input_manifest": state["input_manifest"],
    }
