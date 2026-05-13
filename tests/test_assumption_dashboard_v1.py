from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli
from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate
from tests.test_dbt_assumption_gate_v1 import _manifest


def test_assumption_dashboard_aggregates_typed_receipts(tmp_path: Path):
    manifest = _manifest(tmp_path)
    receipt = DbtAssumptionGate(manifest, table_sizes={"incremental_events": {"gb": 500}}).run(
        ["models/staging/stg_events.sql"], mode="shadow"
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt.to_dict()), encoding="utf-8")

    runner = CliRunner()
    out = tmp_path / "dashboard.json"
    md = tmp_path / "dashboard.md"
    result = runner.invoke(
        cli,
        [
            "assumption-dashboard",
            "--receipt-dir",
            str(tmp_path),
            "--output",
            str(out),
            "--markdown-output",
            str(md),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    assert payload["run_count"] == 1
    assert payload["assumption_finding_count"] >= 1
    assert payload["domain_counts"]["data"] == 1
    assert payload["adapter_counts"]["dbt_assumption_gate"] == 1
    assert "temporal_bucket" in payload["family_counts"]
    assert md.exists()


def test_assumption_dashboard_v17_reports_roi_and_recurring_families(tmp_path: Path):
    manifest = _manifest(tmp_path)
    receipt = DbtAssumptionGate(manifest, table_sizes={"incremental_events": {"gb": 500}}).run(
        ["models/staging/stg_events.sql"], mode="shadow"
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt.to_dict()), encoding="utf-8")
    feedback_file = tmp_path / "assumption_feedback.jsonl"
    feedback_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "receipt": str(receipt_path),
                        "finding_id": "AG-INCREMENTAL-FILTER-001",
                        "disposition": "fixed",
                        "reviewer": "analytics@example.com",
                        "created_at": "2026-05-07T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "receipt": str(receipt_path),
                        "finding_id": "AG-JOIN-CARDINALITY-001",
                        "disposition": "accepted_risk",
                        "reviewer": "analytics@example.com",
                        "created_at": "2026-05-07T00:00:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    out = tmp_path / "dashboard.json"
    md = tmp_path / "dashboard.md"
    result = runner.invoke(
        cli,
        [
            "assumption-dashboard",
            "--receipt-dir",
            str(tmp_path),
            "--feedback-file",
            str(feedback_file),
            "--output",
            str(out),
            "--markdown-output",
            str(md),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    assert payload["roi"]["fixed_finding_count"] >= 1
    assert payload["roi"]["accepted_risk_finding_count"] >= 1
    assert payload["roi"]["estimated_cost_exposure_usd_per_run"] is not None
    assert payload["recurring_assumption_families"]
    assert payload["trend"]["daily_feedback_counts"]["2026-05-07"] == 2
    markdown = md.read_text(encoding="utf-8")
    assert "ROI / value signals" in markdown
    assert "Top recurring assumption families" in markdown


def test_assumption_dashboard_v18_recommends_policy_tuning_from_feedback(tmp_path: Path):
    manifest = _manifest(tmp_path)
    receipt = DbtAssumptionGate(manifest, table_sizes={"incremental_events": {"gb": 500}}).run(
        ["models/staging/stg_events.sql"], mode="shadow"
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt.to_dict()), encoding="utf-8")
    feedback_file = tmp_path / "assumption_feedback.jsonl"
    feedback_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "receipt": str(receipt_path),
                        "finding_id": "AG-INCREMENTAL-FILTER-001",
                        "disposition": "fixed",
                        "reviewer": "analytics@example.com",
                        "created_at": "2026-05-07T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "receipt": str(receipt_path),
                        "finding_id": "AG-INCREMENTAL-FILTER-001",
                        "disposition": "agree",
                        "reviewer": "lead@example.com",
                        "created_at": "2026-05-07T00:01:00Z",
                    }
                ),
                json.dumps(
                    {
                        "receipt": str(receipt_path),
                        "finding_id": "AG-TEMPORAL-BUCKET-001",
                        "disposition": "false_positive",
                        "reviewer": "analytics@example.com",
                        "created_at": "2026-05-07T00:02:00Z",
                    }
                ),
                json.dumps(
                    {
                        "receipt": str(receipt_path),
                        "finding_id": "AG-TEMPORAL-BUCKET-001",
                        "disposition": "false_positive",
                        "reviewer": "lead@example.com",
                        "created_at": "2026-05-07T00:03:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    out = tmp_path / "dashboard.json"
    md = tmp_path / "dashboard.md"
    result = runner.invoke(
        cli,
        [
            "assumption-dashboard",
            "--receipt-dir",
            str(tmp_path),
            "--feedback-file",
            str(feedback_file),
            "--output",
            str(out),
            "--markdown-output",
            str(md),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    policy = payload["policy_recommendations"]
    assert policy["kind"] == "semzero_policy_calibration_v1"
    assert policy["auto_applied"] is False
    actions = {
        row["family"]: row["recommended_policy_action"] for row in policy["family_recommendations"]
    }
    assert actions["incremental_filter"] in {
        "require_review_candidate",
        "keep_shadow_collect_feedback",
        "advisory_candidate",
    }
    assert actions["temporal_bucket"] == "lower_severity_or_suppress_candidate"
    assert "Policy calibration recommendations" in md.read_text(encoding="utf-8")
