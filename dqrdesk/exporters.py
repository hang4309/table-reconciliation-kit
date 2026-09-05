from __future__ import annotations

import csv
import html
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .audit import read_events, verify_events
from .invariants import compute_invariants, summarize
from .ooxml import write_xlsx
from .util import atomic_write_text, canonical_json, formula_safe, stable_hash


STATUS_ORDER = ["matched", "unmatched", "ambiguous", "conflict"]


def report_payload(state: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    invariants = compute_invariants(state)
    return {
        "format_version": 1,
        "product": "Data Quality Reconciliation Desk",
        "tool_version": __version__,
        "run_id": state["run_id"],
        "project": state["config"]["project"],
        "config_hash": state["config_hash"],
        "input_manifest": state["input_manifest"],
        "summary": summarize(state, invariants),
        "invariants": invariants,
        "issues": state.get("issues", []),
        "entities": state["entities"],
        "source_records": state["records"],
        "batches": state.get("batches", []),
        "audit": verify_events(events),
        "reproducibility": {
            "volatile_fields_in_report": [],
            "canonicalization": "UTF-8, sorted JSON keys, deterministic entity IDs and ordering",
            "report_content_hash": "computed outside this field to avoid self-reference",
        },
    }


def _csv_text(headers: list[str], rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({header: formula_safe(row.get(header, "")) for header in headers})
    return output.getvalue()


def _entity_rows(state: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    fields = [item["name"] for item in state["config"]["schema"]["fields"]]
    headers = ["entity_id", "status", "review_disposition", "member_count", "match_method", "member_record_ids", *fields]
    rows: list[dict[str, Any]] = []
    for entity in sorted(state["entities"], key=lambda item: item["entity_id"]):
        row = {
            "entity_id": entity["entity_id"],
            "status": entity["status"],
            "review_disposition": entity.get("review_disposition", "pending"),
            "member_count": entity["member_count"],
            "match_method": entity["match"]["method"],
            "member_record_ids": "|".join(entity["member_record_ids"]),
        }
        row.update(entity["selected_values"])
        rows.append(row)
    return headers, rows


def _source_rows(state: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    fields = [item["name"] for item in state["config"]["schema"]["fields"]]
    headers = ["record_id", "source_id", "source_row", "source_format", "source_sha256", "raw_json", *fields]
    rows: list[dict[str, Any]] = []
    for record in sorted(state["records"], key=lambda item: item["record_id"]):
        row = {
            "record_id": record["record_id"],
            "source_id": record["source_id"],
            "source_row": record["source_row"],
            "source_format": record["source_format"],
            "source_sha256": record["source_sha256"],
            "raw_json": json.dumps(record["raw"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        }
        row.update(record["values"])
        rows.append(row)
    return headers, rows


def _provenance_rows(state: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    headers = [
        "entity_id",
        "status",
        "field",
        "selected_value",
        "resolution_rule",
        "decision",
        "has_disagreement",
        "record_id",
        "source_id",
        "source_value",
        "is_selected",
    ]
    rows: list[dict[str, Any]] = []
    for entity in sorted(state["entities"], key=lambda item: item["entity_id"]):
        for field in entity["field_provenance"]:
            for contributor in field["contributors"]:
                rows.append(
                    {
                        "entity_id": entity["entity_id"],
                        "status": entity["status"],
                        "field": field["field"],
                        "selected_value": field["selected_value"],
                        "resolution_rule": field["resolution_rule"],
                        "decision": field["decision"],
                        "has_disagreement": field["has_disagreement"],
                        "record_id": contributor["record_id"],
                        "source_id": contributor["source_id"],
                        "source_value": contributor["value"],
                        "is_selected": contributor["selected"],
                    }
                )
    return headers, rows


def _issue_rows(state: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    headers = ["code", "severity", "source_id", "record_id", "field", "message"]
    rows = [{header: item.get(header, "") for header in headers} for item in state.get("issues", [])]
    for entity in state["entities"]:
        for field in entity["conflict_fields"]:
            rows.append(
                {
                    "code": "FIELD_CONFLICT",
                    "severity": "review",
                    "source_id": "",
                    "record_id": "|".join(entity["member_record_ids"]),
                    "field": field,
                    "message": f"entity {entity['entity_id']} has competing values",
                }
            )
    return headers, rows


def _invariant_rows(state: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    headers = ["name", "kind", "passed", "expected_json", "observed_json"]
    rows = [
        {
            "name": item["name"],
            "kind": item["kind"],
            "passed": item["passed"],
            "expected_json": json.dumps(item["expected"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "observed_json": json.dumps(item["observed"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        }
        for item in compute_invariants(state)
    ]
    return headers, rows


def _html_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    cards = "".join(
        f'<div class="card {name}"><span>{html.escape(name)}</span><strong>{summary["status_counts"][name]}</strong></div>'
        for name in STATUS_ORDER
    )
    invariant_rows = "".join(
        "<tr>"
        f'<td>{html.escape(item["name"])}</td>'
        f'<td><span class="pill {"ok" if item["passed"] else "bad"}">{"PASS" if item["passed"] else "FAIL"}</span></td>'
        f'<td><code>{html.escape(json.dumps(item["observed"], ensure_ascii=False, sort_keys=True))}</code></td>'
        "</tr>"
        for item in payload["invariants"]
    )
    entity_rows = ""
    for entity in payload["entities"]:
        selected = html.escape(json.dumps(entity["selected_values"], ensure_ascii=False, sort_keys=True))
        evidence = html.escape(json.dumps(entity["match"]["evidence"], ensure_ascii=False, sort_keys=True))
        provenance = html.escape(json.dumps(entity["field_provenance"], ensure_ascii=False, sort_keys=True))
        entity_rows += (
            "<tr>"
            f'<td><code>{entity["entity_id"]}</code></td><td><span class="pill {entity["status"]}">{entity["status"]}</span></td>'
            f'<td>{entity["member_count"]}</td><td>{html.escape(entity["match"]["method"])}</td>'
            f'<td><details><summary>值 / 证据</summary><pre>{selected}</pre><pre>{evidence}</pre><pre>{provenance}</pre></details></td>'
            "</tr>"
        )
    return f'''<!doctype html>
<html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(payload['project'])} · 对账报告</title>
<style>
:root{{--ink:#162133;--muted:#657084;--bg:#f4f6fb;--panel:#fff;--line:#dfe4ee;--accent:#315efb;--ok:#087f5b;--warn:#b4690e;--bad:#c92a2a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1280px;margin:0 auto;padding:32px}}header{{display:flex;justify-content:space-between;gap:24px;align-items:end}}
h1{{font-size:28px;margin:0}}.meta{{color:var(--muted);font-family:ui-monospace,monospace;font-size:12px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}
.card{{background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--accent);padding:18px;border-radius:10px}}.card span{{color:var(--muted)}}.card strong{{display:block;font-size:30px}}
.card.unmatched,.card.ambiguous{{border-top-color:var(--warn)}}.card.conflict{{border-top-color:var(--bad)}}section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:16px 0;padding:20px;overflow:auto}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:12px;text-transform:uppercase}}
.pill{{display:inline-block;border-radius:999px;padding:2px 8px;background:#e9edf5}}.pill.ok,.pill.matched{{color:var(--ok);background:#dff7ed}}.pill.bad,.pill.conflict{{color:var(--bad);background:#ffe3e3}}.pill.ambiguous,.pill.unmatched{{color:var(--warn);background:#fff0d4}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f7f8fb;padding:8px;border-radius:6px;max-width:680px}}code{{font-family:ui-monospace,monospace}}@media(max-width:700px){{.cards{{grid-template-columns:1fr 1fr}}header{{display:block}}}}
</style></head><body><main>
<header><div><div class="meta">DATA QUALITY RECONCILIATION DESK</div><h1>{html.escape(payload['project'])}</h1></div><div class="meta">Run {payload['run_id']}<br>Config {payload['config_hash']}</div></header>
<div class="cards">{cards}</div>
<section><h2>质量与不变量</h2><p>源行 {summary['source_row_count']} · 实体 {summary['entity_count']} · 待人工处理 {summary['review_queue_count']} · 状态 {summary['quality_state']}</p><table><thead><tr><th>检查</th><th>结果</th><th>观测值</th></tr></thead><tbody>{invariant_rows}</tbody></table></section>
<section><h2>对账实体与字段级来源</h2><table><thead><tr><th>实体</th><th>状态</th><th>源行</th><th>匹配规则</th><th>详情</th></tr></thead><tbody>{entity_rows}</tbody></table></section>
</main></body></html>'''


def export_to_directory(state: dict[str, Any], events: list[dict[str, Any]], target: Path) -> dict[str, str]:
    target.mkdir(parents=True, exist_ok=False)
    payload = report_payload(state, events)
    entity_headers, entity_rows = _entity_rows(state)
    source_headers, source_rows = _source_rows(state)
    provenance_headers, provenance_rows = _provenance_rows(state)
    issue_headers, issue_rows = _issue_rows(state)
    invariant_headers, invariant_rows = _invariant_rows(state)
    files = {
        "report.json": canonical_json(payload, pretty=True),
        "report.html": _html_report(payload),
        "entities.csv": _csv_text(entity_headers, entity_rows),
        "source_records.csv": _csv_text(source_headers, source_rows),
        "provenance.csv": _csv_text(provenance_headers, provenance_rows),
        "issues.csv": _csv_text(issue_headers, issue_rows),
        "invariants.csv": _csv_text(invariant_headers, invariant_rows),
    }
    for name, content in files.items():
        atomic_write_text(target / name, content)
    dashboard_headers = ["metric", "value"]
    dashboard_rows = [
        {"metric": "run_id", "value": state["run_id"]},
        {"metric": "source_rows", "value": payload["summary"]["source_row_count"]},
        {"metric": "entities", "value": payload["summary"]["entity_count"]},
        {"metric": "review_queue", "value": payload["summary"]["review_queue_count"]},
        {"metric": "quality_state", "value": payload["summary"]["quality_state"]},
        *({"metric": f"status_{name}", "value": payload["summary"]["status_counts"][name]} for name in STATUS_ORDER),
    ]
    write_xlsx(
        target / "report.xlsx",
        [
            ("Dashboard", dashboard_headers, dashboard_rows),
            ("Entities", entity_headers, entity_rows),
            ("Source Records", source_headers, source_rows),
            ("Provenance", provenance_headers, provenance_rows),
            ("Issues", issue_headers, issue_rows),
            ("Invariants", invariant_headers, invariant_rows),
        ],
    )
    hashes = {name: stable_hash(content) for name, content in sorted(files.items())}
    hashes["report.xlsx"] = "binary-deterministic"
    atomic_write_text(target / "REPORT_CONTENT.json", canonical_json({"run_id": state["run_id"], "text_content_hashes": hashes}, pretty=True))
    return hashes


def refresh_reports(run_dir: Path, state: dict[str, Any]) -> None:
    events = read_events(run_dir / "audit.ndjson")
    verify_events(events)
    staging = Path(tempfile.mkdtemp(prefix=".reports-staging-", dir=run_dir))
    shutil.rmtree(staging)
    export_to_directory(state, events, staging)
    reports = run_dir / "reports"
    backup = run_dir / ".reports-backup"
    try:
        if backup.exists():
            shutil.rmtree(backup)
        if reports.exists():
            os.replace(reports, backup)
        os.replace(staging, reports)
        if backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if reports.exists() and backup.exists():
            shutil.rmtree(reports)
        if backup.exists() and not reports.exists():
            os.replace(backup, reports)
        if staging.exists():
            shutil.rmtree(staging)
        raise

