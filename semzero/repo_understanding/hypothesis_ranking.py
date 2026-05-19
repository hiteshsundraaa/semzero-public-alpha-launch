from __future__ import annotations

import math
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from semzero.repo_understanding.sql_semantic_diff import (
    SemanticDiffEvent,
    score_family_change_specificity,
)


SHADOW_RECEIPT_KIND = "semzero_shadow_hypothesis_receipt_v1"
RANKING_COMPARISON_KIND = "semzero_ranking_comparison_v1"
ACTIVATION_THRESHOLD = 0.45

WEIGHT_CHANGE_SPECIFICITY = 0.35  # highest: direct diff match is most trustworthy
WEIGHT_DEPENDENCY_SCORE = 0.25  # second: blast radius determines reviewer urgency
WEIGHT_EVIDENCE_FIDELITY = 0.20  # third: trust discount for inferred vs confirmed
WEIGHT_ACTIONABILITY = 0.20  # fourth: increases as action generator matures

FAMILY_MINIMUM_FLOORS = {
    "schema_contract_break": 0.30,
    "grain_contract_drift": 0.35,
    "enum_domain_closure": 0.40,
    "join_cardinality": 0.50,
    "metric_semantics_drift": 0.38,
    "filter_population_drift": 0.42,
}

FAMILY_ALIASES = {
    "join_relationship_drift": "join_cardinality",
    "grain_contract_drift": "join_cardinality",
    "schema_contract_break": "schema_contract_break",
    "metric_semantics_drift": "metric_semantics_drift",
    "filter_population_drift": "incremental_filter",
}


@dataclass(frozen=True, slots=True)
class RewriteStats:
    line_change_ratio: float = 0.0
    selected_expression_change_ratio: float = 0.0
    ast_node_change_ratio: float = 0.0
    cte_rewrite_ratio: float = 0.0
    join_graph_change_ratio: float = 0.0
    joins_changed: int = 0
    model_length_ratio: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_change_ratio": round(self.line_change_ratio, 4),
            "selected_expression_change_ratio": round(
                self.selected_expression_change_ratio, 4
            ),
            "ast_node_change_ratio": round(self.ast_node_change_ratio, 4),
            "cte_rewrite_ratio": round(self.cte_rewrite_ratio, 4),
            "join_graph_change_ratio": round(self.join_graph_change_ratio, 4),
            "joins_changed": self.joins_changed,
            "model_length_ratio": round(self.model_length_ratio, 4),
        }


