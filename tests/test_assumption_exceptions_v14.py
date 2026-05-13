from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from click.testing import CliRunner

from semzero.cli import cli


def test_assumption_exception_cli_records_reason_required_exception(tmp_path):
    ledger = tmp_path / "assumption_exceptions.jsonl"
    result = CliRunner().invoke(
        cli,
        [
            "assumption-exception",
            "--scope",
            "stable_id",
            "--value",
            "AG-INCREMENTAL-FILTER-ABC123",
            "--reason",
            "Accepted during backfill window; revisit after migration.",
            "--owner",
            "data-platform",
            "--expires-at",
            (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "--exceptions-file",
            str(ledger),
        ],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["status"] == "active"
    assert rows[0]["reason"]


def test_assumption_gate_marks_active_exception_without_removing_finding(tmp_path):
    from tests.test_dbt_assumption_gate_v1 import _manifest
    from semzero.reliability.assumption_exceptions import (
        AssumptionExceptionRecord,
        append_exception,
    )
    from semzero.integrations.dbt_assumption_gate import (
        DbtAssumptionGate,
        load_assumption_exceptions,
    )

    manifest = _manifest(tmp_path)
    first = DbtAssumptionGate(manifest).run(
        ["models/staging/stg_events.sql"],
        changed_diff="+ convert_timezone('UTC','America/New_York', event_ts) as event_ts",
    )
    finding = first.to_dict()["findings"][0]
    ledger = tmp_path / "assumption_exceptions.jsonl"
    append_exception(
        AssumptionExceptionRecord(
            scope="stable_id",
            value=finding["stable_id"],
            reason="Known migration; dashboard owner accepted temporary bucket shift.",
            owner="finance-data",
            expires_at=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        ),
        ledger,
    )
    second = DbtAssumptionGate(manifest, exceptions=load_assumption_exceptions(ledger)).run(
        ["models/staging/stg_events.sql"],
        changed_diff="+ convert_timezone('UTC','America/New_York', event_ts) as event_ts",
    )
    payload = second.to_dict()
    assert payload["summary"]["finding_count"] >= 1
    assert payload["summary"]["exception_summary"]["active_exception_match_count"] >= 1
    assert any(f["exception"]["state"] == "active_exception" for f in payload["findings"])


def test_dashboard_tracks_expired_exception_debt(tmp_path):
    from semzero.reliability.assumption_dashboard import AssumptionDashboard
    from semzero.reliability.assumption_exceptions import (
        AssumptionExceptionRecord,
        append_exception,
    )

    receipt = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "REQUIRE_REVIEW",
        "adapter": "dbt_assumption_gate",
        "domain": "data",
        "findings": [
            {
                "id": "AG-TEMPORAL-BUCKET-XYZ",
                "stable_id": "AG-TEMPORAL-BUCKET-XYZ",
                "legacy_id": "AG-TEMPORAL-BUCKET-001",
                "family": "temporal_bucket",
                "severity": "high",
                "risk_score": 80,
                "source": {"unique_id": "model.demo.finance_daily_revenue"},
                "blast_radius": [],
            }
        ],
    }
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "receipt.json").write_text(json.dumps(receipt))
    ledger = receipt_dir / "assumption_exceptions.jsonl"
    append_exception(
        AssumptionExceptionRecord(
            scope="stable_id",
            value="AG-TEMPORAL-BUCKET-XYZ",
            reason="Old exception should be revisited.",
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        ),
        ledger,
    )
    dashboard = AssumptionDashboard(receipt_dir=receipt_dir).build()
    assert dashboard["exceptions"]["expired_exception_count"] == 1
    assert dashboard["exceptions"]["expired_exception_finding_count"] == 1
