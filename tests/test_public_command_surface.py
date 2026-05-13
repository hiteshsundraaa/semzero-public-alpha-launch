from click.testing import CliRunner

from semzero.cli import cli


PUBLIC_COMMANDS = {
    "init-assumption-ci",
    "assumption-ci",
    "assumption-gate",
    "assumption-dashboard",
    "assumption-feedback",
    "assumption-exception",
    "assumption-precision-eval",
    "assumption-lineage",
    "assumption-decay",
    "assumption-memory",
    "quickstart",
    "demo",
    "doctor-assumption-ci",
}


def test_public_commands_exist():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in PUBLIC_COMMANDS:
        assert command in result.output