def rank_hypotheses(
    findings: list[dict[str, Any]],
    semantic_events: list[SemanticDiffEvent | dict[str, Any]],
    repo_snapshot: dict[str, Any] | None = None,
    *,
    activation_threshold: float = ACTIVATION_THRESHOLD,
    rewrite_stats: RewriteStats | dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = [_event_from_any(event) for event in semantic_events]
    stats = _rewrite_stats_from_any(rewrite_stats)
    circuit_breaker = _massive_rewrite_reason(stats)
    normalized_findings = [_finding_dict(finding) for finding in findings]
    normalized_findings = _augment_with_event_backed_findings(
        normalized_findings,
        events,
        repo_snapshot,
    )
    if circuit_breaker and _single_schema_column_removal(events):
        circuit_breaker = ""

    if circuit_breaker:
        families_present = _families_present(normalized_findings, events)
        ranked = []
        for finding in normalized_findings:
            family = _family(finding)
            dependency = _dependency_score(finding, repo_snapshot)
            fidelity = _finding_fidelity(finding, events)
            actionability = _actionability(finding)
            ranked.append(
                {
                    **_base_rank_payload(finding),
                    "change_specificity": 0.0,
                    "dependency_score": dependency,
                    "evidence_fidelity": fidelity,
                    "actionability": actionability,
                    "rank_score": 0.0,
                    "score_decomposition": _score_decomposition(
                        finding=finding,
                        family=family,
                        supporting_events=[],
                        change_specificity=0.0,
                        dependency_score=dependency,
                        evidence_fidelity=fidelity,
                        actionability=actionability,
                        final_score=0.0,
                        formula="suppressed_by_massive_rewrite_circuit_breaker",
                        final_discounts=["massive_rewrite_circuit_breaker"],
                    ),
                    "role": "suppressed",
                    "suppression_reason": "massive_rewrite_circuit_breaker",
                    "supporting_event_types": [],
                    "supporting_events": [],
                }
            )
        return {
            "analysis_outcome": "massive_rewrite_circuit_breaker",
            "activation_threshold": activation_threshold,
            "family_minimum_floors": FAMILY_MINIMUM_FLOORS,
            "max_rank_score": 0.0,
            "primary_family": None,
            "semantic_event_types": sorted({event.event_type for event in events}),
            "rewrite_stats": stats.to_dict(),
            "circuit_breaker": {
                "triggered": True,
                "reason": circuit_breaker,
                "granular_findings_suppressed": True,
                "assumption_families_present": families_present,
                "unranked_assumption_families": [
                    {"family": family, "ranked": False} for family in families_present
                ],
                "recommended_action": "run full downstream dbt build/test",
            },
            "ranked_hypotheses": ranked,
        }

    ranked = []
    for finding in normalized_findings:
        family = _family(finding)
        supporting = _supporting_events(family, events)
        change_specificity = score_family_change_specificity(supporting, family)
        if not change_specificity:
            change_specificity = _aliased_specificity(family, events)
        dependency = _dependency_score(finding, repo_snapshot)
        fidelity = _finding_fidelity(finding, supporting or events)
        actionability = _actionability(finding)
        rank_score = _rank_score(
            change_specificity=change_specificity,
            dependency_score=dependency,
            evidence_fidelity=fidelity,
            actionability=actionability,
        )
        formula = "weighted_sum"
        final_discounts: list[str] = []
        if not change_specificity:
            # Dependency context without a property-specific change event should
            # never outrank a direct semantic trigger.
            rank_score = min(rank_score, 0.34)
            formula = "min(weighted_sum, dependency_context_without_semantic_change_event_cap)"
            final_discounts.append("dependency_context_without_semantic_change_event_cap")
        ranked.append(
            {
                **_base_rank_payload(finding),
                "change_specificity": round(change_specificity, 4),
                "dependency_score": round(dependency, 4),
                "evidence_fidelity": round(fidelity, 4),
                "actionability": round(actionability, 4),
                "rank_score": round(rank_score, 4),
                "score_decomposition": _score_decomposition(
                    finding=finding,
                    family=family,
                    supporting_events=supporting,
                    change_specificity=change_specificity,
                    dependency_score=dependency,
                    evidence_fidelity=fidelity,
                    actionability=actionability,
                    final_score=rank_score,
                    formula=formula,
                    final_discounts=final_discounts,
                ),
                "activation_floor": _family_activation_floor(family, activation_threshold),
                "role": "candidate",
                "suppression_reason": "",
                "supporting_event_types": sorted({event.event_type for event in supporting}),
                "supporting_events": [event.to_dict() for event in supporting[:5]],
            }
        )

    ranked.sort(
        key=lambda item: (
            item["rank_score"],
            item["change_specificity"],
            item["dependency_score"],
            str(item.get("stable_id") or ""),
        ),
        reverse=True,
    )

    max_score = ranked[0]["rank_score"] if ranked else 0.0
    eligible = [
        item
        for item in ranked
        if item["rank_score"] >= item["activation_floor"] and item["change_specificity"] > 0
    ]
    if not eligible:
        for item in ranked:
            item["role"] = "suppressed"
            item["suppression_reason"] = (
                "below_family_activation_floor"
                if item["change_specificity"] > 0
                else "no_property_specific_primary"
            )
        return {
            "analysis_outcome": "silent_pass",
            "activation_threshold": activation_threshold,
            "family_minimum_floors": FAMILY_MINIMUM_FLOORS,
            "max_rank_score": round(max_score, 4),
            "primary_family": None,
            "semantic_event_types": sorted({event.event_type for event in events}),
            "rewrite_stats": stats.to_dict() if stats else {},
            "circuit_breaker": {"triggered": False},
            "ranked_hypotheses": ranked,
        }

    primary_assigned = False
    for item in ranked:
        if (
            not primary_assigned
            and item["rank_score"] >= item["activation_floor"]
            and item["change_specificity"] > 0
        ):
            item["role"] = "primary"
            primary_assigned = True
        elif item["rank_score"] >= item["activation_floor"] and item["change_specificity"] > 0:
            item["role"] = "secondary"
        elif item["rank_score"] >= 0.12:
            item["role"] = "advisory"
            if not item["change_specificity"]:
                item["suppression_reason"] = "dependency_context_without_semantic_change_event"
        else:
            item["role"] = "suppressed"
            item["suppression_reason"] = "low_rank_score"

    if not primary_assigned:
        for item in ranked:
            item["role"] = "suppressed"
            item["suppression_reason"] = "no_property_specific_primary"
        outcome = "silent_pass"
        primary_family = None
    else:
        outcome = "ranked"
        primary_family = next(
            (item["family"] for item in ranked if item["role"] == "primary"), None
        )

    return {
        "analysis_outcome": outcome,
        "activation_threshold": activation_threshold,
        "family_minimum_floors": FAMILY_MINIMUM_FLOORS,
        "max_rank_score": round(max_score, 4),
        "primary_family": primary_family,
        "semantic_event_types": sorted({event.event_type for event in events}),
        "rewrite_stats": stats.to_dict() if stats else {},
        "circuit_breaker": {"triggered": False},
        "ranked_hypotheses": ranked,
    }


def build_shadow_hypothesis_receipt(
    production_receipt: dict[str, Any],
    semantic_events: list[SemanticDiffEvent | dict[str, Any]],
    repo_snapshot: dict[str, Any] | None = None,
    *,
    activation_threshold: float = ACTIVATION_THRESHOLD,
    rewrite_stats: RewriteStats | dict[str, Any] | None = None,
) -> dict[str, Any]:
    ranking = rank_hypotheses(
        list(production_receipt.get("findings") or []),
        semantic_events,
        repo_snapshot,
        activation_threshold=activation_threshold,
        rewrite_stats=rewrite_stats,
    )
    ranked = ranking["ranked_hypotheses"]
    return {
        "kind": SHADOW_RECEIPT_KIND,
        "shadow_only": True,
        "source_receipt_kind": production_receipt.get("receipt_kind"),
        "source_verdict": production_receipt.get("verdict"),
        "analysis_outcome": ranking["analysis_outcome"],
        "activation_threshold": ranking["activation_threshold"],
        "family_minimum_floors": ranking.get("family_minimum_floors") or FAMILY_MINIMUM_FLOORS,
        "ranking_weights": {
            "change_specificity": WEIGHT_CHANGE_SPECIFICITY,
            "dependency_score": WEIGHT_DEPENDENCY_SCORE,
            "evidence_fidelity": WEIGHT_EVIDENCE_FIDELITY,
            "actionability": WEIGHT_ACTIONABILITY,
        },
        "max_rank_score": ranking["max_rank_score"],
        "primary_family": ranking["primary_family"],
        "semantic_events": [_event_from_any(event).to_dict() for event in semantic_events],
        "semantic_event_types": ranking["semantic_event_types"],
        "summary": {
            "primary_count": _count_role(ranked, "primary"),
            "secondary_count": _count_role(ranked, "secondary"),
            "advisory_count": _count_role(ranked, "advisory"),
            "suppressed_count": _count_role(ranked, "suppressed"),
        },
        "circuit_breaker": ranking["circuit_breaker"],
        "rewrite_stats": ranking.get("rewrite_stats") or {},
        "ranked_hypotheses": ranked,
        "product_invariant": (
            "SemZero does not optimize for finding the most things; it optimizes "
            "for putting the most review-worthy assumption first."
        ),
    }


def build_ranking_comparison(
    production_receipt: dict[str, Any],
    shadow_receipt: dict[str, Any],
    *,
    production_comment: str = "",
) -> dict[str, Any]:
    old_findings = list(production_receipt.get("findings") or [])
    ranked = list(shadow_receipt.get("ranked_hypotheses") or [])
    new_primary = next((item for item in ranked if item.get("role") == "primary"), None)
    old_top_family = str((old_findings[0] or {}).get("family")) if old_findings else None
    new_primary_family = new_primary.get("family") if new_primary else None
    old_must_review = _parse_comment_count(production_comment, "Review-required")
    if old_must_review is None:
        old_must_review = sum(1 for finding in old_findings if _is_old_must_review(finding))
    new_must_review = _count_role(ranked, "primary")
    silent_pass = shadow_receipt.get("analysis_outcome") == "silent_pass"
    massive_rewrite = bool((shadow_receipt.get("circuit_breaker") or {}).get("triggered"))
    ranking_changed = bool(
        old_top_family and new_primary_family and old_top_family != new_primary_family
    )
    ranking_agreement = _ranking_agreement(old_top_family, new_primary_family)
    return {
        "kind": RANKING_COMPARISON_KIND,
        "shadow_only": True,
        "old_top_family": old_top_family,
        "new_primary_family": new_primary_family,
        "old_must_review_count": old_must_review,
        "new_must_review_count": new_must_review,
        "new_secondary_count": _count_role(ranked, "secondary"),
        "new_advisory_count": _count_role(ranked, "advisory"),
        "suppressed_count": _count_role(ranked, "suppressed"),
        "semantic_events": list(shadow_receipt.get("semantic_event_types") or []),
        "ranking_changed": ranking_changed,
        "silent_pass": silent_pass,
        "silent_pass_triggered": silent_pass,
        "massive_rewrite_circuit_breaker": massive_rewrite,
        "massive_rewrite_triggered": massive_rewrite,
        "reviewer_action_delta": _reviewer_action_delta(
            old_top_family=old_top_family,
            new_primary_family=new_primary_family,
            old_must_review=old_must_review,
            new_must_review=new_must_review,
            silent_pass=silent_pass,
            massive_rewrite=massive_rewrite,
        ),
        "ranking_agreement": ranking_agreement,
        "ranking_confidence": round(float((new_primary or {}).get("rank_score") or 0.0), 4),
    }


def render_shadow_comment(shadow_receipt: dict[str, Any]) -> str:
    lines = [
        "<!-- semzero-shadow-hypothesis-ranking -->",
        "## SemZero Shadow Hypothesis Ranking",
        "",
        "**Shadow only. Production PR comment is unchanged.**",
        "",
        f"Outcome: `{shadow_receipt.get('analysis_outcome')}` · Primary: `{shadow_receipt.get('primary_family') or 'none'}`",
        f"Activation threshold: `{shadow_receipt.get('activation_threshold')}` · Max score: `{shadow_receipt.get('max_rank_score')}`",
        "",
    ]
    events = shadow_receipt.get("semantic_event_types") or []
    if events:
        lines += ["Semantic event evidence:", ""]
        for event_type in events[:12]:
            lines.append(f"- `{event_type}`")
        lines.append("")
    if (shadow_receipt.get("circuit_breaker") or {}).get("triggered"):
        cb = shadow_receipt.get("circuit_breaker") or {}
        lines += [
            "### Circuit Breaker",
            "",
            f"Reason: `{cb.get('reason')}`",
            f"Recommended action: {cb.get('recommended_action')}",
            "",
        ]
        families = cb.get("unranked_assumption_families") or []
        if families:
            lines += ["Assumption families present in this rewrite:", ""]
            for item in families:
                lines.append(f"- `{item.get('family')}` (unranked)")
            lines.append("")
    lines += ["### Ranked Hypotheses", ""]
    for item in shadow_receipt.get("ranked_hypotheses") or []:
        reason = (
            f" · suppression `{item.get('suppression_reason')}`"
            if item.get("suppression_reason")
            else ""
        )
        events_txt = ", ".join(f"`{x}`" for x in item.get("supporting_event_types") or [])
        if not events_txt:
            events_txt = "`none`"
        lines += [
            f"- **{item.get('family')}** — `{item.get('role')}` · score `{item.get('rank_score')}`{reason}",
            f"  Evidence: {events_txt}",
        ]
    lines += [
        "",
        "_This artifact compares semantic ranking against the existing detector output before changing production comments._",
    ]
    return "\n".join(lines)


def write_shadow_ranking_artifacts(
    output_dir: str | Path,
    production_receipt: dict[str, Any],
    production_comment: str,
    semantic_events: list[SemanticDiffEvent | dict[str, Any]],
    repo_snapshot: dict[str, Any] | None = None,
    *,
    activation_threshold: float = ACTIVATION_THRESHOLD,
    rewrite_stats: RewriteStats | dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    shadow_receipt = build_shadow_hypothesis_receipt(
        production_receipt,
        semantic_events,
        repo_snapshot,
        activation_threshold=activation_threshold,
        rewrite_stats=rewrite_stats,
    )
    shadow_comment = render_shadow_comment(shadow_receipt)
    comparison = build_ranking_comparison(
        production_receipt,
        shadow_receipt,
        production_comment=production_comment,
    )
    _write_json(root / "shadow_hypothesis_receipt.json", shadow_receipt)
    (root / "shadow_comment.md").write_text(shadow_comment, encoding="utf-8")
    _write_json(root / "ranking_comparison.json", comparison)
    return {
        "shadow_hypothesis_receipt": shadow_receipt,
        "shadow_comment": shadow_comment,
        "ranking_comparison": comparison,
    }


def estimate_rewrite_stats(
    before_sql: str,
    after_sql: str,
    *,
    joins_changed: int = 0,
) -> RewriteStats:
    before_lines = [line.strip() for line in str(before_sql or "").splitlines() if line.strip()]
    after_lines = [line.strip() for line in str(after_sql or "").splitlines() if line.strip()]
    changed_lines = len(set(before_lines) ^ set(after_lines))
    denom = max(len(set(before_lines) | set(after_lines)), 1)
    line_change_ratio = changed_lines / denom
    before_selects = _select_expression_count(before_sql)
    after_selects = _select_expression_count(after_sql)
    selected_expression_change_ratio = abs(before_selects - after_selects) / max(
        before_selects, after_selects, 1
    )
    before_tokens = _token_count(before_sql)
    after_tokens = _token_count(after_sql)
    ast_node_change_ratio = abs(before_tokens - after_tokens) / max(before_tokens, after_tokens, 1)
    before_ctes = len(re.findall(r"\bas\s*\(", str(before_sql or ""), flags=re.I))
    after_ctes = len(re.findall(r"\bas\s*\(", str(after_sql or ""), flags=re.I))
    cte_rewrite_ratio = abs(before_ctes - after_ctes) / max(before_ctes, after_ctes, 1)
    before_joins = len(re.findall(r"\bjoin\b", str(before_sql or ""), flags=re.I))
    after_joins = len(re.findall(r"\bjoin\b", str(after_sql or ""), flags=re.I))
    join_graph_change_ratio = abs(before_joins - after_joins) / max(before_joins, after_joins, 1)
    if joins_changed:
        join_graph_change_ratio = max(join_graph_change_ratio, min(1.0, joins_changed / 3.0))
    length_ratio = max(len(after_sql or ""), 1) / max(len(before_sql or ""), 1)
    if length_ratio < 1:
        length_ratio = 1 / max(length_ratio, 0.0001)
    return RewriteStats(
        line_change_ratio=line_change_ratio,
        selected_expression_change_ratio=selected_expression_change_ratio,
        ast_node_change_ratio=ast_node_change_ratio,
        cte_rewrite_ratio=cte_rewrite_ratio,
        join_graph_change_ratio=join_graph_change_ratio,
        joins_changed=joins_changed,
        model_length_ratio=length_ratio,
    )


def _augment_with_event_backed_findings(
    findings: list[dict[str, Any]],
    events: list[SemanticDiffEvent],
    repo_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    families = {_family(finding) for finding in findings}
    additions: list[dict[str, Any]] = []
    for event in events:
        if event.event_type != "selected_column_removed":
            continue
        if "schema_contract_break" in families:
            continue
        removed_column = _removed_column_alias(event)
        if not removed_column:
            continue
        model = _snapshot_model_for_event(repo_snapshot, event)
        references = [
            ref
            for ref in (model.get("downstream_column_references") or [])
            if str(ref.get("column") or "").lower() == removed_column.lower()
        ]
        if not references:
            continue
        additions.append(_schema_break_finding_from_event(event, model, removed_column, references))
        families.add("schema_contract_break")
    return findings + additions


def _single_schema_column_removal(events: list[SemanticDiffEvent]) -> bool:
    schema_events = [
        event for event in events if event.event_type == "selected_column_removed"
    ]
    return bool(schema_events) and len(events) <= 2


def _removed_column_alias(event: SemanticDiffEvent) -> str:
    before = str(event.before or event.raw_excerpt or "")
    alias = re.search(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", before, flags=re.I)
    if alias:
        return alias.group(1)
    simple = re.search(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)\s*$", before.strip())
    return simple.group(1) if simple else ""


def _snapshot_model_for_event(
    repo_snapshot: dict[str, Any] | None, event: SemanticDiffEvent
) -> dict[str, Any]:
    models = (repo_snapshot or {}).get("models") or {}
    event_model = event.model.lower()
    for model in models.values():
        if str(model.get("name") or "").lower() == event_model:
            return model
    return {}


def _schema_break_finding_from_event(
    event: SemanticDiffEvent,
    model: dict[str, Any],
    removed_column: str,
    references: list[dict[str, Any]],
) -> dict[str, Any]:
    seed = f"{model.get('unique_id')}:{removed_column}:{event.event_type}"
    stable = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10].upper()
    highest = _highest_sensitivity(ref.get("sensitivity") for ref in references)
    return {
        "id": f"AG-SCHEMA-CONTRACT-BREAK-{stable}",
        "stable_id": f"AG-SCHEMA-CONTRACT-BREAK-{stable}",
        "family": "schema_contract_break",
        "severity": "high",
        "confidence": "high",
        "source_resource": model.get("unique_id") or event.model,
        "source_path": model.get("path") or "",
        "assumption": f"Downstream models can still select `{removed_column}` from `{event.model}`.",
        "why_it_matters": (
            "A selected output column was removed while downstream SQL still references it."
        ),
        "recommended_check": (
            f"Update downstream references to `{removed_column}` or restore the column before merge."
        ),
        "trigger_evidence": [
            f"{event.event_type}: {removed_column}",
            *[
                f"{ref.get('downstream_name') or ref.get('downstream_model')} references {removed_column}"
                for ref in references[:4]
            ],
        ],
        "blast_radius": [
            {
                "node_type": ref.get("resource_type") or "dbt_model",
                "name": ref.get("downstream_name") or ref.get("downstream_model"),
                "unique_id": ref.get("downstream_model"),
                "path": ref.get("downstream_path"),
                "business_severity": ref.get("sensitivity") or "UNKNOWN",
            }
            for ref in references
        ],
        "business_impact": {
            "highest_business_severity": highest,
        },
        "replay_fidelity": {
            "score": max(event.fidelity, 0.70),
        },
        "shadow_generated": True,
    }


def _highest_sensitivity(labels: Any) -> str:
    order = {
        "BOARD_CRITICAL": 6,
        "EXEC_CRITICAL": 5,
        "REVENUE_CRITICAL": 4,
        "CUSTOMER_FACING": 3,
        "OPERATIONAL": 2,
        "ANALYTICAL": 1,
        "UNKNOWN": 0,
    }
    best = "UNKNOWN"
    for label in labels:
        normalized = str(label or "UNKNOWN").upper()
        if order.get(normalized, 0) > order.get(best, 0):
            best = normalized
    return best


def _event_from_any(event: SemanticDiffEvent | dict[str, Any]) -> SemanticDiffEvent:
    if isinstance(event, SemanticDiffEvent):
        return event
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    return SemanticDiffEvent(
        event_type=str(event.get("event_type") or ""),
        family_hint=str(event.get("family_hint") or ""),
        model=str(event.get("model") or ""),
        before=event.get("before"),
        after=event.get("after"),
        changed_columns=tuple(str(x) for x in (event.get("changed_columns") or [])),
        clause=str(location.get("clause") or event.get("clause") or ""),
        cte=location.get("cte") or event.get("cte"),
        confidence=float(event.get("confidence") or 0.0),
        fidelity=float(event.get("fidelity") or 0.0),
        source=str(event.get("source") or ""),
        raw_excerpt=str(event.get("raw_excerpt") or ""),
    )


def _finding_dict(finding: Any) -> dict[str, Any]:
    if isinstance(finding, dict):
        return finding
    if hasattr(finding, "to_dict"):
        return finding.to_dict()
    if hasattr(finding, "__dict__"):
        return dict(finding.__dict__)
    return {}


def _family(finding: dict[str, Any]) -> str:
    return str(finding.get("family") or finding.get("assumption_family") or "").strip()


def _supporting_events(family: str, events: list[SemanticDiffEvent]) -> list[SemanticDiffEvent]:
    candidates = [event for event in events if event.family_hint == family]
    aliases = {hint for hint, target in FAMILY_ALIASES.items() if target == family}
    candidates.extend(event for event in events if event.family_hint in aliases)
    seen = set()
    out = []
    for event in candidates:
        key = (event.event_type, event.family_hint, event.raw_excerpt)
        if key not in seen:
            seen.add(key)
            out.append(event)
    return out


def _aliased_specificity(family: str, events: list[SemanticDiffEvent]) -> float:
    aliases = {hint for hint, target in FAMILY_ALIASES.items() if target == family}
    best = 0.0
    for alias in aliases:
        best = max(best, score_family_change_specificity(events, alias))
    if family == "join_cardinality":
        # Grain changes often surface as downstream join-cardinality consequences.
        best = max(best, score_family_change_specificity(events, "grain_contract_drift") * 0.72)
    return round(best, 4)


def _dependency_score(finding: dict[str, Any], repo_snapshot: dict[str, Any] | None) -> float:
    blast = finding.get("blast_radius") or []
    score = 0.10 if not blast else min(0.85, 0.35 + math.log10(len(blast) + 1) / 2.5)
    business = str(
        ((finding.get("business_impact") or {}).get("highest_business_severity") or "")
    ).upper()
    if business in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}:
        score = max(score, 0.90)
    elif business and business != "UNKNOWN":
        score = max(score, 0.60)
    source = str(finding.get("source_resource") or (finding.get("source") or {}).get("unique_id") or "")
    if repo_snapshot and source:
        summary = repo_snapshot.get("summary") or {}
        if summary.get("dependency_contract_count"):
            score = min(1.0, score + 0.05)
    return min(score, 1.0)


def _finding_fidelity(finding: dict[str, Any], events: list[SemanticDiffEvent]) -> float:
    event_fidelity = max((event.fidelity for event in events), default=0.0)
    replay = finding.get("replay_fidelity") or {}
    try:
        receipt_fidelity = float(replay.get("score") or 0.0)
    except Exception:
        receipt_fidelity = 0.0
    if event_fidelity:
        return max(event_fidelity, receipt_fidelity)
    return receipt_fidelity or 0.2


def _actionability(finding: dict[str, Any]) -> float:
    score = 0.0
    if finding.get("recommended_check"):
        score += 0.45
    if finding.get("stable_id") or finding.get("id"):
        score += 0.25
    if finding.get("assumption") and finding.get("why_it_matters"):
        score += 0.20
    if finding.get("trigger_evidence"):
        score += 0.10
    return min(score, 1.0)


def _score_decomposition(
    *,
    finding: dict[str, Any],
    family: str,
    supporting_events: list[SemanticDiffEvent],
    change_specificity: float,
    dependency_score: float,
    evidence_fidelity: float,
    actionability: float,
    final_score: float,
    formula: str,
    final_discounts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "change_specificity": {
            "score": round(change_specificity, 4),
            "weight": WEIGHT_CHANGE_SPECIFICITY,
            "weighted": round(change_specificity * WEIGHT_CHANGE_SPECIFICITY, 4),
            "evidence": [_event_evidence(event) for event in supporting_events],
            "discounts": [] if supporting_events else ["no_property_specific_semantic_event"],
        },
        "dependency_score": {
            "score": round(dependency_score, 4),
            "weight": WEIGHT_DEPENDENCY_SCORE,
            "weighted": round(dependency_score * WEIGHT_DEPENDENCY_SCORE, 4),
            "evidence": _dependency_evidence(finding),
            "discounts": _dependency_discounts(finding, family),
        },
        "evidence_fidelity": {
            "score": round(evidence_fidelity, 4),
            "weight": WEIGHT_EVIDENCE_FIDELITY,
            "weighted": round(evidence_fidelity * WEIGHT_EVIDENCE_FIDELITY, 4),
            "evidence": _fidelity_evidence(supporting_events, finding),
            "discounts": _fidelity_discounts(finding),
        },
        "actionability": {
            "score": round(actionability, 4),
            "weight": WEIGHT_ACTIONABILITY,
            "weighted": round(actionability * WEIGHT_ACTIONABILITY, 4),
            "evidence": _actionability_evidence(finding),
            "discounts": _actionability_discounts(finding),
            "recommended_check": finding.get("recommended_check") or "",
        },
        "severity_potential": 1.0,
        "final_formula": formula,
        "final_discounts": final_discounts or [],
        "final_score": round(final_score, 4),
    }


def _event_evidence(event: SemanticDiffEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "family_hint": event.family_hint,
        "source": event.source,
        "confidence": round(event.confidence, 4),
        "fidelity": round(event.fidelity, 4),
        "clause": event.clause,
        "changed_columns": list(event.changed_columns),
    }


def _dependency_evidence(finding: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    blast = finding.get("blast_radius") or []
    if blast:
        evidence.append(f"confirmed_blast_radius:{len(blast)}")
    else:
        evidence.append("no_confirmed_downstream_dependency")
    business = str(
        ((finding.get("business_impact") or {}).get("highest_business_severity") or "")
    ).upper()
    if business in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}:
        evidence.append("revenue_critical_downstream")
    elif business and business != "UNKNOWN":
        evidence.append(f"business_severity:{business.lower()}")
    if finding.get("trigger_evidence"):
        evidence.append("inferred_contract_only")
    return evidence


def _dependency_discounts(finding: dict[str, Any], family: str) -> list[str]:
    discounts: list[str] = []
    if family in {"join_cardinality", "grain_contract_drift", "join_relationship_drift"}:
        discounts.append("no_explicit_grain_contract")
    if not finding.get("blast_radius"):
        discounts.append("no_confirmed_downstream_dependency")
    if not (finding.get("business_impact") or {}).get("highest_business_severity"):
        discounts.append("unknown_business_criticality")
    return discounts


def _fidelity_evidence(
    supporting_events: list[SemanticDiffEvent], finding: dict[str, Any]
) -> list[str]:
    evidence: list[str] = []
    if supporting_events:
        evidence.append("semantic_diff_event_available")
        if any("sqlglot" in event.source for event in supporting_events):
            evidence.append("sqlglot_ast_diff_available")
        else:
            evidence.append("clause_or_fallback_semantic_diff_available")
    replay = finding.get("replay_fidelity") or {}
    if replay.get("score") is not None:
        evidence.append("static_replay_fidelity_score_available")
    return evidence or ["generic_receipt_context_only"]


def _fidelity_discounts(finding: dict[str, Any]) -> list[str]:
    discounts: list[str] = []
    replay = finding.get("replay_fidelity") or {}
    if not replay.get("queries_replayed"):
        discounts.append("no_behavioral_replay")
    return discounts


def _actionability_evidence(finding: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    if finding.get("recommended_check"):
        evidence.append("specific_recommended_check")
    if finding.get("stable_id") or finding.get("id"):
        evidence.append("stable_finding_id")
    if finding.get("assumption"):
        evidence.append("assumption_text_available")
    if finding.get("trigger_evidence"):
        evidence.append("trigger_evidence_available")
    return evidence


def _actionability_discounts(finding: dict[str, Any]) -> list[str]:
    discounts: list[str] = []
    if not finding.get("recommended_check"):
        discounts.append("missing_specific_recommended_check")
    if not (finding.get("stable_id") or finding.get("id")):
        discounts.append("missing_stable_finding_id")
    return discounts


def _rank_score(
    *,
    change_specificity: float,
    dependency_score: float,
    evidence_fidelity: float,
    actionability: float,
) -> float:
    return (
        WEIGHT_CHANGE_SPECIFICITY * change_specificity
        + WEIGHT_DEPENDENCY_SCORE * dependency_score
        + WEIGHT_EVIDENCE_FIDELITY * evidence_fidelity
        + WEIGHT_ACTIONABILITY * actionability
    )


def _family_activation_floor(family: str, activation_threshold: float) -> float:
    return FAMILY_MINIMUM_FLOORS.get(family, activation_threshold)


def _families_present(
    findings: list[dict[str, Any]], events: list[SemanticDiffEvent]
) -> list[str]:
    families = {_family(finding) for finding in findings if _family(finding)}
    families.update(event.family_hint for event in events if event.family_hint)
    return sorted(families)


def _ranking_agreement(old_top_family: str | None, new_primary_family: str | None) -> str:
    if not old_top_family and not new_primary_family:
        return "no_primary"
    if old_top_family == new_primary_family:
        return "agree_on_primary"
    if old_top_family and new_primary_family:
        return "disagree_on_primary"
    if old_top_family and not new_primary_family:
        return "shadow_suppressed_primary"
    return "shadow_added_primary"


def _reviewer_action_delta(
    *,
    old_top_family: str | None,
    new_primary_family: str | None,
    old_must_review: int,
    new_must_review: int,
    silent_pass: bool,
    massive_rewrite: bool,
) -> str:
    if massive_rewrite:
        return "would_replace_granular_review_with_full_downstream_validation"
    if silent_pass and old_must_review > 0:
        return "would_remove_reviewer_action"
    if old_top_family and new_primary_family and old_top_family != new_primary_family:
        return "would_review_different_finding_first"
    if old_must_review != new_must_review:
        return "would_change_review_count"
    return "no_change"


def _base_rank_payload(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": finding.get("id") or finding.get("stable_id") or finding.get("legacy_id"),
        "stable_id": finding.get("stable_id") or finding.get("id"),
        "family": _family(finding),
        "source_resource": finding.get("source_resource")
        or (finding.get("source") or {}).get("unique_id"),
        "source_path": finding.get("source_path")
        or (finding.get("source") or {}).get("path"),
        "severity": finding.get("severity"),
        "confidence": finding.get("confidence"),
    }


def _count_role(ranked: list[dict[str, Any]], role: str) -> int:
    return sum(1 for item in ranked if item.get("role") == role)


def _rewrite_stats_from_any(raw: RewriteStats | dict[str, Any] | None) -> RewriteStats | None:
    if raw is None:
        return None
    if isinstance(raw, RewriteStats):
        return raw
    return RewriteStats(
        line_change_ratio=float(raw.get("line_change_ratio") or 0.0),
        selected_expression_change_ratio=float(
            raw.get("selected_expression_change_ratio") or 0.0
        ),
        ast_node_change_ratio=float(raw.get("ast_node_change_ratio") or 0.0),
        cte_rewrite_ratio=float(raw.get("cte_rewrite_ratio") or 0.0),
        join_graph_change_ratio=float(raw.get("join_graph_change_ratio") or 0.0),
        joins_changed=int(raw.get("joins_changed") or 0),
        model_length_ratio=float(raw.get("model_length_ratio") or 1.0),
    )


def _massive_rewrite_reason(stats: RewriteStats | None) -> str:
    if not stats:
        return ""
    if stats.ast_node_change_ratio > 0.40:
        return "ast_node_change_ratio_above_40_percent"
    if stats.selected_expression_change_ratio > 0.60:
        return "selected_expression_change_ratio_above_60_percent"
    if stats.joins_changed > 3:
        return "more_than_three_joins_changed"
    if stats.model_length_ratio > 2.0:
        return "model_length_changed_by_more_than_2x"
    return ""


def _parse_comment_count(comment: str, label: str) -> int | None:
    match = re.search(rf"{re.escape(label)}:\s*`?(\d+)`?", comment or "")
    if match:
        return int(match.group(1))
    return None


def _is_old_must_review(finding: dict[str, Any]) -> bool:
    severity = str(finding.get("severity") or "").lower()
    business = str(
        ((finding.get("business_impact") or {}).get("highest_business_severity") or "")
    ).upper()
    risk = int(finding.get("risk_score") or 0)
    return bool(
        severity in {"high", "critical"}
        or risk >= 80
        or business in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}
    )


def _select_expression_count(sql: str) -> int:
    clause = ""
    match = re.search(r"\bselect\b(.*?)\bfrom\b", str(sql or ""), flags=re.I | re.S)
    if match:
        clause = match.group(1)
    if not clause:
        return 0
    return max(1, clause.count(",") + 1)


def _token_count(sql: str) -> int:
    return len(re.findall(r"\b\w+\b|[(),=*<>+-]", str(sql or "")))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    import json

    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
