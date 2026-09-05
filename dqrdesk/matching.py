from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from .util import normalize_text, stable_hash


def _field_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in config["schema"]["fields"]}


def _source_priority(config: dict[str, Any]) -> dict[str, int]:
    return {item["id"]: item["priority"] for item in config["sources"]}


def _record_sort(record: dict[str, Any], config: dict[str, Any]) -> tuple[int, str]:
    return (_source_priority(config)[record["source_id"]], record["record_id"])


def _resolve_field(config: dict[str, Any], field: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    contributors = [
        {
            "record_id": record["record_id"],
            "source_id": record["source_id"],
            "value": record["values"].get(field["name"]),
            "selected": False,
        }
        for record in sorted(records, key=lambda item: _record_sort(item, config))
    ]
    nonempty = [item for item in contributors if item["value"] is not None]
    distinct: list[Any] = []
    for item in nonempty:
        if item["value"] not in distinct:
            distinct.append(item["value"])
    selected: Any = None
    decision = "all_empty"
    rule = field["resolution"]
    if len(distinct) == 1:
        selected = distinct[0]
        decision = "consensus"
    elif distinct:
        if rule in {"source_priority", "first_nonempty"}:
            selected = nonempty[0]["value"]
            decision = "selected_by_source_priority" if rule == "source_priority" else "selected_first_nonempty"
        elif rule == "consensus":
            decision = "unresolved_no_consensus"
        elif rule in {"min", "max"}:
            if field["type"] in {"decimal", "integer"}:
                selected = format((min if rule == "min" else max)(Decimal(value) for value in distinct), "f")
            else:
                selected = (min if rule == "min" else max)(distinct)
            decision = f"selected_{rule}"
    for item in contributors:
        item["selected"] = selected is not None and item["value"] == selected
    return {
        "field": field["name"],
        "selected_value": selected,
        "resolution_rule": rule,
        "decision": decision,
        "has_disagreement": len(distinct) > 1,
        "contributors": contributors,
    }


def make_entity(
    config: dict[str, Any],
    records: list[dict[str, Any]],
    status: str,
    method: str,
    evidence: dict[str, Any] | None = None,
    *,
    review_disposition: str = "pending",
) -> dict[str, Any]:
    records = sorted(records, key=lambda item: item["record_id"])
    provenance = [_resolve_field(config, field, records) for field in config["schema"]["fields"]]
    selected = {item["field"]: item["selected_value"] for item in provenance}
    conflict_fields = [
        item["field"] for item in provenance if item["field"] in config["conflict_fields"] and item["has_disagreement"]
    ]
    if status == "matched" and conflict_fields:
        status = "conflict"
    member_ids = [record["record_id"] for record in records]
    return {
        "entity_id": "ent_" + stable_hash(member_ids)[:16],
        "status": status,
        "review_disposition": review_disposition,
        "member_record_ids": member_ids,
        "member_count": len(member_ids),
        "match": {
            "method": method,
            "rule_version": "dqrdesk-match-v1",
            "evidence": evidence or {},
        },
        "selected_values": selected,
        "conflict_fields": conflict_fields,
        "field_provenance": provenance,
    }


def _key(record: dict[str, Any], fields: list[str], field_config: dict[str, dict[str, Any]], normalized: bool) -> tuple[str, ...] | None:
    values: list[str] = []
    for field in fields:
        value = record["values"].get(field)
        if value is None or value == "":
            return None
        if normalized:
            value = normalize_text(value, field_config[field]["normalizers"])
            if not value:
                return None
        else:
            value = str(value)
        values.append(value)
    return tuple(values)


def _similarity(left: str, right: str) -> tuple[Decimal, Decimal, Decimal]:
    if not left or not right:
        return Decimal("0"), Decimal("0"), Decimal("0")
    sequence = Decimal(str(SequenceMatcher(None, left, right).ratio()))
    left_tokens = " ".join(sorted(left.split()))
    right_tokens = " ".join(sorted(right.split()))
    token = Decimal(str(SequenceMatcher(None, left_tokens, right_tokens).ratio()))
    score = sequence * Decimal("0.6") + token * Decimal("0.4")
    return score, sequence, token


def fuzzy_score(config: dict[str, Any], left: dict[str, Any], right: dict[str, Any]) -> tuple[Decimal, list[dict[str, str]]]:
    fields = _field_config(config)
    total_weight = sum((Decimal(item["weight"]) for item in config["match"]["fuzzy_fields"]), Decimal("0"))
    weighted = Decimal("0")
    details: list[dict[str, str]] = []
    for item in config["match"]["fuzzy_fields"]:
        name = item["field"]
        weight = Decimal(item["weight"])
        left_value = normalize_text(left["values"].get(name), fields[name]["normalizers"])
        right_value = normalize_text(right["values"].get(name), fields[name]["normalizers"])
        score, sequence, token = _similarity(left_value, right_value)
        weighted += score * weight
        details.append(
            {
                "field": name,
                "weight": format(weight, "f"),
                "left_normalized": left_value,
                "right_normalized": right_value,
                "sequence_score": format(sequence.quantize(Decimal("0.000001")), "f"),
                "token_score": format(token.quantize(Decimal("0.000001")), "f"),
                "contribution": format((score * weight).quantize(Decimal("0.000001")), "f"),
            }
        )
    final = Decimal("0") if total_weight == 0 else weighted / total_weight
    return final.quantize(Decimal("0.000001")), details


def build_entities(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    record_by_id = {record["record_id"]: record for record in records}
    remaining = set(record_by_id)
    entities: list[dict[str, Any]] = []
    held_ambiguous: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fields = _field_config(config)

    def consume_key_rules(rules: list[list[str]], normalized: bool) -> None:
        nonlocal remaining
        method = "normalized_key" if normalized else "exact_key"
        for rule_index, key_fields in enumerate(rules, start=1):
            buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
            for record_id in sorted(remaining):
                record = record_by_id[record_id]
                key = _key(record, key_fields, fields, normalized)
                if key is not None:
                    buckets[key].append(record)
            consumed: set[str] = set()
            for key, members in sorted(buckets.items()):
                if len(members) < 2 or len({item["source_id"] for item in members}) < 2:
                    continue
                if len({item["source_id"] for item in members}) != len(members):
                    ids = [item["record_id"] for item in members]
                    for member in members:
                        held_ambiguous[member["record_id"]].append(
                            {
                                "reason": "duplicate_key_within_source",
                                "method": method,
                                "rule_index": rule_index,
                                "fields": key_fields,
                                "key": list(key),
                                "candidate_record_ids": [item for item in ids if item != member["record_id"]],
                            }
                        )
                        consumed.add(member["record_id"])
                    continue
                evidence = {"rule_index": rule_index, "fields": key_fields, "key": list(key), "normalized": normalized}
                entities.append(make_entity(config, members, "matched", method, evidence))
                consumed.update(item["record_id"] for item in members)
            remaining -= consumed

    consume_key_rules(config["match"]["exact_keys"], normalized=False)
    consume_key_rules(config["match"]["normalized_keys"], normalized=True)

    fuzzy_candidates: list[dict[str, Any]] = []
    remaining_records = [record_by_id[item] for item in sorted(remaining)]
    review_threshold = Decimal(config["match"]["review_threshold"])
    auto_threshold = Decimal(config["match"]["auto_threshold"])
    tie_margin = Decimal(config["match"]["tie_margin"])
    for index, left in enumerate(remaining_records):
        for right in remaining_records[index + 1 :]:
            if left["source_id"] == right["source_id"]:
                continue
            score, details = fuzzy_score(config, left, right)
            if score >= review_threshold:
                fuzzy_candidates.append(
                    {
                        "left": left["record_id"],
                        "right": right["record_id"],
                        "score_decimal": score,
                        "score": format(score, "f"),
                        "field_scores": details,
                    }
                )
    by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in fuzzy_candidates:
        by_record[candidate["left"]].append(candidate)
        by_record[candidate["right"]].append(candidate)
    for values in by_record.values():
        values.sort(key=lambda item: (-item["score_decimal"], item["left"], item["right"]))

    def other(candidate: dict[str, Any], record_id: str) -> str:
        return candidate["right"] if candidate["left"] == record_id else candidate["left"]

    eligible: list[dict[str, Any]] = []
    for candidate in fuzzy_candidates:
        left = candidate["left"]
        right = candidate["right"]
        if candidate["score_decimal"] < auto_threshold:
            continue
        left_list, right_list = by_record[left], by_record[right]
        if other(left_list[0], left) != right or other(right_list[0], right) != left:
            continue
        left_second = left_list[1]["score_decimal"] if len(left_list) > 1 else Decimal("0")
        right_second = right_list[1]["score_decimal"] if len(right_list) > 1 else Decimal("0")
        if candidate["score_decimal"] - left_second < tie_margin or candidate["score_decimal"] - right_second < tie_margin:
            continue
        eligible.append(candidate)
    eligible.sort(key=lambda item: (-item["score_decimal"], item["left"], item["right"]))
    auto_assigned: set[str] = set()
    for candidate in eligible:
        if candidate["left"] in auto_assigned or candidate["right"] in auto_assigned:
            continue
        pair = [record_by_id[candidate["left"]], record_by_id[candidate["right"]]]
        evidence = {
            "score": candidate["score"],
            "auto_threshold": config["match"]["auto_threshold"],
            "review_threshold": config["match"]["review_threshold"],
            "tie_margin": config["match"]["tie_margin"],
            "reciprocal_unique_best": True,
            "field_scores": candidate["field_scores"],
        }
        entities.append(make_entity(config, pair, "matched", "fuzzy_auto", evidence))
        auto_assigned.update([candidate["left"], candidate["right"]])
    remaining -= auto_assigned

    for record_id, reasons in sorted(held_ambiguous.items()):
        record = record_by_id[record_id]
        entities.append(make_entity(config, [record], "ambiguous", "key_ambiguity", {"candidates": reasons}))

    for record_id in sorted(remaining):
        record = record_by_id[record_id]
        candidates: list[dict[str, Any]] = []
        for candidate in by_record.get(record_id, []):
            candidates.append(
                {
                    "record_id": other(candidate, record_id),
                    "score": candidate["score"],
                    "field_scores": candidate["field_scores"],
                    "candidate_is_already_matched": other(candidate, record_id) in auto_assigned,
                }
            )
        if candidates:
            evidence = {
                "reason": "candidate_requires_human_review",
                "auto_threshold": config["match"]["auto_threshold"],
                "review_threshold": config["match"]["review_threshold"],
                "tie_margin": config["match"]["tie_margin"],
                "candidates": candidates,
            }
            entities.append(make_entity(config, [record], "ambiguous", "fuzzy_review", evidence))
        else:
            entities.append(make_entity(config, [record], "unmatched", "none", {"reason": "no_candidate_above_review_threshold"}))

    return sorted(entities, key=lambda item: item["entity_id"])


def recalculate_entity(
    config: dict[str, Any], records: list[dict[str, Any]], method: str, evidence: dict[str, Any], disposition: str = "pending"
) -> dict[str, Any]:
    status = "matched" if len(records) > 1 else "unmatched"
    return make_entity(config, records, status, method, evidence, review_disposition=disposition)

