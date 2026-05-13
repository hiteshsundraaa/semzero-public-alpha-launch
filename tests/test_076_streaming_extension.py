from pathlib import Path
import json
from click.testing import CliRunner

from semzero.cli import cli
from semzero.integrations.streaming_gate import StreamingGate


def _payloads():
    before = json.loads(Path("examples/streaming/before_topics.json").read_text())
    after = json.loads(Path("examples/streaming/after_topics.json").read_text())
    contracts = json.loads(Path("examples/streaming/consumer_contracts.json").read_text())
    return before, after, contracts


def test_streaming_gate_detects_schema_contract_and_event_time_risk():
    before, after, contracts = _payloads()
    result = StreamingGate(before, after, contracts).evaluate(
        repo="stream-repo", team="stream-platform", shadow_mode=True
    )
    assert result["verdict"] == "BLOCK"
    assert result["iron_gate"]["should_block_merge"] is False
    categories = set(result["decision_summary"]["risk_categories"])
    assert "schema_evolution" in categories
    assert "consumer_contract" in categories
    assert "assumption" in categories
    assert "operational" in categories
    assert result["streaming_summary"]["finding_count"] >= 6
    assert result["remediation_blueprints"]


def test_streaming_shadow_cli_writes_reports_and_dashboard(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "streaming-shadow",
            "--before",
            "examples/streaming/before_topics.json",
            "--after",
            "examples/streaming/after_topics.json",
            "--contracts",
            "examples/streaming/consumer_contracts.json",
            "--repo",
            "stream-repo",
            "--team",
            "stream-platform",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Streaming shadow verdict: BLOCK" in result.output
    gate_path = tmp_path / "streaming_gate_result.json"
    dashboard_path = tmp_path / "shadow_dashboard.json"
    assert gate_path.exists()
    assert dashboard_path.exists()
    payload = json.loads(gate_path.read_text())
    dashboard = json.loads(dashboard_path.read_text())
    assert payload["shadow_mode"] is True
    assert dashboard["run_count"] == 1
    assert dashboard["repo_trends"][0]["repo"] == "stream-repo"
