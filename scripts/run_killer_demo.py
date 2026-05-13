#!/usr/bin/env python3
"""Run the focused SemZero killer demo PR.

The demo shows a harmless-looking dbt timestamp change that can silently move
revenue between reporting days. It runs fully offline and writes a receipt plus
PR-style comment under examples/killer_demo_pr/output/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semzero.integrations.dbt_assumption_gate import (
    DbtAssumptionGate,
    load_business_criticality,
    load_replay_fixtures,
    render_pr_comment,
)

DEMO = ROOT / "examples" / "killer_demo_pr"


def main() -> int:
    output_dir = DEMO / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = DEMO / "target" / "manifest.json"
    diff_path = DEMO / "pr.diff"
    replay_path = DEMO / "replay_fixtures" / "replay_lite_samples.json"
    criticality_path = DEMO / "business_criticality.yml"

    gate = DbtAssumptionGate(
        manifest,
        replay_fixtures=load_replay_fixtures(replay_path),
        criticality_registry=load_business_criticality(criticality_path),
    )
    receipt = gate.run(
        ["models/staging/stg_events.sql"],
        mode="shadow",
        changed_diff=diff_path.read_text(encoding="utf-8"),
    )
    payload = receipt.to_dict()
    comment = render_pr_comment(receipt, max_findings=3)

    receipt_path = output_dir / "receipt.json"
    comment_path = output_dir / "comment.md"
    receipt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    comment_path.write_text(comment, encoding="utf-8")

    findings = payload.get("findings", [])
    first = findings[0] if findings else {}
    replay = first.get("validation_replay") or {}
    blast = first.get("blast_radius") or []

    print("SemZero killer demo PR")
    print("=======================")
    print(f"Verdict: {payload.get('verdict')}")
    print(f"Findings: {len(findings)}")
    if first:
        print(f"Family: {first.get('family')}")
        print(f"Severity: {first.get('severity')} · risk {first.get('risk_score')}/100")
        print(f"Replay Lite: {replay.get('status', 'not_run')}")
        print(f"Replay summary: {replay.get('summary', 'No replay summary')}")
        print("Blast radius:")
        for node in blast[:5]:
            print(
                f"- {node.get('name') or node.get('unique_id')} ({node.get('type') or node.get('node_type')})"
            )
    print("Generated:")
    print(f"- {receipt_path.relative_to(ROOT)}")
    print(f"- {comment_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
