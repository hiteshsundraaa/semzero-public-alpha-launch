import json
from pathlib import Path

from semzero.reliability.assumption_dashboard import AssumptionDashboard
from semzero.reliability.assumption_precision import AssumptionPrecisionEvaluator


def _receipt(path: Path):
    payload = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "generated_at": "2026-05-11T00:00:00+00:00",
        "verdict": "REQUIRE_REVIEW",
        "adapter": "dbt_assumption_gate",
        "domain": "data",
        "findings": [
            {
                "id": "AG-INCREMENTAL-FILTER-ABC",
                "stable_id": "AG-INCREMENTAL-FILTER-ABC",
                "legacy_id": "AG-INCREMENTAL-FILTER-001",
                "family": "incremental_filter",
                "severity": "high",
                "risk_score": 91,
                "confidence": "high",
                "trigger_evidence": ["DATE(updated_at) >= DATE(last_run)"],
                "blast_radius": [
                    {
                        "name": "finance_daily_revenue",
                        "unique_id": "model.demo.finance_daily_revenue",
                    }
                ],
                "business_impact": {"highest_business_severity": "REVENUE_CRITICAL"},
                "control_coverage": {"status": "weak"},
                "validation_replay": {
                    "replay_ran": True,
                    "status": "drift_detected",
                    "summary": "Replay Lite selected 10800 rows after vs 1200 before.",
                },
                "replay_fidelity": {"score": 0.82, "level": "replay_lite_fidelity"},
                "cost_estimate": {
                    "estimated_extra_cost_per_run_usd": 100.0,
                    "estimated_extra_cost_per_month_usd": 3000.0,
                },
            },
            {
                "id": "AG-TEMPORAL-BUCKET-XYZ",
                "stable_id": "AG-TEMPORAL-BUCKET-XYZ",
                "legacy_id": "AG-TEMPORAL-BUCKET-001",
                "family": "temporal_bucket",
                "severity": "medium",
                "risk_score": 70,
                "trigger_evidence": ["convert_timezone('UTC','America/New_York', event_ts)"],
                "blast_radius": [
                    {"name": "exec_dashboard", "unique_id": "exposure.demo.exec_dashboard"}
                ],
                "validation_replay": {"replay_ran": False, "status": "not_run"},
                "replay_fidelity": {"score": 0.42, "level": "low_static_fidelity"},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_dashboard_has_replay_aware_summary(tmp_path):
    _receipt(tmp_path / "receipt.json")
    dashboard = AssumptionDashboard(receipt_dir=str(tmp_path)).build()
    assert dashboard["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    replay = dashboard["replay_aware"]
    assert replay["replay_ran_count"] == 1
    assert replay["drift_detected_count"] == 1
    assert replay["replay_not_run_count"] == 1
    assert replay["low_fidelity_count"] >= 1
    assert replay["status_counts"]["drift_detected"] == 1
    assert any(row["family"] == "incremental_filter" for row in replay["family_replay"])


def test_precision_eval_uses_replay_signals(tmp_path):
    _receipt(tmp_path / "receipt.json")
    precision = AssumptionPrecisionEvaluator(receipt_dir=str(tmp_path)).build()
    assert precision["report_kind"] == "semzero_assumption_precision_eval_v1_22"
    assert precision["replay_ran_count"] == 1
    assert precision["replay_drift_count"] == 1
    assert precision["inferred_only_count"] == 1
    states = {row["precision_state"] for row in precision["finding_review_queue"]}
    assert "replay_validated_shadow" in states
    assert "low_fidelity_review" in states
