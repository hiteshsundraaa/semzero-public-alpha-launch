from __future__ import annotations

import json
from pathlib import Path

from semzero.reliability.assumption_precision import AssumptionPrecisionEvaluator, PrecisionConfig


def test_precision_eval_flags_missing_trigger_and_validated_cost(tmp_path: Path):
    receipt = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "generated_at": "2026-05-10T00:00:00+00:00",
        "findings": [
            {
                "id": "AG-INCREMENTAL-FILTER-AAA",
                "stable_id": "AG-INCREMENTAL-FILTER-AAA",
                "legacy_id": "AG-INCREMENTAL-FILTER-001",
                "family": "incremental_filter",
                "severity": "high",
                "risk_score": 92,
                "confidence": "high",
                "trigger_evidence": ["DATE(updated_at) >= DATE(last_run)"],
                "blast_radius": [
                    {"name": "finance_daily_revenue", "node_type": "dbt_model", "domain": "data"}
                ],
                "cost_estimate": {
                    "estimated_extra_cost_per_run_usd": 40,
                    "estimated_extra_cost_per_month_usd": 1200,
                },
                "business_impact": {"highest_business_severity": "REVENUE_CRITICAL"},
                "control_coverage": {"status": "weak"},
            },
            {
                "id": "AG-TEMPORAL-BUCKET-BBB",
                "stable_id": "AG-TEMPORAL-BUCKET-BBB",
                "legacy_id": "AG-TEMPORAL-BUCKET-001",
                "family": "temporal_bucket",
                "severity": "medium",
                "risk_score": 50,
                "trigger_evidence": [],
                "blast_radius": [],
            },
        ],
    }
    (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (tmp_path / "assumption_feedback.jsonl").write_text(
        json.dumps(
            {
                "stable_finding_id": "AG-INCREMENTAL-FILTER-AAA",
                "disposition": "fixed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = AssumptionPrecisionEvaluator(PrecisionConfig(receipt_dir=tmp_path)).build()
    assert report["finding_count"] == 2
    assert report["developer_validated_count"] == 1
    assert report["missing_trigger_evidence_count"] == 1
    states = {row["stable_id"]: row["precision_state"] for row in report["finding_review_queue"]}
    assert states["AG-INCREMENTAL-FILTER-AAA"] == "developer_validated"
    assert states["AG-TEMPORAL-BUCKET-BBB"] == "insufficient_evidence"


def test_precision_eval_writes_markdown(tmp_path: Path):
    (tmp_path / "receipt.json").write_text(
        json.dumps({"receipt_kind": "dbt_assumption_gate_v1_25", "findings": []}), encoding="utf-8"
    )
    evaluator = AssumptionPrecisionEvaluator(PrecisionConfig(receipt_dir=tmp_path))
    out = tmp_path / "precision.md"
    text = evaluator.save_markdown(out)
    assert out.exists()
    assert "Precision summary" in text
