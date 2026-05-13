from click.testing import CliRunner
from pathlib import Path
import json

from semzero_lab.cli import cli


def test_expected_migration_calibration(tmp_path: Path):
    runner = CliRunner()
    dataset = tmp_path / "bench"
    run = tmp_path / "run"
    result = runner.invoke(
        cli, ["generate-datafold-benchmark", "--count", "30", "--out", str(dataset), "--force"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        cli, ["run-datafold-benchmark", "--dataset", str(dataset), "--out", str(run), "--force"]
    )
    assert result.exit_code == 0, result.output
    summary = json.loads((run / "run_summary.json").read_text())
    assert summary["expected_migration_exact_accuracy"] >= 0.90
    expected = summary["by_class"]["expected_migration_noise_control"]
    # The bug we are fixing: expected migrations must not all disappear into ALLOW.
    assert expected["predicted_verdicts"].get("ALLOW", 0) == 0
    assert expected["predicted_verdicts"].get("ADVISORY", 0) > 0
    assert expected["predicted_verdicts"].get("REQUIRE_REVIEW", 0) > 0


def test_datafold_benchmark_exports(tmp_path: Path):
    runner = CliRunner()
    dataset = tmp_path / "bench"
    run = tmp_path / "run"
    features = tmp_path / "features.jsonl"
    graph = tmp_path / "graph.jsonl"
    assert (
        runner.invoke(
            cli, ["generate-datafold-benchmark", "--count", "24", "--out", str(dataset), "--force"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli, ["run-datafold-benchmark", "--dataset", str(dataset), "--out", str(run), "--force"]
        ).exit_code
        == 0
    )
    assert runner.invoke(cli, ["evaluate-datafold-benchmark", "--run", str(run)]).exit_code == 0
    assert (
        runner.invoke(
            cli, ["export-datafold-features", "--run", str(run), "--out", str(features)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli, ["export-datafold-graph", "--dataset", str(dataset), "--out", str(graph)]
        ).exit_code
        == 0
    )
    assert features.exists() and len(features.read_text().splitlines()) == 24
    assert graph.exists() and len(graph.read_text().splitlines()) == 24
