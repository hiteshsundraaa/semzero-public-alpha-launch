from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_killer_demo_script_generates_receipt_and_comment():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/run_killer_demo.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "SemZero killer demo PR" in result.stdout
    assert "Family: temporal_bucket" in result.stdout
    assert "Replay Lite: drift_detected" in result.stdout

    receipt_path = root / "examples" / "killer_demo_pr" / "output" / "receipt.json"
    comment_path = root / "examples" / "killer_demo_pr" / "output" / "comment.md"
    assert receipt_path.exists()
    assert comment_path.exists()

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["verdict"] == "REQUIRE_REVIEW"
    assert receipt["findings"][0]["family"] == "temporal_bucket"
    assert receipt["findings"][0]["validation_replay"]["status"] == "drift_detected"
    assert "executive_revenue_dashboard" in comment_path.read_text(encoding="utf-8")
