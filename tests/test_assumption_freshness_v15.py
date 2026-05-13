from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from semzero.cli import cli
from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate
from tests.test_dbt_assumption_gate_v1 import _manifest


def test_assumption_dashboard_v15_surfaces_stale_high_risk_review_queue(tmp_path: Path):
    manifest = _manifest(tmp_path)
    receipt = DbtAssumptionGate(manifest, table_sizes={"incremental_events": {"gb": 500}}).run(
        ["models/staging/stg_events.sql"], mode="shadow"
    )
    payload = receipt.to_dict()
    payload["generated_at"] = "2026-03-01T00:00:00+00:00"
    receipt_path = tmp_path / "old_receipt.json"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

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
    dashboard = json.loads(out.read_text(encoding="utf-8"))
    assert dashboard["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    freshness = dashboard["freshness"]
    assert freshness["stale_receipt_count"] >= 1
    assert freshness["high_risk_unreviewed_count"] >= 1
    assert freshness["stale_unreviewed_high_risk_count"] >= 1
    assert freshness["review_queue"]
    assert "Freshness / stale-risk review" in md.read_text(encoding="utf-8")
