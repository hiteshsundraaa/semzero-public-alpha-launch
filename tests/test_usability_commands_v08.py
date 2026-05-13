from click.testing import CliRunner

from semzero.cli import cli


def test_quickstart_command_explains_first_user_path():
    result = CliRunner().invoke(cli, ["quickstart", "--repo", "."])
    assert result.exit_code == 0
    assert "SemZero quickstart" in result.output
    assert "semzero init-assumption-ci" in result.output
    assert "python scripts/run_killer_demo.py" in result.output


def test_demo_command_has_friendly_fallback():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["demo"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "SemZero killer demo" in result.output
    assert "git clone" in result.output


def test_doctor_assumption_ci_reports_missing_setup(tmp_path):
    result = CliRunner().invoke(cli, ["doctor-assumption-ci", "--repo", str(tmp_path)])
    assert result.exit_code != 0
    assert "SemZero Assumption CI doctor" in result.output
    assert "dbt_project.yml" in result.output
    assert "semzero init-assumption-ci" in result.output


def test_init_assumption_ci_prints_next_steps(tmp_path):
    result = CliRunner().invoke(cli, ["init-assumption-ci", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Next steps" in result.output
    assert "doctor-assumption-ci" in result.output
    assert (tmp_path / ".github" / "workflows" / "semzero_assumption_gate.yml").exists()
    assert (tmp_path / ".semzero" / "README.md").exists()
