import json
from pathlib import Path

from click.testing import CliRunner
from semzero.cli import cli


def test_assumption_diff_and_replay_fidelity_present(tmp_path):
    manifest = Path("examples/dogfood_dbt_assumption_gate/target/manifest.json")
    diff = Path("examples/dogfood_dbt_assumption_gate/scenarios/02_incremental_filter_cost.diff")
    out = tmp_path / "receipt.json"
    comment = tmp_path / "comment.md"
    result = CliRunner().invoke(
        cli,
        [
            "assumption-gate",
            "--dbt-manifest",
            str(manifest),
            "--changed-file",
            "models/marts/incremental_events.sql",
            "--changed-diff",
            str(diff),
            "--warehouse-history",
            "examples/dogfood_dbt_assumption_gate/warehouse_history/snowflake_query_history.csv",
            "--cost-profiles",
            "examples/dogfood_dbt_assumption_gate/table_sizes/cost_profiles.yml",
            "--output",
            str(out),
            "--comment-out",
            str(comment),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["receipt_kind"] == "dbt_assumption_gate_v1_25"
    assert payload["summary"]["assumption_diff_summary"]["finding_count"] >= 1
    assert payload["summary"]["replay_fidelity_summary"]["average_score"] is not None
    finding = next(f for f in payload["findings"] if f["family"] == "incremental_filter")
    assert finding["assumption_diff"]["drift_type"] == "incremental_predicate_selectivity"
    assert finding["replay_fidelity"]["replay_ran"] is False
    assert "No before/after output replay" in " ".join(finding["replay_fidelity"]["limitations"])
    text = comment.read_text()
    assert "Assumption drift" in text
    assert "Evidence fidelity" in text
