from __future__ import annotations

import json

from click.testing import CliRunner

from semzero.cli import cli
from semzero.repo_understanding.hypothesis_ranking import (
    FAMILY_MINIMUM_FLOORS,
    WEIGHT_ACTIONABILITY,
    WEIGHT_CHANGE_SPECIFICITY,
    WEIGHT_DEPENDENCY_SCORE,
    WEIGHT_EVIDENCE_FIDELITY,
    RewriteStats,
    build_ranking_comparison,
    build_shadow_hypothesis_receipt,
    rank_hypotheses,
    write_shadow_ranking_artifacts,
)
from tests.test_dbt_assumption_gate_v1 import _manifest
from semzero.repo_understanding.sql_semantic_diff import (
    SemanticDiffEvent,
    extract_clause_fallback_events,
)


def _finding(family: str, *, stable_id: str | None = None, blast: bool = True) -> dict:
    return {
        "id": stable_id or f"AG-{family.upper()}",
        "stable_id": stable_id or f"AG-{family.upper()}",
        "family": family,
        "severity": "high",
        "confidence": "high",
        "source_resource": f"model.repo3.{family}",
        "source_path": f"models/{family}.sql",
        "assumption": f"{family} assumption",
        "why_it_matters": "Downstream business logic can drift silently.",
        "recommended_check": "Run the targeted before/after validation.",
        "trigger_evidence": ["changed SQL"],
        "blast_radius": [
            {
                "node_type": "dbt_model",
                "name": "mart_order_payments",
                "business_severity": "REVENUE_CRITICAL",
            }
        ]
        if blast
        else [],
        "business_impact": {
            "highest_business_severity": "REVENUE_CRITICAL" if blast else "UNKNOWN"
        },
        "replay_fidelity": {"score": 0.58},
    }


def test_enum_only_change_ranks_enum_primary_and_join_advisory():
    before = """
    select
      case when payment_status = 'completed' then 'paid' else 'pending' end
        as final_payment_status
    from payments
    """
    after = before.replace("else 'pending'", "else 'unresolved'")
    events = extract_clause_fallback_events(before, after, model="int_payment_summary")
    findings = [
        _finding("join_cardinality", stable_id="AG-JOIN", blast=False),
        _finding("enum_domain_closure", stable_id="AG-ENUM"),
    ]

    payload = rank_hypotheses(findings, events)
    ranked = {item["family"]: item for item in payload["ranked_hypotheses"]}

    assert WEIGHT_CHANGE_SPECIFICITY == 0.35
    assert WEIGHT_DEPENDENCY_SCORE == 0.25
    assert WEIGHT_EVIDENCE_FIDELITY == 0.20
    assert WEIGHT_ACTIONABILITY == 0.20
    assert FAMILY_MINIMUM_FLOORS["join_cardinality"] == 0.50
    assert payload["primary_family"] == "enum_domain_closure"
    assert ranked["enum_domain_closure"]["role"] == "primary"
    assert ranked["enum_domain_closure"]["activation_floor"] == 0.40
    assert "case_else_changed" in ranked["enum_domain_closure"]["supporting_event_types"]
    assert ranked["join_cardinality"]["role"] == "advisory"
    assert ranked["join_cardinality"]["suppression_reason"] == (
        "dependency_context_without_semantic_change_event"
    )


def test_join_key_change_ranks_join_primary():
    events = [
        SemanticDiffEvent(
            event_type="join_key_changed",
            family_hint="join_relationship_drift",
            before="o.customer_id = p.customer_id",
            after="o.order_id = p.customer_id",
            changed_columns=("order_id", "customer_id"),
            clause="JOIN",
            confidence=0.95,
            fidelity=0.95,
            source="sqlglot_ast_diff",
        )
    ]
    payload = rank_hypotheses([_finding("join_cardinality", stable_id="AG-JOIN")], events)

    assert payload["analysis_outcome"] == "ranked"
    assert payload["primary_family"] == "join_cardinality"
    assert payload["ranked_hypotheses"][0]["role"] == "primary"
    assert payload["ranked_hypotheses"][0]["change_specificity"] > 0.60


def test_formatting_only_change_produces_silent_pass_no_primary():
    payload = rank_hypotheses(
        [_finding("enum_domain_closure", stable_id="AG-ENUM", blast=False)],
        [],
    )

    assert payload["analysis_outcome"] == "silent_pass"
    assert payload["primary_family"] is None
    assert all(item["role"] == "suppressed" for item in payload["ranked_hypotheses"])


