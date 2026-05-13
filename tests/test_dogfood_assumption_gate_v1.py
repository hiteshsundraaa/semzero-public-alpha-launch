from __future__ import annotations

import json
from pathlib import Path

from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate, load_table_sizes
from semzero.reliability.assumption_dashboard import AssumptionDashboard


DOGFOOD = Path(__file__).resolve().parents[1] / "examples" / "dogfood_dbt_assumption_gate"


def test_dogfood_scenarios_cover_all_core_assumption_families(tmp_path: Path):
    manifest = DOGFOOD / "target" / "manifest.json"
    table_sizes = load_table_sizes(DOGFOOD / "table_sizes" / "table_sizes.json")
    scenarios = json.loads((DOGFOOD / "scenarios" / "scenarios.json").read_text(encoding="utf-8"))[
        "scenarios"
    ]
    gate = DbtAssumptionGate(manifest, table_sizes=table_sizes)

    covered: set[str] = set()
    for scenario in scenarios:
        diff_text = (DOGFOOD / scenario["diff"]).read_text(encoding="utf-8")
        receipt = gate.run(
            [scenario["changed_file"]], mode="shadow", changed_diff=diff_text
        ).to_dict()
        families = {finding["family"] for finding in receipt["findings"]}
        assert scenario["expected_family"] in families, scenario["id"]
        assert receipt["verdict"] == "REQUIRE_REVIEW"
        assert receipt["receipt_kind"] == "dbt_assumption_gate_v1_25"
        covered.update(families)

    assert {
        "temporal_bucket",
        "incremental_filter",
        "join_cardinality",
        "enum_domain_closure",
        "null_default_fallback",
    }.issubset(covered)


def test_dogfood_dashboard_can_aggregate_generated_receipts(tmp_path: Path):
    manifest = DOGFOOD / "target" / "manifest.json"
    scenarios = json.loads((DOGFOOD / "scenarios" / "scenarios.json").read_text(encoding="utf-8"))[
        "scenarios"
    ]
    gate = DbtAssumptionGate(
        manifest, table_sizes=load_table_sizes(DOGFOOD / "table_sizes" / "table_sizes.json")
    )
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    for scenario in scenarios:
        receipt = gate.run(
            [scenario["changed_file"]],
            mode="shadow",
            changed_diff=(DOGFOOD / scenario["diff"]).read_text(encoding="utf-8"),
        ).to_dict()
        (receipt_dir / f"{scenario['id']}.json").write_text(json.dumps(receipt), encoding="utf-8")

    dashboard = AssumptionDashboard(receipt_dir=str(receipt_dir)).build()
    assert dashboard["dashboard_kind"] == "semzero_assumption_dashboard_v1_25"
    assert dashboard["run_count"] == 6
    assert dashboard["assumption_finding_count"] >= 5
    assert dashboard["stable_finding_count"] >= 5
    assert dashboard["roi"]["estimated_cost_exposure_usd_per_run"] is not None
