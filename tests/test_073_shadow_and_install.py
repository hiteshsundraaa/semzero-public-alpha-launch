from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli


def test_shadow_command_invokes_premerge_shadow_mode(tmp_path):
    graph = tmp_path / "graph.json"
    drift = tmp_path / "drift.json"
    graph.write_text('{"nodes": [], "edges": []}')
    drift.write_text('{"changes": []}')
    out = tmp_path / "bundle.json"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "shadow",
            "--graph",
            str(graph),
            "--drift",
            str(drift),
            "--live-mode",
            "metadata-only",
            "--output",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = out.read_text()
    assert '"shadow_mode": true' in payload


def test_premerge_accepts_shadow_mode_alias(tmp_path):
    graph = tmp_path / "graph.json"
    drift = tmp_path / "drift.json"
    graph.write_text('{"nodes": [], "edges": []}')
    drift.write_text('{"changes": []}')
    out = tmp_path / "bundle.json"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "premerge",
            "--graph",
            str(graph),
            "--drift",
            str(drift),
            "--live-mode",
            "metadata-only",
            "--shadow-mode",
            "--output",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "shadow_mode" in out.read_text()


def test_python_module_entrypoint_exists():
    assert Path("semzero/__main__.py").exists()
    assert Path("src/__main__.py").exists()


def test_install_scripts_exist():
    assert Path("scripts/install_semzero.sh").exists()
    assert Path("scripts/install_semzero.ps1").exists()
    assert Path("scripts/build_release.sh").exists()


def test_pyproject_has_databricks_extra():
    text = Path("pyproject.toml").read_text()
    assert "databricks" in text
    assert "databricks-sql-connector[sqlalchemy]" in text
