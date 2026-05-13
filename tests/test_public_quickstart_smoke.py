import json
import subprocess
from pathlib import Path


def run(cmd: str, cwd: Path):
    return subprocess.run(
        cmd,
        cwd=cwd,
        shell=True,
        text=True,
        capture_output=True,
        timeout=60,
    )


def test_cli_help_works():
    result = run("python -m semzero --help", cwd=Path.cwd())
    assert result.returncode == 0, result.stderr
    assert "assumption-ci" in result.stdout
    assert "init-assumption-ci" in result.stdout


def test_init_assumption_ci_creates_expected_files(tmp_path):
    result = run(f"python -m semzero init-assumption-ci --output-dir {tmp_path}", cwd=Path.cwd())
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".github" / "workflows" / "semzero_assumption_gate.yml").exists()
    assert (tmp_path / ".semzero" / "assumption_gate_policy.yml").exists()
    assert (tmp_path / ".semzero" / "README.md").exists()


def test_killer_demo_runs():
    result = run("python scripts/run_killer_demo.py", cwd=Path.cwd())
    assert result.returncode == 0, result.stderr
    receipt = Path("examples/killer_demo_pr/output/receipt.json")
    assert receipt.exists()
    data = json.loads(receipt.read_text())
    assert data.get("findings")
    assert data["findings"][0].get("family") == "temporal_bucket"
