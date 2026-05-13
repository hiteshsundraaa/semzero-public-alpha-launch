from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def _pick_failure(assessment: Any) -> str:
    failures = getattr(assessment, "predicted_failure_modes", None) or []
    if failures:
        return str(failures[0])
    return "Potential downstream breakage requires review."


def _pick_fix(assessment: Any) -> str:
    proof = getattr(assessment, "proof_evidence", None) or []
    if proof:
        fix = proof[0].get("suggested_fix") or ""
        if fix:
            return str(fix)
    recommendation = getattr(assessment, "recommendation", "") or ""
    return str(recommendation or "Coordinate a staged rollout before merge.")


class MergeCommentRenderer:
    """Human-readable merge comment that combines Gate, Wind Tunnel, and Chaos."""

    def render(
        self,
        gate_result: Any,
        wind_tunnel_receipt: Optional[dict[str, Any]] = None,
        chaos_report: Optional[dict[str, Any]] = None,
    ) -> str:
        verdict = getattr(gate_result, "verdict", None)
        verdict_value = getattr(verdict, "value", str(verdict))
        emoji = {"SAFE": "✅", "NEEDS_REVIEW": "⚠️", "BLOCK": "🚫"}.get(verdict_value, "❓")
        finops = getattr(gate_result, "finops_summary", None) or {}
        lines = [
            f"## {emoji} SemZero Change Gate — SemZero Merge Comment — {verdict_value}",
            "",
            f"**Reliability Score:** {getattr(gate_result, 'reliability_score', 0):.1f}/100  ",
            f"**On-call Risk:** {getattr(gate_result, 'oncall_risk', 'UNKNOWN')}  ",
            f"**Blast Radius:** {getattr(gate_result, 'total_blast_radius', 0)} downstream nodes  ",
            f"**Estimated Backfill / Rollback Risk:** ${getattr(gate_result, 'total_estimated_backfill_cost_usd', 0.0):,.0f}",
        ]
        if finops.get("projected_weekly_cost_usd"):
            lines.append(
                f"**Projected compute waste avoided:** ${float(finops.get('projected_weekly_cost_usd', 0.0)):,.0f}/week · ${float(finops.get('blocked_weekend_waste_usd', 0.0)):,.0f} this weekend  "
            )
            lines.append(f"**FinOps confidence:** {finops.get('confidence', 'medium')}")
        lines += [
            "",
        ]

        important = (
            getattr(gate_result, "blocking_assessments", None)
            or getattr(gate_result, "review_assessments", None)
            or getattr(gate_result, "safe_assessments", None)
            or []
        )
        if important:
            lines += [
                "### What will break first",
                "",
                "| Impacted nodes | Expected failure mode | Estimated cost | Suggested fix |",
                "|---|---|---:|---|",
            ]
            for assessment in important[:5]:
                impacted = ", ".join(
                    f"`{a}`"
                    for a in (
                        getattr(assessment, "affected_assets", [])
                        or [getattr(assessment, "node_id", "unknown")]
                    )[:4]
                )
                failure = _pick_failure(assessment)
                fix = _pick_fix(assessment)
                cost = getattr(assessment, "estimated_backfill_cost_usd", 0.0)
                lines.append(f"| {impacted} | {failure[:120]} | ${cost:,.0f} | {fix[:140]} |")
            lines.append("")

        if finops:
            lines += [
                "### Pre-merge FinOps Gate",
                "",
                f"SemZero projects **${float(finops.get('projected_weekly_cost_usd', 0.0)):,.0f}/week** of avoidable warehouse spend if this change merges unchanged.",
                "",
            ]
            for item in (finops.get("drivers") or [])[:4]:
                detail = item.get("detail") or item.get("kind") or "compute driver"
                lines.append(
                    f"- {detail} → ≈ ${float(item.get('estimated_weekly_cost_usd', 0.0) or 0.0):,.0f}/week"
                )
            for note in (finops.get("notes") or [])[:2]:
                lines.append(f"- {note}")
            lines.append("")

        proof_bundle = getattr(gate_result, "proof_bundle", None) or {}
        summary = proof_bundle.get("summary", {}) if isinstance(proof_bundle, dict) else {}
        findings = proof_bundle.get("findings", []) if isinstance(proof_bundle, dict) else []
        if summary or findings:
            lines += [
                "### AST-first proofing",
                "",
                f"Scanned **{summary.get('scanned_files', 0)}** source assets and found **{summary.get('finding_count', 0)}** direct/downstream references before running warehouse compute.",
                "",
            ]
            for finding in findings[:5]:
                name = Path(str(finding.get("asset_path", "unknown"))).name
                lang = finding.get("language", "code")
                lines.append(
                    f"- `{name}` ({lang}) references `{finding.get('node_id', 'unknown')}` → {finding.get('expected_failure_mode', '')}"
                )
            lines.append("")

        receipt = wind_tunnel_receipt or getattr(gate_result, "wind_tunnel_receipt", None)
        if receipt:
            broken = int(receipt.get("queries_broken", 0) or 0)
            mismatch = int(receipt.get("queries_mismatch", 0) or 0)
            replayed = int(receipt.get("queries_replayed", 0) or 0)
            verdict = receipt.get("verdict", "UNKNOWN")
            lines += [
                "### Wind Tunnel replay",
                "",
                f"Replay verdict: **{verdict}** · {broken} broken · {mismatch} mismatched · {replayed} replayed",
                "",
            ]
            mix = receipt.get("query_mix_summary") or {}
            if mix:
                lines.append(
                    f"- Workload mix: historical={mix.get('historical_queries', 0)}, synthetic={mix.get('synthetic_queries', 0)}, future={mix.get('future_queries', 0)}"
                )
            for query in (receipt.get("broken_queries") or [])[:3]:
                preview = str(query.get("query_preview") or query.get("query_text") or "")
                lines.append(f"- Broken query `{query.get('query_id', 'query')}` → {preview[:110]}")
            for item in (receipt.get("prevention_summary") or [])[:3]:
                lines.append(f"- Prevented / surfaced: {item}")
            if receipt.get("semantic_risks"):
                for risk in receipt["semantic_risks"][:2]:
                    lines.append(
                        f"- Semantic risk `{risk.get('risk_type', 'risk')}` on `{risk.get('column', 'unknown')}` → {risk.get('suggestion', '')}"
                    )
            lines.append("")

        chaos = chaos_report or getattr(gate_result, "chaos_report", None)
        if chaos:
            summary = chaos.get("summary", {})
            lines += [
                "### Chaos resilience",
                "",
                f"Fragility score: **{summary.get('fragility_score', 0)} / 100** ({summary.get('fragility_grade', '?')}) · {summary.get('mutations_that_broke', 0)} breaking mutations · {summary.get('critical_pipelines', 0)} critical pipelines",
                "",
            ]
            for mutation in (chaos.get("mutation_results") or [])[:3]:
                if not mutation.get("tests_failed"):
                    continue
                lines.append(
                    f"- `{mutation.get('node_id', 'unknown')}` under `{mutation.get('mutation_type', 'UNKNOWN')}` failed {mutation.get('tests_failed', 0)}/{mutation.get('tests_run', 0)} workload checks"
                )
            for item in (chaos.get("top_oncall_triggers") or [])[:3]:
                lines.append(f"- On-call trigger: {item}")
            lines.append("")

        exec_plan = getattr(gate_result, "recommended_execution", None) or {}
        if exec_plan:
            lines += [
                "### Reliability execution plan",
                "",
                f"- Run Wind Tunnel: **{'yes' if exec_plan.get('run_wind_tunnel') else 'no'}**",
                f"- Run Chaos: **{'yes' if exec_plan.get('run_chaos') else 'no'}**",
                f"- Synthetic future workload: **{'yes' if exec_plan.get('future_workload_required') else 'no'}**",
            ]
            scoped = exec_plan.get("scope_assets") or []
            if scoped:
                lines.append(f"- Scoped assets: {', '.join(f'`{item}`' for item in scoped[:6])}")
            for reason in (exec_plan.get("reasons") or [])[:4]:
                lines.append(f"- Why: {reason}")
            lines.append("")

        next_actions = getattr(gate_result, "next_actions", None) or []
        lines += [
            "### Suggested next step",
            "",
        ]
        if next_actions:
            lines.extend(f"- {action}" for action in next_actions[:6])
        else:
            lines.append(
                "Patch the directly referenced consumers first, then rerun Gate + Wind Tunnel on the narrowed blast radius before merge."
            )
        return "\n".join(lines).strip() + "\n"
