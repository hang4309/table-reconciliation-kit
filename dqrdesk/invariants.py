from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any


def compute_invariants(state: dict[str, Any]) -> list[dict[str, Any]]:
    records = state["records"]
    entities = state["entities"]
    config = state["config"]
    input_ids = [record["record_id"] for record in records]
    represented_ids = [record_id for entity in entities for record_id in entity["member_record_ids"]]
    input_counter = Counter(input_ids)
    represented_counter = Counter(represented_ids)
    results: list[dict[str, Any]] = []

    def add(name: str, passed: bool, expected: Any, observed: Any, kind: str = "count") -> None:
        results.append(
            {
                "name": name,
                "kind": kind,
                "passed": bool(passed),
                "expected": expected,
                "observed": observed,
            }
        )

    add("source_row_count", len(represented_ids) == len(input_ids), len(input_ids), len(represented_ids))
    missing = sorted((input_counter - represented_counter).elements())
    extra = sorted((represented_counter - input_counter).elements())
    duplicated = sorted(record_id for record_id, count in represented_counter.items() if count > 1)
    add(
        "record_coverage_exactly_once",
        not missing and not extra and not duplicated and represented_counter == input_counter,
        {"missing": [], "extra": [], "duplicated": []},
        {"missing": missing, "extra": extra, "duplicated": duplicated},
    )
    allowed = {"matched", "unmatched", "ambiguous", "conflict"}
    bad_status = sorted({entity["status"] for entity in entities} - allowed)
    add("classification_vocabulary", not bad_status, sorted(allowed), bad_status)
    entity_member_count = sum(entity["member_count"] for entity in entities)
    add("entity_member_count", entity_member_count == len(records), len(records), entity_member_count)

    record_by_id = {record["record_id"]: record for record in records}
    for field in config["amount_fields"]:
        input_totals: dict[str, Decimal] = defaultdict(Decimal)
        represented_totals: dict[str, Decimal] = defaultdict(Decimal)
        for record in records:
            value = record["values"].get(field)
            if value is not None:
                input_totals[record["source_id"]] += Decimal(value)
        for record_id in represented_ids:
            record = record_by_id.get(record_id)
            if record is None:
                continue
            value = record["values"].get(field)
            if value is not None:
                represented_totals[record["source_id"]] += Decimal(value)
        all_sources = [source["id"] for source in config["sources"]]
        expected = {source: format(input_totals[source], "f") for source in all_sources}
        observed = {source: format(represented_totals[source], "f") for source in all_sources}
        add(f"amount_preservation:{field}", expected == observed, expected, observed, "amount_preservation")

    for check in config["balance_checks"]:
        totals: dict[str, Decimal] = {}
        for source in check["sources"]:
            total = Decimal("0")
            for record in records:
                if record["source_id"] == source and record["values"].get(check["field"]) is not None:
                    total += Decimal(record["values"][check["field"]])
            totals[source] = total
        high = max(totals.values())
        low = min(totals.values())
        difference = high - low
        tolerance = Decimal(check["tolerance"])
        add(
            f"balance:{check['name']}",
            difference <= tolerance,
            {"maximum_difference": format(tolerance, "f")},
            {"totals": {key: format(value, "f") for key, value in totals.items()}, "difference": format(difference, "f")},
            "balance",
        )
    return results


def summarize(state: dict[str, Any], invariants: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    invariants = invariants if invariants is not None else compute_invariants(state)
    status_counts = Counter(entity["status"] for entity in state["entities"])
    review_queue = sum(
        1
        for entity in state["entities"]
        if entity["status"] in {"unmatched", "ambiguous", "conflict"} and entity.get("review_disposition") != "ignored"
    )
    return {
        "source_count": len(state["config"]["sources"]),
        "source_row_count": len(state["records"]),
        "entity_count": len(state["entities"]),
        "status_counts": {name: status_counts.get(name, 0) for name in ["matched", "unmatched", "ambiguous", "conflict"]},
        "review_queue_count": review_queue,
        "invariants_passed": sum(1 for item in invariants if item["passed"]),
        "invariants_failed": sum(1 for item in invariants if not item["passed"]),
        "quality_state": "pass" if all(item["passed"] for item in invariants) else "attention_required",
    }

