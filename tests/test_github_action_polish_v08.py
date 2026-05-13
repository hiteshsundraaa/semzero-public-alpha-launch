from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli


def test_init_assumption_ci_scaffolds_sticky_shadow_workflow_and_examples(tmp_path: Path):
    result = CliRunner().invoke(cli, ["init-assumption-ci", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    workflow = (tmp_path / ".github" / "workflows" / "semzero_assumption_gate.yml").read_text(
        encoding="utf-8"
    )
    assert "mode shadow" in workflow
    assert "sticky SemZero review" in workflow
    assert "semzero assumption-ci" in workflow
    assert "--project-dir ." in workflow
    assert "--replay-fixtures .semzero/replay_fixtures.json" in workflow
    assert "--criticality-registry .semzero/business_criticality.yml" in workflow
    assert "SemZero ran in **shadow mode**" in workflow
    assert "const artifactNote = '\\n\\n---\\nSemZero ran in **shadow mode**" in workflow
    assert "const finalBody = `${marker}\\n${body}${artifactNote}`;" in workflow
    assert "const artifactNote = '\\n\n---" not in workflow
    assert "const finalBody = `${marker}\n${body}" not in workflow
    assert "concurrency:" in workflow

    assert "cache: pip" not in workflow
    assert "SEMZERO_PACKAGE_SPEC" in workflow
    assert "pip install -e ." in workflow
    assert '[ -f semzero/cli.py ] && grep -q "init-assumption-ci" semzero/cli.py' in workflow
    assert "SemZero could not find target/manifest.json" in workflow
    assert "command -v dbt" in workflow
    assert (tmp_path / ".semzero" / "assumption_gate_policy.yml").exists()
    assert (tmp_path / ".semzero" / "README.md").exists()
    assert (tmp_path / ".semzero" / "replay_fixtures.example.json").exists()
    assert (tmp_path / ".semzero" / "cost_profiles.example.yml").exists()
    assert (tmp_path / ".semzero" / "business_criticality.example.yml").exists()
    assert (tmp_path / ".semzero" / "assumption_exceptions.example.jsonl").exists()


def test_packaged_composite_action_exposes_same_core_evidence_inputs():
    action = Path(".github/actions/semzero-assumption-gate/action.yml").read_text(encoding="utf-8")
    assert "replay-fixtures" in action
    assert "dbt-catalog" in action
    assert "dbt-run-results" in action
    assert "project-dir" in action
    assert "exceptions-file" in action
    assert "--write-github-summary" in action
    assert "--no-write-github-summary" in action
