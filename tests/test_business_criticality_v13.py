from __future__ import annotations

import json
from pathlib import Path

from semzero.integrations.dbt_assumption_gate import (
    DbtAssumptionGate,
    load_business_criticality,
    load_cost_profiles,
    load_table_sizes,
)
from semzero.reliability.assumption_dashboard import AssumptionDashboard

DOGFOOD = Path(__file__).resolve().parents[1] / "examples" / "dogfood_dbt_assumption_gate"


def test_v13_business_criticality_and_control_coverage_are_advisory(tmp_path: Path):
    gate = DbtAssumptionGate(
        DOGFOOD / "target" / "manifest.json",
        table_sizes=load_table_sizes(DOGFOOD / "table_sizes" / "table_sizes.json"),
        cost_profiles=load_cost_profiles(DOGFOOD / "table_sizes" / "cost_profiles.yml"),
        criticality_registry=load_business_criticality(
            DOGFOOD / "criticality" / "business_criticality.yml"
        ),
    )
    diff = (DOGFOOD / "scenarios" / "01_temporal_bucket_timezone.diff").read_text(encoding="utf-8")
    receipt = gate.run(["models/staging/stg_events.sql"], changed_diff=diff).to_dict()
    assert receipt["receipt_kind"] == "dbt_assumption_gate_v1_25"
    assert receipt["verdict"] == "REQUIRE_REVIEW"
    assert receipt["summary"]["business_impact"]["highest_business_severity"] == "BOARD_CRITICAL"
    finding = receipt["findings"][0]
    assert finding["business_impact"]["highest_business_severity"] in {
        "BOARD_CRITICAL",
        "REVENUE_CRITICAL",
        "EXEC_CRITICAL",
    }
    assert finding["control_coverage"]["kind"] == "assumption_control_coverage_v1"
    assert finding["control_coverage"]["status"] in {"weak", "partial", "covered"}
    assert finding["incident_chain"][0]["kind"] == "changed_or_scanned_resource"

    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(receipt), encoding="utf-8")
    dashboard = AssumptionDashboard(receipt_dir=str(tmp_path)).build()
    assert dashboard["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    assert dashboard["business_severity_counts"]
    assert dashboard["control_coverage_counts"]
