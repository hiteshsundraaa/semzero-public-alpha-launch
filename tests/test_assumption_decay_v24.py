import json
from pathlib import Path

from semzero.reliability.assumption_decay import AssumptionDecayConfig, AssumptionDecayTracker


def _receipt(path: Path, stable_id: str, family: str = "incremental_filter", drift: bool = True):
    payload = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "generated_at": "2026-05-01T00:00:00+00:00",
        "findings": [
            {
                "id": stable_id,
                "stable_id": stable_id,
                "family": family,
                "severity": "high",
                "risk_score": 92,
                "assumption": "Incremental predicate remains selective.",
                "business_impact": {"highest_business_severity": "REVENUE_CRITICAL"},
                "validation_replay": {
                    "replay_ran": True,
                    "status": "drift_detected" if drift else "no_drift_detected",
                },
                "replay_fidelity": {"score": 0.82},
                "assumption_diff": {"drift_summary": "Predicate became less selective."},
                "source": {
                    "unique_id": "model.demo.incremental_events",
                    "path": "models/incremental_events.sql",
                },
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_decay_tracker_flags_recurring_replay_validated_drift(tmp_path):
    _receipt(tmp_path / "a.json", "AG-INCREMENTAL-FILTER-ABC")
    _receipt(tmp_path / "b.json", "AG-INCREMENTAL-FILTER-ABC")
    fb = tmp_path / "assumption_feedback.jsonl"
    fb.write_text(
        json.dumps({"stable_finding_id": "AG-INCREMENTAL-FILTER-ABC", "disposition": "fixed"})
        + "\n",
        encoding="utf-8",
    )
    payload = AssumptionDecayTracker(
        AssumptionDecayConfig(receipt_dir=tmp_path, feedback_file=fb)
    ).build()
    assert payload["decay_kind"] == "semzero_assumption_decay_lite_v1_25"
    top = payload["top_decay_assumptions"][0]
    assert top["stable_id"] == "AG-INCREMENTAL-FILTER-ABC"
    assert "recurring_replay_validated_drift" in top["decay_signals"]
    assert top["decay_state"] == "decaying_high_confidence"


def test_decay_tracker_writes_markdown(tmp_path):
    _receipt(tmp_path / "a.json", "AG-TEMPORAL-BUCKET-XYZ", family="temporal_bucket")
    tracker = AssumptionDecayTracker(AssumptionDecayConfig(receipt_dir=tmp_path))
    out = tmp_path / "decay.json"
    md = tmp_path / "decay.md"
    tracker.save_json(out)
    tracker.save_markdown(md)
    assert out.exists()
    assert "Assumption Decay Tracking Lite" in md.read_text(encoding="utf-8")
