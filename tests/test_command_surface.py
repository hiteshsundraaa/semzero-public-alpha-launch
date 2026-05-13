from pathlib import Path

from click.testing import CliRunner


def test_commands_command_lists_high_level_and_engine_commands():
    from semzero.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["commands"])

    assert result.exit_code == 0
    assert "Daily / high-level commands" in result.output
    assert "semzero check" in result.output
    assert "semzero gate | wind-tunnel | chaos | premerge | validate-e2e" in result.output


def test_check_command_reads_premerge_bundle():
    from semzero.cli import cli

    runner = CliRunner()
    bundle = "validation_artifacts/run_phase1_0.4.0/premerge/premerge_bundle.json"
    result = runner.invoke(cli, ["check", "--receipt", bundle])

    assert result.exit_code == 0
    assert "Verdict:" in result.output
    assert "Evidence source:" in result.output
    assert bundle in result.output


def test_explain_command_lists_linked_artifacts():
    from semzero.cli import cli

    runner = CliRunner()
    bundle = "validation_artifacts/run_phase1_0.4.0/premerge/premerge_bundle.json"
    result = runner.invoke(cli, ["explain", "--receipt", bundle])

    assert result.exit_code == 0
    assert "Linked artifacts:" in result.output
    assert "premerge_gate_result.json" in result.output


def test_compare_command_surfaces_verdict_delta(tmp_path):
    from semzero.cli import cli

    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(
        '{"verdict":"SAFE","assessments":[],"blocked_by":[],"review_reasons":[],"evaluated_at":"2026-04-05T00:00:00+00:00"}'
    )
    right.write_text(
        '{"verdict":"BLOCK","assessments":[],"blocked_by":["domain filter drift"],"review_reasons":[],"evaluated_at":"2026-04-05T02:00:00+00:00"}'
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["compare", "--left", str(left), "--right", str(right)])

    assert result.exit_code == 0
    assert "Verdict changed: yes" in result.output
    assert "domain filter drift" in result.output


def test_command_doc_exists_and_mentions_both_layers():
    text = Path("docs/COMMANDS.md").read_text(encoding="utf-8")
    assert "Daily / high-level commands" in text
    assert "Expert / engine commands" in text
    assert "semzero check" in text
    assert "semzero gate" in text


def test_report_command_renders_receipt_markdown(tmp_path):
    from semzero.cli import cli

    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        '{"verdict":"BLOCK","assessments":[],"blocked_by":["stale downstream filters"],"review_reasons":[],"evaluated_at":"2026-04-05T00:00:00+00:00"}'
    )
    output = tmp_path / "receipt.md"

    runner = CliRunner()
    result = runner.invoke(
        cli, ["report", "--receipt", str(receipt), "--format", "md", "--output", str(output)]
    )

    assert result.exit_code == 0
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "SemZero Receipt" in text
    assert "stale downstream filters" in text


def test_report_command_writes_composite_receipt_json(tmp_path):
    from semzero.cli import cli

    gate = tmp_path / "gate_result.json"
    gate.write_text(
        '{"verdict":"BLOCK","assessments":[],"blocked_by":["domain enum drift"],"review_reasons":[],"evaluated_at":"2026-04-05T00:00:00+00:00"}'
    )
    wind = tmp_path / "simulation_receipt.json"
    wind.write_text(
        '{"verdict":"BLOCKED","queries_replayed":18,"queries_broken":1,"queries_mismatch":1,"confidence_score":0.9,"completed_at":"2026-04-05T00:10:00+00:00","semantic_risks":[{"description":"rename risk"}]}'
    )
    chaos = tmp_path / "chaos_report.json"
    chaos.write_text(
        '{"summary":{"fragility_grade":"A","mutations_applied":10,"mutations_that_broke":0,"generated_at":"2026-04-05T00:20:00+00:00","fragility_score":99},"mutation_results":[],"recommended_hardening":["increase duplicate coverage"]}'
    )
    output = tmp_path / "semzero_receipt.json"

    runner = CliRunner()
    result = runner.invoke(
        cli, ["report", "--search-dir", str(tmp_path), "--format", "json", "--output", str(output)]
    )

    assert result.exit_code == 0
    payload = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert payload["kind"] == "composite_receipt"
    assert payload["summary"]["queries_replayed"] == 18