def test_massive_rewrite_circuit_breaker_suppresses_granular_hypotheses():
    events = [
        SemanticDiffEvent(
            event_type="case_else_changed",
            family_hint="enum_domain_closure",
            confidence=0.95,
            fidelity=0.95,
            source="sqlglot_ast_diff",
        )
    ]
    payload = rank_hypotheses(
        [_finding("enum_domain_closure", stable_id="AG-ENUM")],
        events,
        rewrite_stats=RewriteStats(ast_node_change_ratio=0.55),
    )

    assert payload["analysis_outcome"] == "massive_rewrite_circuit_breaker"
    assert payload["circuit_breaker"]["granular_findings_suppressed"] is True
    assert payload["circuit_breaker"]["unranked_assumption_families"] == [
        {"family": "enum_domain_closure", "ranked": False}
    ]
    assert payload["ranked_hypotheses"][0]["role"] == "suppressed"
    assert payload["ranked_hypotheses"][0]["suppression_reason"] == (
        "massive_rewrite_circuit_breaker"
    )


def test_ranking_comparison_artifact_records_old_vs_new_primary(tmp_path):
    old_receipt = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "verdict": "REQUIRE_REVIEW",
        "findings": [
            _finding("join_cardinality", stable_id="AG-JOIN", blast=False),
            _finding("enum_domain_closure", stable_id="AG-ENUM"),
        ],
    }
    events = [
        SemanticDiffEvent(
            event_type="case_else_changed",
            family_hint="enum_domain_closure",
            before="pending",
            after="unresolved",
            changed_columns=("payment_status",),
            clause="CASE",
            confidence=0.95,
            fidelity=0.95,
            source="sqlglot_ast_diff",
        )
    ]
    shadow_receipt = build_shadow_hypothesis_receipt(old_receipt, events)
    comparison = build_ranking_comparison(
        old_receipt,
        shadow_receipt,
        production_comment="Verdict: `REQUIRE_REVIEW` · Review-required: `1` · Advisory: `1`",
    )

    assert comparison["old_top_family"] == "join_cardinality"
    assert comparison["new_primary_family"] == "enum_domain_closure"
    assert comparison["old_must_review_count"] == 1
    assert comparison["new_must_review_count"] == 1
    assert comparison["new_advisory_count"] == 1
    assert comparison["semantic_events"] == ["case_else_changed"]
    assert comparison["ranking_changed"] is True
    assert comparison["silent_pass"] is False
    assert comparison["massive_rewrite_circuit_breaker"] is False
    assert comparison["reviewer_action_delta"] == "would_review_different_finding_first"
    assert comparison["ranking_agreement"] == "disagree_on_primary"
    assert comparison["ranking_confidence"] > 0.0

    artifacts = write_shadow_ranking_artifacts(
        tmp_path,
        old_receipt,
        "Verdict: `REQUIRE_REVIEW` · Review-required: `1` · Advisory: `1`",
        events,
    )

    assert (tmp_path / "shadow_hypothesis_receipt.json").exists()
    assert (tmp_path / "shadow_comment.md").exists()
    assert (tmp_path / "ranking_comparison.json").exists()
    written = json.loads((tmp_path / "ranking_comparison.json").read_text())
    assert written == artifacts["ranking_comparison"]


def test_assumption_ci_writes_shadow_ranking_artifacts(tmp_path):
    manifest = _manifest(tmp_path)
    out = tmp_path / "semzero_out"
    diff = """
--- a/models/staging/stg_events.sql
+++ b/models/staging/stg_events.sql
- event_ts,
+ convert_timezone('UTC','America/New_York', event_ts) as event_ts,
"""

    result = CliRunner().invoke(
        cli,
        [
            "assumption-ci",
            "--dbt-manifest",
            str(manifest),
            "--changed-files",
            "models/staging/stg_events.sql",
            "--changed-diff",
            diff,
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "receipt.json").exists()
    assert (out / "comment.md").exists()
    assert (out / "shadow_hypothesis_receipt.json").exists()
    assert (out / "shadow_comment.md").exists()
    assert (out / "ranking_comparison.json").exists()
    payload = json.loads((out / "shadow_hypothesis_receipt.json").read_text())
    assert payload["kind"] == "semzero_shadow_hypothesis_receipt_v1"
    assert payload["shadow_only"] is True
