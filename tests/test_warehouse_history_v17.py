from pathlib import Path

from semzero.reliability.warehouse_history import load_warehouse_history, profile_for_resource
from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate, load_cost_profiles

ROOT = Path(__file__).resolve().parents[1]
DOGFOOD = ROOT / "examples" / "dogfood_dbt_assumption_gate"


def test_offline_snowflake_history_profiles_match_model():
    history = load_warehouse_history(DOGFOOD / "warehouse_history" / "snowflake_query_history.csv")
    profile = profile_for_resource(
        history, unique_id="model.dogfood.incremental_events", name="incremental_events"
    )
    assert profile["engine"] == "snowflake"
    assert profile["sample_count"] >= 3
    assert profile["avg_cost_usd"] > 0


def test_assumption_gate_uses_offline_history_for_cost_calibration(tmp_path):
    history = load_warehouse_history(DOGFOOD / "warehouse_history" / "snowflake_query_history.csv")
    gate = DbtAssumptionGate(
        DOGFOOD / "target" / "manifest.json",
        cost_profiles=load_cost_profiles(DOGFOOD / "table_sizes" / "cost_profiles.yml"),
        warehouse_history=history,
    )
    receipt = gate.run(
        ["models/marts/incremental_events.sql"],
        changed_diff=(DOGFOOD / "scenarios" / "02_incremental_filter_cost.diff").read_text(),
    )
    payload = receipt.to_dict()
    assert payload["receipt_kind"] == "dbt_assumption_gate_v1_25"
    inc = [f for f in payload["findings"] if f["family"] == "incremental_filter"][0]
    assert inc["cost_estimate"]["history_calibrated"] is True
    assert inc["cost_estimate"]["method"] in {
        "offline_history_per_run_multiplier",
        "cost_profile_per_run_multiplier",
    }
    assert inc["cost_estimate"]["warehouse_history"]["engine"] == "snowflake"
