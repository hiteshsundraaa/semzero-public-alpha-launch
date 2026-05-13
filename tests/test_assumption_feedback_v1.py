from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli
from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate
from tests.test_dbt_assumption_gate_v1 import _manifest


def test_assumption_feedback_cli_records_jsonl_and_dashboard_aggregates(tmp_path: Path):
    manifest = _manifest(tmp_path)
    receipt = DbtAssumptionGate(manifest).run(["models/staging/stg_events.sql"], mode="shadow")
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt.to_dict()), encoding="utf-8")
    feedback_file = tmp_path / "assumption_feedback.jsonl"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "assumption-feedback",
            "--receipt",
            str(receipt_path),
            "--finding-id",
            "AG-TEMPORAL-BUCKET-001",
            "--disposition",
            "agree",
            "--reviewer",
            "analytics@example.com",
            "--comment",
            "This is a real timezone boundary risk.",
            "--feedback-file",
            str(feedback_file),
        ],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in feedback_file.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["disposition"] == "agree"
    assert rows[0]["finding_id"] == "AG-TEMPORAL-BUCKET-001"

    out = tmp_path / "dashboard.json"
    md = tmp_path / "dashboard.md"
    dash = runner.invoke(
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
    assert dash.exit_code == 0, dash.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    assert payload["feedback"]["feedback_count"] == 1
    assert payload["feedback"]["developer_agreement_count"] == 1
    assert payload["developer_agreement_rate"] == 1.0
    assert "Developer feedback records" in md.read_text(encoding="utf-8")
