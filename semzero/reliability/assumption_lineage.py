from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ASSUMPTION_RECEIPT_PREFIX = "dbt_assumption_gate_"


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in str(value or "unknown"))


def _node_id(kind: str, value: str) -> str:
    return f"{kind}:{_safe_id(value)}"


def _finding_key(finding: dict[str, Any]) -> str:
    return str(
        finding.get("stable_id") or finding.get("id") or finding.get("fingerprint") or "unknown"
    )


def _iter_receipts(receipt_dir: str | Path) -> Iterable[dict[str, Any]]:
    root = Path(receipt_dir)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("receipt_kind", "")).startswith(ASSUMPTION_RECEIPT_PREFIX):
            payload["_receipt_path"] = str(path)
            rows.append(payload)
    return rows


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path) if path else Path("")
    if not p or not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _feedback_index(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    idx: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        for key in (row.get("stable_finding_id"), row.get("finding_id"), row.get("fingerprint")):
            if key:
                idx.setdefault(str(key), []).append(row)
    return idx


def _exception_state(finding: dict[str, Any]) -> str:
    exc = finding.get("exception") or {}
    if exc.get("active"):
        return "active_exception"
    if exc.get("expired"):
        return "expired_exception"
    return "none"


def _highest_business(finding: dict[str, Any]) -> str:
    bi = finding.get("business_impact") or {}
    if bi.get("highest_business_severity"):
        return str(bi.get("highest_business_severity"))
    vals = []
    for n in finding.get("blast_radius") or []:
        meta = n.get("metadata") or {}
        vals.append(meta.get("business_severity") or n.get("business_severity"))
    return next((str(v) for v in vals if v), "UNKNOWN")


@dataclass(slots=True)
class AssumptionLineageBuilder:
    receipt_dir: str = "data"
    feedback_file: str = ""
    exceptions_file: str = ""

    def build(self) -> dict[str, Any]:
        receipts = list(_iter_receipts(self.receipt_dir))
        feedback_records = _load_jsonl(
            self.feedback_file or Path(self.receipt_dir) / "assumption_feedback.jsonl"
        )
        feedback = _feedback_index(feedback_records)

        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        assumption_nodes: dict[str, dict[str, Any]] = {}
        source_nodes: dict[str, int] = {}
        exposure_nodes: dict[str, int] = {}
        family_counts: dict[str, int] = {}
        replay_counts = {"replay_validated_drift": 0, "replay_ran_no_drift": 0, "inferred_only": 0}
        exception_counts = {"active_exception": 0, "expired_exception": 0, "none": 0}
        feedback_counts = {"with_feedback": 0, "without_feedback": 0}

        def add_node(node_id: str, **attrs: Any) -> None:
            base = nodes.setdefault(node_id, {"id": node_id})
            for k, v in attrs.items():
                if v is not None and v != "":
                    base[k] = v

        def add_edge(src: str, dst: str, rel: str, **attrs: Any) -> None:
            edges.append(
                {
                    "source": src,
                    "target": dst,
                    "relationship": rel,
                    **{k: v for k, v in attrs.items() if v is not None},
                }
            )

        for receipt in receipts:
            receipt_id = _node_id(
                "receipt", receipt.get("_receipt_path") or receipt.get("generated_at") or len(nodes)
            )
            add_node(
                receipt_id,
                node_type="receipt",
                label=Path(str(receipt.get("_receipt_path", receipt_id))).name,
                receipt_kind=receipt.get("receipt_kind"),
                verdict=receipt.get("verdict"),
                generated_at=receipt.get("generated_at"),
            )
            for finding in receipt.get("findings") or []:
                stable = _finding_key(finding)
                family = str(finding.get("family") or "unknown")
                family_counts[family] = family_counts.get(family, 0) + 1
                assumption_id = _node_id("assumption", stable)
                source = finding.get("source") or {}
                source_name = (
                    source.get("unique_id")
                    or finding.get("source_resource")
                    or source.get("name")
                    or "unknown_source"
                )
                source_id = _node_id("source", source_name)
                business = _highest_business(finding)
                replay = finding.get("validation_replay") or {}
                replay_status = replay.get("status") or (
                    "not_run" if not replay.get("replay_ran") else "unknown"
                )
                replay_ran = bool(replay.get("replay_ran"))
                if replay_ran and replay_status == "drift_detected":
                    replay_counts["replay_validated_drift"] += 1
                elif replay_ran:
                    replay_counts["replay_ran_no_drift"] += 1
                else:
                    replay_counts["inferred_only"] += 1
                exc_state = _exception_state(finding)
                exception_counts[exc_state] = exception_counts.get(exc_state, 0) + 1
                fbacks = []
                for key in (
                    stable,
                    finding.get("id"),
                    finding.get("legacy_id"),
                    finding.get("fingerprint"),
                ):
                    if key and str(key) in feedback:
                        fbacks.extend(feedback[str(key)])
                feedback_counts["with_feedback" if fbacks else "without_feedback"] += 1

                add_node(
                    assumption_id,
                    node_type="assumption",
                    label=f"{family}: {stable}",
                    stable_id=stable,
                    family=family,
                    severity=finding.get("severity"),
                    risk_score=finding.get("risk_score"),
                    confidence=finding.get("confidence"),
                    business_severity=business,
                    replay_status=replay_status,
                    replay_ran=replay_ran,
                    exception_state=exc_state,
                    feedback_count=len(fbacks),
                    detector=finding.get("detector_version"),
                    assumption=(finding.get("assumption") or "")[:300],
                    drift_summary=(
                        (finding.get("assumption_diff") or {}).get("drift_summary") or ""
                    )[:300],
                )
                assumption_nodes[assumption_id] = nodes[assumption_id]
                add_node(
                    source_id,
                    node_type=source.get("node_type") or source.get("type") or "dbt_resource",
                    label=source.get("name") or source_name,
                    unique_id=source_name,
                    path=source.get("path"),
                    domain=source.get("domain") or "data",
                )
                source_nodes[source_id] = source_nodes.get(source_id, 0) + 1
                add_edge(source_id, assumption_id, "contains_assumption", family=family)
                add_edge(
                    assumption_id,
                    receipt_id,
                    "evidenced_by",
                    receipt_kind=receipt.get("receipt_kind"),
                )

                for br in finding.get("blast_radius") or []:
                    uid = br.get("unique_id") or br.get("name") or "unknown_blast_node"
                    bid = _node_id("blast", uid)
                    meta = br.get("metadata") or {}
                    bsev = (
                        meta.get("business_severity")
                        or br.get("business_severity")
                        or br.get("criticality")
                    )
                    add_node(
                        bid,
                        node_type=br.get("node_type") or br.get("type") or "blast_radius_node",
                        label=br.get("name") or uid,
                        unique_id=uid,
                        path=br.get("path"),
                        domain=br.get("domain") or "data",
                        owner=br.get("owner"),
                        business_severity=bsev,
                    )
                    exposure_nodes[bid] = exposure_nodes.get(bid, 0) + 1
                    add_edge(assumption_id, bid, "exposes", business_severity=bsev)

                if replay_ran:
                    rid = _node_id("replay", stable)
                    add_node(
                        rid,
                        node_type="validation_replay",
                        label=f"Replay: {stable}",
                        status=replay_status,
                        drift_metric=replay.get("drift_metric"),
                        drift_unit=replay.get("drift_unit"),
                        summary=replay.get("summary"),
                    )
                    add_edge(assumption_id, rid, "validated_by", status=replay_status)
                if exc_state != "none":
                    eid = _node_id("exception", stable)
                    add_node(
                        eid, node_type="exception", label=f"Exception: {stable}", state=exc_state
                    )
                    add_edge(assumption_id, eid, "annotated_by_exception", state=exc_state)
                if fbacks:
                    fid = _node_id("feedback", stable)
                    dispositions = sorted(
                        {str(x.get("disposition")) for x in fbacks if x.get("disposition")}
                    )
                    add_node(
                        fid,
                        node_type="feedback",
                        label=f"Feedback: {stable}",
                        count=len(fbacks),
                        dispositions=dispositions,
                    )
                    add_edge(
                        assumption_id, fid, "calibrated_by_feedback", feedback_count=len(fbacks)
                    )

        top_assumptions = sorted(
            assumption_nodes.values(),
            key=lambda n: (
                int(n.get("risk_score") or 0),
                str(n.get("business_severity") or ""),
                int(n.get("feedback_count") or 0),
            ),
            reverse=True,
        )[:20]
        top_sources = sorted(source_nodes.items(), key=lambda kv: kv[1], reverse=True)[:20]
        top_exposed = sorted(exposure_nodes.items(), key=lambda kv: kv[1], reverse=True)[:20]
        return {
            "lineage_kind": "semzero_assumption_lineage_lite_v1_25",
            "scope": "core_data_only",
            "receipt_count": len(receipts),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "assumption_node_count": len(assumption_nodes),
            "family_counts": family_counts,
            "replay_counts": replay_counts,
            "exception_counts": exception_counts,
            "feedback_counts": feedback_counts,
            "top_assumptions": top_assumptions,
            "top_source_nodes": [{"node": nodes[k], "assumption_count": c} for k, c in top_sources],
            "top_exposed_nodes": [
                {"node": nodes[k], "assumption_count": c} for k, c in top_exposed
            ],
            "graph": {"nodes": list(nodes.values()), "edges": edges},
            "guardrail": "Assumption Lineage Lite is a receipt-derived data-assumption graph. It is not a full cross-domain platform graph and does not change enforcement.",
        }

    def save_json(self, path: str | Path) -> dict[str, Any]:
        payload = self.build()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    def save_markdown(self, path: str | Path) -> dict[str, Any]:
        payload = self.build()
        lines = ["# SemZero Assumption Lineage Lite", "", payload["guardrail"], ""]
        lines += [
            f"- Receipts: {payload['receipt_count']}",
            f"- Assumption nodes: {payload['assumption_node_count']}",
            f"- Graph nodes: {payload['node_count']}",
            f"- Graph edges: {payload['edge_count']}",
            "",
        ]
        lines += ["## Families", ""]
        for fam, count in sorted(
            payload["family_counts"].items(), key=lambda kv: kv[1], reverse=True
        ):
            lines.append(f"- `{fam}`: {count}")
        lines += ["", "## Replay / calibration state", ""]
        for k, v in payload["replay_counts"].items():
            lines.append(f"- {k}: {v}")
        for k, v in payload["feedback_counts"].items():
            lines.append(f"- {k}: {v}")
        lines += ["", "## Top assumptions", ""]
        for n in payload["top_assumptions"][:10]:
            lines.append(
                f"- `{n.get('stable_id')}` · {n.get('family')} · severity={n.get('severity')} · business={n.get('business_severity')} · replay={n.get('replay_status')} · exception={n.get('exception_state')}"
            )
        lines += ["", "## Most exposed downstream nodes", ""]
        for row in payload["top_exposed_nodes"][:10]:
            n = row["node"]
            lines.append(
                f"- {n.get('label') or n.get('unique_id')} · {row['assumption_count']} assumption link(s) · business={n.get('business_severity')}"
            )
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return payload
