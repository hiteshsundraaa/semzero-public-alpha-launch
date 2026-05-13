from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FAMILY_LABELS = {
    "temporal_bucket": "Temporal bucket / timezone boundary",
    "incremental_filter": "Incremental filter / cost pruning",
    "join_cardinality": "Join fanout / grain assumption",
    "enum_domain_closure": "Closed enum/domain mapping",
    "null_default_fallback": "Null/default fallback semantics",
}


@dataclass(slots=True)
class DogfoodReportBuilder:
    dogfood_dir: str | Path

    def build(self) -> dict[str, Any]:
        root = Path(self.dogfood_dir)
        summary = _load_json(root / "dogfood_run_summary.json", default={})
        dashboard = _load_json(root / "assumption_dashboard.json", default={})
        spec = _load_json(root / "scenarios" / "scenarios.json", default={"scenarios": []})
        rows = []
        passed = failed = 0
        for scenario in spec.get("scenarios", []):
            sid = scenario.get("id")
            receipt_path = root / "receipts" / f"{sid}.receipt.json"
            comment_path = root / "comments" / f"{sid}.comment.md"
            receipt = _load_json(receipt_path, default={})
            findings = [f for f in receipt.get("findings") or [] if isinstance(f, dict)]
            expected = scenario.get("expected_family")
            found = sorted({str(f.get("family")) for f in findings})
            ok = expected in found
            passed += int(ok)
            failed += int(not ok)
            top = _top_finding(findings)
            rows.append(
                {
                    "id": sid,
                    "title": scenario.get("title") or sid,
                    "expected_family": expected,
                    "expected_label": FAMILY_LABELS.get(str(expected), str(expected)),
                    "found_families": found,
                    "status": "pass" if ok else "fail",
                    "verdict": receipt.get("verdict"),
                    "finding_count": len(findings),
                    "top_finding": top,
                    "receipt": str(receipt_path.relative_to(root)) if receipt_path.exists() else "",
                    "comment": str(comment_path.relative_to(root)) if comment_path.exists() else "",
                }
            )
        policy = dashboard.get("policy_recommendations") or {}
        return {
            "report_kind": "semzero_dogfood_demo_report_v1_13",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dogfood_dir": str(root),
            "scenario_count": len(rows),
            "scenario_pass_count": passed,
            "scenario_fail_count": failed,
            "families_covered": sorted(
                {row["expected_family"] for row in rows if row.get("expected_family")}
            ),
            "scenario_results": rows,
            "dashboard_summary": {
                "run_count": dashboard.get("run_count"),
                "assumption_finding_count": dashboard.get("assumption_finding_count"),
                "would_require_review_count": dashboard.get("would_require_review_count"),
                "family_counts": dashboard.get("family_counts") or {},
                "top_blast_radius_nodes": (dashboard.get("top_blast_radius_nodes") or [])[:5],
                "recurring_stable_findings": (dashboard.get("recurring_stable_findings") or [])[:5],
                "roi": dashboard.get("roi") or {},
                "calibration_readiness": dashboard.get("calibration_readiness") or {},
                "policy_recommendations": {
                    "summary": policy.get("summary"),
                    "family_recommendations": (policy.get("family_recommendations") or [])[:8],
                },
            },
            "demo_script": "python scripts/run_dogfood_assumption_gate.py",
            "positioning": "Assumption-aware dbt PR gate: hidden SQL assumptions + blast radius + receipt + PR comment + calibration dashboard.",
            "scope_guardrail": "Core-only dogfood pack: no Terraform/Kubernetes adapters, no full Wind Tunnel, no RGCN, no Chaos Mode.",
        }

    def save_json(self, output: str | Path) -> dict[str, Any]:
        payload = self.build()
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return payload

    def save_markdown(self, output: str | Path) -> str:
        text = render_dogfood_markdown(self.build())
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return text


def render_dogfood_markdown(payload: dict[str, Any]) -> str:
    dash = payload.get("dashboard_summary") or {}
    roi = dash.get("roi") or {}
    lines = [
        "# SemZero Dogfood Demo Report",
        "",
        f"Generated: `{payload.get('generated_at')}`",
        "",
        "## Product loop demonstrated",
        "",
        "`dbt PR diff → hidden assumption finding → blast radius → typed receipt → PR-ready comment → dashboard calibration`",
        "",
        "## Scope guardrail",
        "",
        payload.get("scope_guardrail", "Core-only."),
        "",
        "## Scenario coverage",
        "",
        f"- Scenarios: **{payload.get('scenario_count', 0)}**",
        f"- Passed: **{payload.get('scenario_pass_count', 0)}**",
        f"- Failed: **{payload.get('scenario_fail_count', 0)}**",
        f"- Families covered: `{', '.join(payload.get('families_covered') or [])}`",
        "",
        "| Scenario | Expected assumption | Status | Verdict | Findings | Top evidence |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("scenario_results") or []:
        top = row.get("top_finding") or {}
        evidence = str(top.get("trigger_evidence") or top.get("evidence_excerpt") or "")[
            :120
        ].replace("\n", " ")
        lines.append(
            f"| `{row.get('id')}` | `{row.get('expected_family')}` | **{row.get('status')}** | `{row.get('verdict')}` | {row.get('finding_count')} | {evidence} |"
        )
    lines += [
        "",
        "## Dashboard summary",
        "",
        f"- Runs: **{dash.get('run_count', 0)}**",
        f"- Assumption findings: **{dash.get('assumption_finding_count', 0)}**",
        f"- Would require review: **{dash.get('would_require_review_count', 0)}**",
    ]
    if roi.get("estimated_cost_exposure_usd_per_run") is not None:
        lines.append(
            f"- Directional cost exposure surfaced: **${roi.get('estimated_cost_exposure_usd_per_run')}/run**"
        )
    lines += ["", "## Family counts", ""]
    fam = dash.get("family_counts") or {}
    if fam:
        lines.extend(
            f"- `{family}`: {count}"
            for family, count in sorted(fam.items(), key=lambda kv: (-kv[1], kv[0]))
        )
    else:
        lines.append("No family counts available.")
    lines += ["", "## Calibration posture", ""]
    readiness = dash.get("calibration_readiness") or {}
    policy = dash.get("policy_recommendations") or {}
    lines += [
        f"- Readiness state: **{readiness.get('state', 'unknown')}**",
        f"- Reason: {readiness.get('reason', '')}",
        f"- Policy recommendation: {policy.get('summary', 'No recommendation yet.')}",
        "",
        "## How to rerun",
        "",
        "```bash",
        payload.get("demo_script", "python scripts/run_dogfood_assumption_gate.py"),
        "```",
        "",
    ]
    return "\n".join(lines)


def _load_json(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _top_finding(findings: list[dict[str, Any]]) -> dict[str, Any]:
    if not findings:
        return {}
    return sorted(
        findings, key=lambda f: (-(int(f.get("risk_score") or 0)), str(f.get("family") or ""))
    )[0]
