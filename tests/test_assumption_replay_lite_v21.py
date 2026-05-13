import json
from pathlib import Path
from click.testing import CliRunner
from semzero.cli import cli
from semzero.reliability.assumption_replay_lite import load_replay_fixtures, run_replay_lite


def test_replay_lite_family_checks_detect_drift():
    fixtures = load_replay_fixtures(
        "examples/dogfood_dbt_assumption_gate/replay_fixtures/replay_lite_samples.json"
    )
    inc = run_replay_lite(fixtures, "incremental_filter")
    join = run_replay_lite(fixtures, "join_cardinality")
    assert inc["replay_ran"] is True
    assert inc["status"] == "drift_detected"
    assert inc["drift_metric"] > 1
    assert join["status"] == "drift_detected"


def test_assumption_gate_attaches_validation_replay(tmp_path: Path):
    out = tmp_path / "receipt.json"
    comment = tmp_path / "comment.md"
    result = CliRunner().invoke(
        cli,
        [
            "assumption-gate",
            "--dbt-manifest",
            "examples/dogfood_dbt_assumption_gate/target/manifest.json",
            "--changed-file",
            "models/marts/incremental_events.sql",
            "--changed-diff",
            "examples/dogfood_dbt_assumption_gate/scenarios/02_incremental_filter_cost.diff",
            "--warehouse-history",
            "examples/dogfood_dbt_assumption_gate/warehouse_history/snowflake_query_history.csv",
            "--cost-profiles",
            "examples/dogfood_dbt_assumption_gate/table_sizes/cost_profiles.yml",
            "--replay-fixtures",
            "examples/dogfood_dbt_assumption_gate/replay_fixtures/replay_lite_samples.json",
            "--output",
            str(out),
            "--comment-out",
            str(comment),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["receipt_kind"] == "dbt_assumption_gate_v1_25"
    assert payload["summary"]["validation_replay_summary"]["replay_ran_count"] >= 1
    finding = next(f for f in payload["findings"] if f["family"] == "incremental_filter")
    assert finding["validation_replay"]["replay_ran"] is True
    assert finding["validation_replay"]["status"] == "drift_detected"
    assert finding["replay_fidelity"]["replay_ran"] is True
    text = comment.read_text()
    assert "Validation replay lite" in text
    assert "Validation replay" in text
