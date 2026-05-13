from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli
from semzero.reliability.validation import build_demo_validation_pack


def test_demo_pack_black_swan_builds_heavier_assets(tmp_path: Path):
    pack = build_demo_validation_pack(tmp_path, scale="small", profile="black_swan")
    workload = Path(pack.workload_path).read_text(encoding="utf-8")
    proof_root = Path(pack.proof_paths[0])
    drift = Path(pack.drift_path).read_text(encoding="utf-8")
    assert "subscription_events" in workload
    assert "support_tickets" in workload
    assert "refunds" in workload
    assert (proof_root / "subscription_margin.sql").exists()
    assert (proof_root / "ticket_risk.py").exists()
    assert "support_tickets.severity" in drift
    assert "subscription_events.mrr" in drift


def test_validate_e2e_accepts_black_swan_profile(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "report.json"
    result = runner.invoke(
        cli,
        [
            "validate-e2e",
            "--demo-pack-dir",
            str(tmp_path / "demo"),
            "--demo-scale",
            "small",
            "--demo-profile",
            "black_swan",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
