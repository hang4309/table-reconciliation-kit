from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .engine import load_state, run_reconciliation, validate_only, verify_run
from .errors import DeskError
from .invariants import compute_invariants, summarize
from .review import ignore, merge, queue, rollback, split
from .util import canonical_json


def _json(value: Any) -> None:
    sys.stdout.write(canonical_json(value, pretty=True))


def _comma_ids(value: str) -> list[str]:
    result = [item.strip() for item in value.split(",") if item.strip()]
    if not result:
        raise argparse.ArgumentTypeError("at least one id is required")
    return result


def _groups(value: str) -> list[list[str]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("groups must be JSON, e.g. [[\"a:1\"],[\"b:1\"]]") from exc
    if not isinstance(parsed, list) or any(not isinstance(group, list) for group in parsed):
        raise argparse.ArgumentTypeError("groups must be an array of arrays")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dqrdesk",
        description="Data Quality Reconciliation Desk — deterministic offline reconciliation",
    )
    parser.add_argument("--version", action="version", version=f"dqrdesk {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate config and all inputs without writing output")
    validate.add_argument("--config", required=True, type=Path)

    run = sub.add_parser("run", help="create a new reconciliation run atomically")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)

    verify = sub.add_parser("verify", help="verify state, audit chain, invariants, and reports")
    verify.add_argument("--run", required=True, type=Path)
    verify.add_argument("--skip-reports", action="store_true")

    status = sub.add_parser("status", help="show dashboard summary and queue")
    status.add_argument("--run", required=True, type=Path)

    list_queue = sub.add_parser("queue", help="list unresolved review entities")
    list_queue.add_argument("--run", required=True, type=Path)

    merge_parser = sub.add_parser("merge", help="manually merge complete entities as one reversible batch")
    merge_parser.add_argument("--run", required=True, type=Path)
    merge_parser.add_argument("--entities", required=True, type=_comma_ids)
    merge_parser.add_argument("--actor", default="cli-reviewer")
    merge_parser.add_argument("--reason", required=True)

    split_parser = sub.add_parser("split", help="split an entity into singletons or an explicit partition")
    split_parser.add_argument("--run", required=True, type=Path)
    split_parser.add_argument("--entity", required=True)
    split_parser.add_argument("--groups", type=_groups, help='JSON partition, e.g. [["erp:1"],["crm:1"]]')
    split_parser.add_argument("--actor", default="cli-reviewer")
    split_parser.add_argument("--reason", required=True)

    ignore_parser = sub.add_parser("ignore", help="remove entities from review queue without deleting data")
    ignore_parser.add_argument("--run", required=True, type=Path)
    ignore_parser.add_argument("--entities", required=True, type=_comma_ids)
    ignore_parser.add_argument("--actor", default="cli-reviewer")
    ignore_parser.add_argument("--reason", required=True)

    rollback_parser = sub.add_parser("rollback", help="roll back the latest active review batch")
    rollback_parser.add_argument("--run", required=True, type=Path)
    rollback_parser.add_argument("--batch", required=True)
    rollback_parser.add_argument("--actor", default="cli-reviewer")
    rollback_parser.add_argument("--reason", required=True)

    serve = sub.add_parser("serve", help="launch the local review desk")
    serve.add_argument("--run", required=True, type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    return parser


def dispatch(args: argparse.Namespace) -> Any:
    if args.command == "validate":
        return validate_only(args.config)
    if args.command == "run":
        return run_reconciliation(args.config, args.output)
    if args.command == "verify":
        return verify_run(args.run, check_reports=not args.skip_reports)
    if args.command == "status":
        state = load_state(args.run)
        batches = [{key: value for key, value in batch.items() if key not in {"before_entities", "after_entities"}} for batch in state["batches"]]
        return {"run_id": state["run_id"], "summary": summarize(state), "invariants": compute_invariants(state), "batches": batches}
    if args.command == "queue":
        state = load_state(args.run)
        return {"run_id": state["run_id"], "queue_count": len(queue(state)), "entities": queue(state)}
    if args.command == "merge":
        return merge(args.run, args.entities, actor=args.actor, reason=args.reason)
    if args.command == "split":
        return split(args.run, args.entity, args.groups, actor=args.actor, reason=args.reason)
    if args.command == "ignore":
        return ignore(args.run, args.entities, actor=args.actor, reason=args.reason)
    if args.command == "rollback":
        return rollback(args.run, args.batch, actor=args.actor, reason=args.reason)
    if args.command == "serve":
        from .web import serve

        serve(args.run, args.host, args.port, open_browser=not args.no_browser)
        return None
    raise AssertionError(args.command)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = dispatch(args)
        if result is not None:
            _json(result)
        return 0
    except (DeskError, ValueError, OSError) as exc:
        sys.stderr.write(canonical_json({"ok": False, "error_type": type(exc).__name__, "message": str(exc)}, pretty=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
