from __future__ import annotations

from typing import Any


SEVERITY_POTENTIAL = {
    "primary_key_changed": 1.00,
    "grain_changed": 0.95,
    "required_column_removed": 0.90,
    "metric_formula_changed": 0.85,
    "join_cardinality": 0.75,
    "join_grain_or_fanout": 0.75,
    "enum_domain_closure": 0.70,
    "null_default_drift": 0.60,
    "type_changed": 0.55,
    "filter_changed": 0.50,
    "materialization_changed": 0.35,
    "freshness_changed": 0.30,
    "column_added": 0.10,
}

SENSITIVITY_WEIGHT = {
    "REVENUE_CRITICAL": 1.00,
    "CUSTOMER_FACING": 0.80,
    "OPERATIONAL": 0.60,
    "ANALYTICAL": 0.35,
    "EXPERIMENTAL": 0.10,
    "UNKNOWN": 0.30,
    "BOARD_CRITICAL": 1.00,
    "EXEC_CRITICAL": 1.00,
    "INTERNAL_HIGH": 0.60,
    "INTERNAL_LOW": 0.35,
}

CHANGE_CLASSIFICATION = {
    "enum_domain_closure": "semantic",
    "join_cardinality": "structural",
    "join_grain_or_fanout": "structural",
    "null_default_drift": "semantic",
    "metric_formula_change": "semantic",
    "grain_change": "structural",
    "primary_key_changed": "structural",
    "type_changed": "semantic",
    "filter_changed": "semantic",
    "materialization_changed": "operational",
    "freshness_changed": "operational",
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _finding_dict(finding: Any) -> dict[str, Any]:
    if isinstance(finding, dict):
        return finding
    if hasattr(finding, "to_dict"):
        try:
            return finding.to_dict()
        except Exception:
            pass
    if hasattr(finding, "__dict__"):
        return dict(finding.__dict__)
    return {}


def _changed_resource_ids(finding: dict[str, Any], changed_resources: list[str] | None = None) -> list[str]:
    out: list[str] = []
    for item in finding.get("changed_resources") or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("unique_id"):
            out.append(str(item["unique_id"]))

    for item in changed_resources or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("unique_id"):
            out.append(str(item["unique_id"]))

    source = finding.get("source_resource")
    if source:
        out.append(str(source))

    source_obj = finding.get("source") or {}
    if isinstance(source_obj, dict) and source_obj.get("unique_id"):
        out.append(str(source_obj["unique_id"]))

    seen = set()
    stable = []
    for uid in out:
        if uid and uid not in seen:
            seen.add(uid)
            stable.append(uid)
    return stable


def _family(finding: dict[str, Any]) -> str:
    family = str(finding.get("family") or finding.get("pattern_type") or "").strip()
    if family == "join_cardinality":
        return "join_cardinality"
    return family


def _has_change_signal(finding: dict[str, Any]) -> tuple[bool, str, float, str]:
    family = _family(finding)
    assumption_diff = finding.get("assumption_diff") or {}
    trigger_evidence = finding.get("trigger_evidence") or []
    evidence_excerpt = finding.get("evidence_excerpt") or ""
    confidence = str(finding.get("confidence") or "").lower()

    if assumption_diff and assumption_diff.get("has_explicit_before_after_diff"):
        conf = 0.85 if confidence == "high" else 0.65 if confidence == "low" else 0.75
        prop = str(assumption_diff.get("pattern_type") or assumption_diff.get("drift_type") or family)
        return True, prop, conf, "assumption_diff_before_after"

    if trigger_evidence:
        conf = 0.70 if confidence == "high" else 0.50 if confidence == "low" else 0.60
        return True, family or "changed_property", conf, "trigger_evidence"

    if evidence_excerpt:
        conf = 0.55 if confidence != "low" else 0.40
        return True, family or "changed_property", conf, "static_evidence_excerpt"

    return False, family or "unknown", 0.0, "no_change_signal"


def _blast_nodes(finding: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for node in finding.get("blast_radius") or []:
        if isinstance(node, dict):
            nodes.append(node)
    return nodes


def _highest_sensitivity_from_nodes(nodes: list[dict[str, Any]]) -> tuple[str, float, dict[str, Any] | None]:
    best_label = "UNKNOWN"
    best_weight = SENSITIVITY_WEIGHT["UNKNOWN"]
    best_node = None

    for node in nodes:
        label = str(
            node.get("business_severity")
            or node.get("sensitivity")
            or node.get("criticality")
            or node.get("metadata", {}).get("business_severity")
            or "UNKNOWN"
        ).upper()

        if label == "HIGH":
            label = "REVENUE_CRITICAL"
        elif label == "MEDIUM":
            label = "OPERATIONAL"
        elif label == "LOW":
            label = "ANALYTICAL"

        weight = SENSITIVITY_WEIGHT.get(label, SENSITIVITY_WEIGHT["UNKNOWN"])
        if weight > best_weight or best_node is None:
            best_label = label
            best_weight = weight
            best_node = node

    return best_label, best_weight, best_node


def _snapshot_models(repo_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not repo_snapshot:
        return {}
    models = repo_snapshot.get("models") or {}
    return models if isinstance(models, dict) else {}


def _snapshot_dependency_signal(
    finding: dict[str, Any],
    repo_snapshot: dict[str, Any] | None,
    changed_resources: list[str] | None,
) -> tuple[bool, str, float, list[str], str, int]:
    models = _snapshot_models(repo_snapshot)
    if not models:
        return False, "UNKNOWN", 0.0, [], "no_repo_snapshot", 999

    candidates = _changed_resource_ids(finding, changed_resources)

    best_label = "UNKNOWN"
    best_weight = SENSITIVITY_WEIGHT["UNKNOWN"]
    best_path: list[str] = []
    best_distance = 999

    for uid in candidates:
        model = models.get(uid)
        if not isinstance(model, dict):
            continue

        # Direct model sensitivity counts, but downstream is stronger.
        own_sens = model.get("sensitivity") or {}
        if isinstance(own_sens, dict):
            label = str(own_sens.get("label") or "UNKNOWN").upper()
            weight = SENSITIVITY_WEIGHT.get(label, SENSITIVITY_WEIGHT["UNKNOWN"])
            if weight > best_weight:
                best_label = label
                best_weight = weight
                best_path = [uid]
                best_distance = 0

        for downstream in model.get("downstream") or []:
            if not isinstance(downstream, dict):
                continue
            label = str(downstream.get("sensitivity") or "UNKNOWN").upper()
            weight = SENSITIVITY_WEIGHT.get(label, SENSITIVITY_WEIGHT["UNKNOWN"])
            distance = int(downstream.get("distance") or 1)
            downstream_uid = str(downstream.get("unique_id") or "")
            if weight > best_weight or (weight == best_weight and distance < best_distance):
                best_label = label
                best_weight = weight
                best_path = [uid, downstream_uid] if downstream_uid else [uid]
                best_distance = distance

        contracts = model.get("contracts") or []
        if contracts and best_weight <= SENSITIVITY_WEIGHT["UNKNOWN"]:
            best_label = str((model.get("sensitivity") or {}).get("label") or "UNKNOWN").upper()
            best_weight = max(best_weight, SENSITIVITY_WEIGHT.get(best_label, 0.30))
            best_path = [uid]
            best_distance = 0

    if best_path:
        confidence = 0.80 if best_distance <= 1 else 0.65 if best_distance <= 2 else 0.45
        return True, best_label, confidence, best_path, "repo_snapshot_downstream_or_contract", best_distance

    return False, "UNKNOWN", 0.0, [], "no_snapshot_dependency_match", 999


def _blast_dependency_signal(finding: dict[str, Any]) -> tuple[bool, str, float, list[str], str, int]:
    nodes = _blast_nodes(finding)
    if not nodes:
        return False, "UNKNOWN", 0.0, [], "no_blast_radius", 999

    label, _weight, best = _highest_sensitivity_from_nodes(nodes)
    path = []
    source = finding.get("source_resource")
    if source:
        path.append(str(source))
    if best and best.get("unique_id"):
        path.append(str(best["unique_id"]))

    return True, label, 0.85, path, "finding_blast_radius", 1


def _dependency_signal(
    finding: dict[str, Any],
    repo_snapshot: dict[str, Any] | None,
    changed_resources: list[str] | None,
) -> tuple[bool, str, float, list[str], str, int]:
    blast_present, blast_label, blast_conf, blast_path, blast_source, blast_distance = _blast_dependency_signal(finding)
    snap_present, snap_label, snap_conf, snap_path, snap_source, snap_distance = _snapshot_dependency_signal(
        finding, repo_snapshot, changed_resources
    )

    blast_weight = SENSITIVITY_WEIGHT.get(blast_label, SENSITIVITY_WEIGHT["UNKNOWN"]) if blast_present else 0.0
    snap_weight = SENSITIVITY_WEIGHT.get(snap_label, SENSITIVITY_WEIGHT["UNKNOWN"]) if snap_present else 0.0

    if blast_present and (blast_weight >= snap_weight):
        return blast_present, blast_label, blast_conf, blast_path, blast_source, blast_distance
    if snap_present:
        return snap_present, snap_label, snap_conf, snap_path, snap_source, snap_distance
    return False, "UNKNOWN", 0.0, [], "no_dependency_signal", 999


def _propagation_probability(distance: int, dependency_present: bool, source: str) -> float:
    if not dependency_present:
        return 0.20
    if distance <= 0:
        base = 0.85
    elif distance == 1:
        base = 1.00
    elif distance == 2:
        base = 0.80
    elif distance == 3:
        base = 0.60
    else:
        base = 0.35

    if "snapshot" in source and "contract" not in source and "blast" not in source:
        base *= 0.90
    return min(max(base, 0.0), 1.0)


def _evidence_strength(
    change_present: bool,
    dependency_present: bool,
    change_confidence: float,
    dependency_confidence: float,
    repo_snapshot: dict[str, Any] | None,
) -> float:
    if change_present and dependency_present:
        if repo_snapshot:
            return min(0.85, max(0.65, (change_confidence + dependency_confidence) / 2))
        return min(0.70, max(0.55, (change_confidence + dependency_confidence) / 2))

    if change_present or dependency_present:
        return min(0.45, max(change_confidence, dependency_confidence, 0.25))

    return 0.0


def _replay_multiplier(finding: dict[str, Any]) -> float:
    replay = finding.get("validation_replay") or finding.get("replay_fidelity") or {}
    if not isinstance(replay, dict):
        return 0.85

    status = str(
        replay.get("status")
        or replay.get("validation_replay_status")
        or ""
    ).lower()

    if status in {"drift_detected", "confirmed", "replay_confirmed"}:
        return 1.20
    if status in {"no_drift", "clean"}:
        return 0.50
    if status in {"failed", "error"}:
        return 0.60
    return 0.85


def _route(score: float, two_signal_confirmed: bool) -> tuple[str, str]:
    if score < 0.05:
        return "suppressed", "causality below comment threshold"
    if score < 0.15:
        return "informational", "weak causality; retained for awareness"
    if score < 0.40:
        if two_signal_confirmed and score >= 0.35:
            return "must_review", "two independent signals confirmed near review threshold"
        return "advisory", "single or moderate signal; reviewer action useful but not blocking"
    if score < 0.65:
        return "must_review", "change signal and dependency signal indicate review-worthy causal risk"
    return "must_review_escalated", "high causality score; escalation eligible"


def score_finding_causality(
    finding: Any,
    repo_snapshot: dict[str, Any] | None = None,
    changed_resources: list[str] | None = None,
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score whether a detector finding is causally relevant to repo promises.

    v1 deliberately uses coarse property matching:
    change signal from the finding itself + dependency signal from blast radius or repo snapshot.
    Later versions should require property-specific dependency contracts.
    """
    fd = _finding_dict(finding)
    family = _family(fd)

    change_present, changed_property, change_conf, change_source = _has_change_signal(fd)
    dependency_present, highest_sensitivity, dep_conf, path, dep_source, distance = _dependency_signal(
        fd, repo_snapshot, changed_resources
    )

    classification = CHANGE_CLASSIFICATION.get(family, "unknown")
    two_signal = bool(change_present and dependency_present)

    severity = SEVERITY_POTENTIAL.get(family, 0.35)
    sensitivity = SENSITIVITY_WEIGHT.get(highest_sensitivity, SENSITIVITY_WEIGHT["UNKNOWN"])
    propagation = _propagation_probability(distance, dependency_present, dep_source)
    evidence = _evidence_strength(change_present, dependency_present, change_conf, dep_conf, repo_snapshot)
    replay = _replay_multiplier(fd)

    raw = propagation * severity * sensitivity * evidence * replay
    raw = min(max(raw, 0.0), 1.0)

    false_positive_rate = 0.0
    if calibration:
        family_cal = calibration.get(family) if isinstance(calibration, dict) else None
        if isinstance(family_cal, dict):
            false_positive_rate = float(family_cal.get("false_positive_rate") or 0.0)

    adjusted = raw * (1.0 - min(max(false_positive_rate, 0.0), 0.95))
    adjusted = min(max(adjusted, 0.0), 1.0)

    routing, routing_reason = _route(adjusted, two_signal)
    comment_visibility = "visible" if routing != "suppressed" else "suppressed"

    return {
        "kind": "semzero_causality_v1",
        "family": family,
        "change_signal": {
            "present": change_present,
            "classification": classification,
            "property": changed_property,
            "confidence": round(change_conf, 4),
            "source": change_source,
        },
        "dependency_signal": {
            "present": dependency_present,
            "highest_sensitivity": highest_sensitivity,
            "confidence": round(dep_conf, 4),
            "shortest_path": path,
            "source": dep_source,
            "distance": None if distance == 999 else distance,
        },
        "two_signal_confirmed": two_signal,
        "propagation_probability": round(propagation, 4),
        "severity_potential": round(severity, 4),
        "sensitivity_weight": round(sensitivity, 4),
        "evidence_strength": round(evidence, 4),
        "replay_multiplier": round(replay, 4),
        "false_positive_rate_adjustment": round(false_positive_rate, 4),
        "raw_causality_score": round(raw, 6),
        "adjusted_causality_score": round(adjusted, 6),
        "priority": int(round(adjusted * 100)),
        "routing": routing,
        "routing_reason": routing_reason,
        "comment_visibility": comment_visibility,
    }


def attach_causality_to_receipt_payload(
    payload: dict[str, Any],
    repo_snapshot: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mutate a receipt-like dict by attaching causality metadata to findings."""
    changed_resources = []
    for item in payload.get("changed_resources") or []:
        if isinstance(item, dict) and item.get("unique_id"):
            changed_resources.append(str(item["unique_id"]))
        elif isinstance(item, str):
            changed_resources.append(item)

    routing_counts: dict[str, int] = {}

    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        causality = score_finding_causality(
            finding,
            repo_snapshot=repo_snapshot,
            changed_resources=changed_resources,
            calibration=calibration,
        )
        finding["causality"] = causality
        finding["comment_visibility"] = causality["comment_visibility"]
        routing_counts[causality["routing"]] = routing_counts.get(causality["routing"], 0) + 1

    summary = payload.setdefault("summary", {})
    summary["causality_summary"] = {
        "kind": "semzero_causality_summary_v1",
        "routing_counts": dict(sorted(routing_counts.items())),
        "finding_count": len(payload.get("findings") or []),
        "visible_finding_count": sum(
            1
            for f in payload.get("findings") or []
            if isinstance(f, dict) and f.get("comment_visibility") != "suppressed"
        ),
        "suppressed_finding_count": routing_counts.get("suppressed", 0),
        "note": "Causality v1 combines finding change-signal evidence with blast-radius/repo-snapshot dependency evidence.",
    }

    # Keep top_findings synchronized where possible.
    top = summary.get("top_findings") or []
    by_id = {
        f.get("id") or f.get("stable_id"): f
        for f in payload.get("findings") or []
        if isinstance(f, dict)
    }
    for item in top:
        if not isinstance(item, dict):
            continue
        match = by_id.get(item.get("id") or item.get("stable_id"))
        if match and match.get("causality"):
            item["causality"] = match["causality"]
            item["comment_visibility"] = match.get("comment_visibility")

    return payload
