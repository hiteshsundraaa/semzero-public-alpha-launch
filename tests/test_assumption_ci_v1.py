from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli
from tests.test_dbt_assumption_gate_v1 import _manifest


def test_assumption_ci_accepts_explicit_changed_files_and_writes_artifacts(tmp_path: Path):
    manifest = _manifest(tmp_path)
    out_dir = tmp_path / "artifacts"
    diff = """
--- a/models/staging/stg_events.sql
+++ b/models/staging/stg_events.sql
+ select convert_timezone('UTC','America/New_York', event_ts) as event_ts
"""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "assumption-ci",
            "--dbt-manifest",
            str(manifest),
            "--changed-files",
            "models/staging/stg_events.sql",
            "--changed-diff",
            diff,
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads((out_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["receipt_kind"] == "dbt_assumption_gate_v1_25"
    assert receipt["verdict"] == "REQUIRE_REVIEW"
    assert (out_dir / "comment.md").exists()
    assert (out_dir / "changed_files.txt").read_text(
        encoding="utf-8"
    ).strip() == "models/staging/stg_events.sql"
    assert "SemZero Assumption Gate" in (out_dir / "comment.md").read_text(encoding="utf-8")


def test_assumption_ci_noops_when_no_dbt_files_changed(tmp_path: Path):
    manifest = _manifest(tmp_path)
    out_dir = tmp_path / "artifacts"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "assumption-ci",
            "--dbt-manifest",
            str(manifest),
            "--changed-files",
            "README.md",
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads((out_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["receipt_kind"] == "dbt_assumption_gate_ci_noop_v1"
    assert receipt["verdict"] == "ALLOW"
    assert receipt["summary"]["finding_count"] == 0


def test_init_assumption_ci_scaffolds_focused_workflow(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init-assumption-ci", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    workflow = tmp_path / ".github" / "workflows" / "semzero_assumption_gate.yml"
    policy = tmp_path / ".semzero" / "assumption_gate_policy.yml"
    assert workflow.exists()
    assert policy.exists()
    body = workflow.read_text(encoding="utf-8")
    assert "semzero assumption-ci" in body
    assert "target/manifest.json" in body
    assert "SEMZERO_PACKAGE_SPEC" in body
    assert "cache: pip" not in body
    assert "SemZero could not find target/manifest.json" in body
