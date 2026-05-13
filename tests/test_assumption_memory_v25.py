import json
from pathlib import Path

from semzero.reliability.assumption_memory import AssumptionMemoryBuilder, AssumptionMemoryConfig


def _receipt(
    path: Path,
    stable_id: str,
    family: str = "incremental_filter",
    source: str = "model.demo.incremental_events",
    drift: bool = True,
):
    payload = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "generated_at": "2026-05-01T00:00:00+00:00",
        "findings": [
            {
                "id": stable_id,
                "stable_id": stable_id,
                "family": family,
                "severity": "high",
                "risk_score": 93,
                "source": {
                    "unique_id": source,
                    "name": source.split(".")[-1],
                    "metadata": {"owner": "finance-data"},
                },
                "blast_radius": [
                    {
                        "node_type": "dbt_exposure",
                        "name": "executive_revenue_dashboard",
                        "unique_id": "exposure.demo.executive_revenue_dashboard",
                        "metadata": {
                            "owner": "finance-data",
                            "business_severity": "BOARD_CRITICAL",
                        },
                    }
                ],
                "business_impact": {"highest_business_severity": "BOARD_CRITICAL"},
                "validation_replay": {
                    "replay_ran": True,
                    "status": "drift_detected" if drift else "no_drift_detected",
                },
                "replay_fidelity": {"score": 0.9},
                "cost_estimate": {"estimated_extra_cost_per_month_usd": 1200.0},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_assumption_memory_summarizes_family_owner_and_model_patterns(tmp_path):
    _receipt(tmp_path / "a.json", "AG-INCREMENTAL-FILTER-ABC")
    _receipt(tmp_path / "b.json", "AG-INCREMENTAL-FILTER-ABC")
    fb = tmp_path / "assumption_feedback.jsonl"
    fb.write_text(
        json.dumps({"stable_finding_id": "AG-INCREMENTAL-FILTER-ABC", "disposition": "fixed"})
        + "\n",
        encoding="utf-8",
    )
    payload = AssumptionMemoryBuilder(
        AssumptionMemoryConfig(receipt_dir=tmp_path, feedback_file=fb)
    ).build()
    assert payload["memory_kind"] == "semzero_assumption_drift_memory_lite_v1_25"
    assert payload["organization_memory"]["memory_pattern"] == "validated_recurring_drift"
    assert payload["top_family_memory"][0]["name"] == "incremental_filter"
    assert payload["top_owner_or_team_memory"][0]["name"] == "finance-data"
    assert payload["top_source_memory"][0]["name"] == "model.demo.incremental_events"
    assert payload["accuracy_guardrail"].startswith("This report does not add detectors")


def test_assumption_memory_writes_markdown(tmp_path):
    _receipt(
        tmp_path / "a.json",
        "AG-TEMPORAL-BUCKET-XYZ",
        family="temporal_bucket",
        source="model.demo.finance_daily_revenue",
    )
    builder = AssumptionMemoryBuilder(AssumptionMemoryConfig(receipt_dir=tmp_path))
    out = tmp_path / "memory.json"
    md = tmp_path / "memory.md"
    builder.save_json(out)
    builder.save_markdown(md)
    assert out.exists()
    text = md.read_text(encoding="utf-8")
    assert "Assumption Drift Memory Lite" in text
    assert "Family memory" in text
