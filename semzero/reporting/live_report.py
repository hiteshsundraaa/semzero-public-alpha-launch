from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Optional


@dataclass
class UnifiedOpsReport:
    gate_result: Optional[dict[str, Any]] = None
    wind_tunnel_receipt: Optional[dict[str, Any]] = None
    chaos_report: Optional[dict[str, Any]] = None

    def render_markdown(self) -> str:
        lines = ["# SemZero Unified Operations Report", ""]
        lines += self._executive_summary()
        lines += self._problem_solution_section()
        lines += self._tested_and_prevented_section()
        lines += self._execution_proof_section()
        if self.gate_result:
            lines += self._gate_section(self.gate_result)
        finops_lines = self._finops_lines()
        if finops_lines:
            lines += (
                ["## Pre-merge FinOps Gate", ""] + [f"- {line}" for line in finops_lines] + [""]
            )
        ast_lines = self._ast_mapping_lines()
        if ast_lines:
            lines += (
                ["## Cross-modal AST mapping proof", ""]
                + [f"- {line}" for line in ast_lines]
                + [""]
            )
        assumption_lines = self._assumption_lines()
        if assumption_lines:
            lines += (
                ["## Undocumented downstream assumptions", ""]
                + [f"- {line}" for line in assumption_lines]
                + [""]
            )
        decision_lines = self._decision_summary_lines()
        if decision_lines:
            lines += (
                ["## Why SemZero is blocking or reviewing this merge", ""]
                + [f"- {line}" for line in decision_lines]
                + [""]
            )
        risk_lines = self._risk_register_lines()
        if risk_lines:
            lines += ["## Risk register", ""] + [f"- {line}" for line in risk_lines] + [""]
        remediation_lines = self._remediation_lines()
        if remediation_lines:
            lines += (
                ["## What the engineer should do next", ""]
                + [f"- {line}" for line in remediation_lines]
                + [""]
            )
        savings_lines = self._savings_ledger_lines()
        if savings_lines:
            lines += ["## Savings ledger", ""] + [f"- {line}" for line in savings_lines] + [""]
        shadow_lines = self._shadow_dashboard_lines()
        if shadow_lines:
            lines += (
                ["## Shadow mode proof dashboard", ""]
                + [f"- {line}" for line in shadow_lines]
                + [""]
            )
        evidence_lines = self._evidence_lines()
        if evidence_lines:
            lines += ["## Evidence ledger", ""] + [f"- {line}" for line in evidence_lines] + [""]
        sticky = self._sticky_ops_lines()
        if sticky:
            lines += (
                ["## Operator memory and feedback", ""] + [f"- {line}" for line in sticky] + [""]
            )
        if self.wind_tunnel_receipt:
            lines += self._wind_tunnel_section(self.wind_tunnel_receipt)
        if self.chaos_report:
            lines += self._chaos_section(self.chaos_report)
        visual = self._visual_map_markdown()
        if visual:
            lines += ["## Query and error map", "", *visual, ""]
        lines += self._ecosystem_section()
        lines += self._debug_checklist()
        if not any([self.gate_result, self.wind_tunnel_receipt, self.chaos_report]):
            lines += ["No inputs were provided.", ""]
        return "\n".join(lines).strip() + "\n"

    def render_html(self) -> str:
        gate = self.gate_result or {}
        receipt = self.wind_tunnel_receipt or {}
        chaos = self.chaos_report or {}
        cards = [
            self._card_html("Merge recommendation", self._summary_card_lines()),
            self._card_html(
                "Problem and SemZero solution", self._problem_solution_lines(), wide=True
            ),
            self._card_html(
                "What SemZero tested and prevented",
                self._tested_and_prevented_lines()
                or ["No simulation or prevention details available."],
            ),
            self._card_html(
                "Execution / isolation proof", self._execution_proof_lines(), wide=True
            ),
        ]
        if gate:
            cards.append(self._card_html("Change Gate", self._gate_card_lines(gate), wide=True))
        finops_lines = self._finops_lines()
        if finops_lines:
            cards.append(self._card_html("Pre-merge FinOps Gate", finops_lines, wide=True))
        ast_lines = self._ast_mapping_lines()
        if ast_lines:
            cards.append(self._card_html("Cross-modal AST mapping proof", ast_lines, wide=True))
        assumption_lines = self._assumption_lines()
        if assumption_lines:
            cards.append(
                self._card_html("Undocumented downstream assumptions", assumption_lines, wide=True)
            )
        decision_lines = self._decision_summary_lines()
        if decision_lines:
            cards.append(
                self._card_html(
                    "Why SemZero is blocking or reviewing this merge", decision_lines, wide=True
                )
            )
        risk_lines = self._risk_register_lines()
        if risk_lines:
            cards.append(self._card_html("Risk register", risk_lines, wide=True))
        remediation_lines = self._remediation_lines()
        if remediation_lines:
            cards.append(
                self._card_html("What the engineer should do next", remediation_lines, wide=True)
            )
        savings_lines = self._savings_ledger_lines()
        if savings_lines:
            cards.append(self._card_html("Savings ledger", savings_lines, wide=True))
        shadow_lines = self._shadow_dashboard_lines()
        if shadow_lines:
            cards.append(self._card_html("Shadow mode proof dashboard", shadow_lines, wide=True))
        evidence_lines = self._evidence_lines()
        if evidence_lines:
            cards.append(self._card_html("Evidence ledger", evidence_lines, wide=True))
        sticky = self._sticky_ops_lines()
        if sticky:
            cards.append(self._card_html("Operator memory and feedback", sticky, wide=True))
        if receipt:
            cards.append(self._card_html("Wind Tunnel", self._wind_card_lines(receipt), wide=True))
        if chaos:
            cards.append(self._card_html("Chaos", self._chaos_card_lines(chaos), wide=True))
        visual_svg = self._visual_map_svg()
        if visual_svg:
            cards.append(
                f"<section class='card wide'><h2>Visual query/error map</h2>{visual_svg}</section>"
            )
        ecosystem_lines = self._ecosystem_lines()
        if ecosystem_lines:
            cards.append(self._card_html("Ecosystem context", ecosystem_lines))
        debug_lines = self._debug_lines()
        if debug_lines:
            cards.append(self._card_html("Debug checklist", debug_lines, wide=True))
        body = "\n".join(cards)
        return f"""<html><head><meta charset='utf-8'><title>SemZero Ops Report</title>
<style>
body{{font-family:Inter,Arial,sans-serif;max-width:1280px;margin:32px auto;padding:0 16px;color:#111827;background:#f8fafc}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}
.card{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:20px;box-shadow:0 10px 28px rgba(15,23,42,.06);grid-column:span 4}}
.card.wide{{grid-column:span 12}}
.card h2{{margin:0 0 12px 0;font-size:20px}}
.kpi{{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 12px}}
.badge{{display:inline-block;padding:6px 10px;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:12px;font-weight:600}}
.badge.block{{background:#fef2f2;color:#991b1b}}
.badge.review{{background:#fff7ed;color:#9a3412}}
.badge.safe{{background:#ecfdf5;color:#166534}}
ul{{margin:0;padding-left:18px;line-height:1.5}}
li{{margin:4px 0}}
code{{background:#f3f4f6;padding:1px 6px;border-radius:6px}}
.header{{margin-bottom:20px}}
.header h1{{margin:0;font-size:30px}}
.header p{{margin:8px 0 0;color:#4b5563}}
svg{{width:100%;height:auto;border:1px solid #e5e7eb;border-radius:12px;background:linear-gradient(180deg,#fff,#f8fafc)}}
.label{{font-size:12px;fill:#334155;font-family:Inter,Arial,sans-serif}}
.node{{fill:#eef2ff;stroke:#6366f1;stroke-width:1.2}}
.node.error{{fill:#fef2f2;stroke:#dc2626}}
.node.warn{{fill:#fff7ed;stroke:#ea580c}}
.edge{{stroke:#94a3b8;stroke-width:1.2;marker-end:url(#arrow)}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 0}}
.legend span{{font-size:12px;color:#475569}}
.swatch{{display:inline-block;width:12px;height:12px;border-radius:999px;margin-right:6px;vertical-align:middle}}
.swatch.ast{{background:#6366f1}} .swatch.query{{background:#dc2626}} .swatch.asset{{background:#0f766e}}
</style></head><body>
<div class='header'><h1>SemZero Ops Report</h1><p>Pre-merge reliability summary, scoped replay evidence, chaos recovery findings, cross-modal AST proof, and operator runbook guidance.</p></div>
<div class='grid'>{body}</div></body></html>"""

    def save_markdown(self, path: str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.render_markdown(), encoding="utf-8")
        return out

    def save_html(self, path: str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.render_html(), encoding="utf-8")
        return out

    def _executive_summary(self) -> list[str]:
        if not any([self.gate_result, self.wind_tunnel_receipt, self.chaos_report]):
            return []
        lines = ["## Merge recommendation", ""]
        lines.extend(f"- {line}" for line in self._summary_card_lines())
        lines.append("")
        return lines

    def _summary_card_lines(self) -> list[str]:
        gate = self.gate_result or {}
        receipt = self.wind_tunnel_receipt or {}
        chaos = self.chaos_report or {}
        summary = chaos.get("summary", {}) if isinstance(chaos, dict) else {}
        lines: list[str] = []
        if gate:
            lines.append(
                f"Gate verdict: {gate.get('verdict', 'UNKNOWN')} · reliability score {gate.get('reliability_score', 'n/a')} · on-call risk {gate.get('oncall_risk', 'UNKNOWN')}"
            )
            iron = gate.get("iron_gate") or {}
            if iron:
                lines.append(
                    f"Iron Gate state: {iron.get('state', 'unknown')} · merge block {'enabled' if iron.get('should_block_merge') else 'not required'}"
                )
        if receipt:
            lines.append(
                f"Wind Tunnel: {receipt.get('verdict', 'UNKNOWN')} · {receipt.get('queries_broken', 0)} broken · {receipt.get('queries_mismatch', 0)} mismatched queries"
            )
        finops = gate.get("finops_summary", {}) if isinstance(gate, dict) else {}
        if finops.get("projected_weekly_cost_usd"):
            lines.append(
                f"FinOps Gate: ≈ ${float(finops.get('projected_weekly_cost_usd', 0.0)):,.0f}/week of avoidable compute waste surfaced before merge"
            )
        assumptions = gate.get("assumption_summary", {}) if isinstance(gate, dict) else {}
        if assumptions.get("finding_count"):
            lines.append(
                f"Assumption Gate: {assumptions.get('finding_count', 0)} downstream tribal-knowledge assumptions surfaced before merge"
            )
        if chaos:
            lines.append(
                f"Chaos: fragility {summary.get('fragility_score', chaos.get('fragility_score', 'n/a'))}/100 ({summary.get('fragility_grade', chaos.get('fragility_grade', '?'))})"
            )
        return lines

    def _problem_solution_section(self) -> list[str]:
        lines = self._problem_solution_lines()
        if not lines:
            return []
        return ["## Problem and SemZero solution", ""] + [f"- {line}" for line in lines] + [""]

    def _problem_solution_lines(self) -> list[str]:
        return [
            "Problem: risky schema, app-model, and workflow changes often pass review but fail later as downstream breakage, compute spikes, or silent data corruption.",
            "PreGate proves structural, contractual, and cross-modal AST risk before expensive execution begins.",
            "Wind Tunnel validates the proposed change on an isolated clone / shadow environment using historical and future-risk workloads.",
            "Chaos injects realistic bad-data conditions and verifies both breakage and recovery burden before production sees them.",
            "Unified reports connect source-code evidence, replay failures, chaos findings, and suggested fixes into one operator runbook.",
        ]

    def _execution_proof_section(self) -> list[str]:
        lines = self._execution_proof_lines()
        if not lines:
            return []
        return ["## Execution / isolation proof", ""] + [f"- {line}" for line in lines] + [""]

    def _execution_proof_lines(self) -> list[str]:
        gate = self.gate_result or {}
        receipt = self.wind_tunnel_receipt or {}
        chaos = self.chaos_report or {}
        lines: list[str] = [
            "Order of operations: PreGate → Wind Tunnel → Chaos → unified report.",
        ]
        proof = gate.get("proof_bundle") or {}
        summary = proof.get("summary", {}) if isinstance(proof, dict) else {}
        if summary.get("finding_count"):
            lines.append(
                f"AST mapping proof: {summary.get('finding_count', 0)} direct/downstream references were found across source code before warehouse compute began."
            )
        if receipt:
            clone_name = (
                receipt.get("clone_name")
                or receipt.get("clone")
                or "isolated validation environment"
            )
            lines.append(
                f"Isolation proof: Wind Tunnel used `{clone_name}` and replayed {receipt.get('queries_replayed', 0)} queries before any live apply."
            )
            if receipt.get("query_mix_summary"):
                mix = receipt.get("query_mix_summary") or {}
                lines.append(
                    f"Replay proof: historical={mix.get('historical_queries', 0)}, synthetic={mix.get('synthetic_queries', 0)}, future={mix.get('future_queries', 0)}."
                )
        if chaos:
            summary = chaos.get("summary", {}) if isinstance(chaos, dict) else {}
            lines.append(
                f"Chaos proof: {summary.get('mutations_applied', chaos.get('mutations_applied', 0))} mutations executed with {summary.get('mutations_that_broke', chaos.get('mutations_that_broke', 0))} workload-breaking cases surfaced."
            )
            recovery = chaos.get("recovery_summary") or summary.get("recovery_summary") or {}
            if recovery:
                lines.append(
                    f"Recovery proof: verified recoveries={recovery.get('verified_recoveries', 0)}, manual backfills/restores={recovery.get('manual_backfill_required', 0)}, recoverability score={recovery.get('recoverability_score', 0)}."
                )
        return lines

    def _tested_and_prevented_section(self) -> list[str]:
        bullets = self._tested_and_prevented_lines()
        if not bullets:
            return []
        lines = ["## What SemZero tested and prevented", ""]
        lines.extend(f"- {line}" for line in bullets)
        lines.append("")
        return lines

    def _tested_and_prevented_lines(self) -> list[str]:
        lines: list[str] = []
        receipt = self.wind_tunnel_receipt or {}
        if receipt:
            mix = receipt.get("query_mix_summary") or {}
            tested = []
            if mix.get("historical_queries"):
                tested.append(f"{mix['historical_queries']} historical queries")
            if mix.get("synthetic_queries"):
                tested.append(f"{mix['synthetic_queries']} synthetic queries")
            if mix.get("future_queries"):
                tested.append(f"{mix['future_queries']} synthetic future-workload queries")
            if tested:
                lines.append("Wind Tunnel tested: " + ", ".join(tested))
            budget = receipt.get("replay_budget_summary") or {}
            if budget:
                lines.append(
                    f"Wind Tunnel scoped replay: {budget.get('selected_queries', 0)}/{budget.get('candidate_queries', 0)} queries kept, {budget.get('compute_saved_pct', 0)}% compute avoided while keeping {budget.get('focus_hit_rate', 0)}% focus-hit coverage"
                )
            for item in (receipt.get("prevention_summary") or [])[:4]:
                lines.append(f"Prevented / surfaced: {item}")
        gate = self.gate_result or {}
        proof = gate.get("proof_bundle") or {}
        summary = proof.get("summary", {}) if isinstance(proof, dict) else {}
        if summary.get("finding_count"):
            lines.append(
                f"PreGate proved: {summary.get('finding_count', 0)} direct/downstream code references before warehouse compute started"
            )
        chaos = self.chaos_report or {}
        if chaos:
            for item in (chaos.get("top_oncall_triggers") or [])[:3]:
                lines.append(f"Chaos exposed: {item}")
        return lines

    def _finops_lines(self) -> list[str]:
        gate = self.gate_result or {}
        finops = gate.get("finops_summary", {}) if isinstance(gate, dict) else {}
        if not finops:
            return []
        lines = [
            f"Projected avoidable compute waste: ${float(finops.get('projected_weekly_cost_usd', 0.0) or 0.0):,.0f}/week · ${float(finops.get('blocked_weekend_waste_usd', 0.0) or 0.0):,.0f} this weekend.",
            f"Confidence: {finops.get('confidence', 'medium')} · source: {finops.get('source', 'heuristic')}.",
        ]
        runtime = finops.get("runtime_validation") or {}
        if runtime.get("projected_weekly_cost_usd"):
            lines.append(
                f"Wind Tunnel workload replay validated up to ${float(runtime.get('projected_weekly_cost_usd', 0.0) or 0.0):,.0f}/week of compute risk in the scoped query set."
            )
        for item in (finops.get("drivers") or [])[:4]:
            detail = item.get("detail") or item.get("kind") or "compute driver"
            lines.append(
                f"Driver: {detail} → ≈ ${float(item.get('estimated_weekly_cost_usd', 0.0) or 0.0):,.0f}/week"
            )
        for note in (finops.get("notes") or [])[:3]:
            lines.append(note)
        return lines

    def _gate_section(self, gate: dict[str, Any]) -> list[str]:
        assessments = gate.get("assessments", [])
        lines = [
            "## Change Gate",
            "",
            f"- **Verdict:** {gate.get('verdict', 'UNKNOWN')}",
            f"- **Reliability score:** {gate.get('reliability_score', 'n/a')}",
            f"- **On-call risk:** {gate.get('oncall_risk', 'UNKNOWN')}",
            f"- **Blast radius:** {gate.get('total_blast_radius', 0)}",
            f"- **Estimated rollback/backfill risk:** ${gate.get('total_estimated_backfill_cost_usd', 0):,.0f}",
            "",
        ]
        iron = gate.get("iron_gate") or {}
        if iron:
            lines += ["### Iron Gate", ""]
            lines.append(f"- State: **{iron.get('state', 'unknown')}**")
            lines.append(
                f"- Should block merge: **{'yes' if iron.get('should_block_merge') else 'no'}**"
            )
            for reason in iron.get("reasons", [])[:5]:
                lines.append(f"- Reason: {reason}")
            lines.append("")
        if assessments:
            lines += [
                "### Highest-risk nodes",
                "",
                "| Node | Compatibility | Failure mode | Suggested fix |",
                "|---|---|---|---|",
            ]
            for item in assessments[:8]:
                failure_modes = item.get("predicted_failure_modes") or []
                failure = (
                    failure_modes[0]
                    if failure_modes
                    else item.get("query_impact", "Potential downstream breakage")
                )
                lines.append(
                    f"| `{item.get('node_id', 'unknown')}` | {item.get('compatibility', 'UNKNOWN')} | {failure} | {item.get('recommendation', 'Review change before merge')} |"
                )
            lines.append("")
        graph = gate.get("graph_intelligence") or {}
        if graph.get("top_nodes"):
            lines += ["### Graph intelligence", ""]
            provider = graph.get("provider") or graph.get("status") or "graph"
            lines.append(f"- Provider: **{provider}**")
            for item in graph.get("top_nodes", [])[:5]:
                if isinstance(item, dict):
                    lines.append(
                        f"- Graph-ranked scope: `{item.get('node_id', 'unknown')}` ({item.get('score', 'n/a')})"
                    )
            lines.append("")
        exec_plan = gate.get("recommended_execution") or {}
        if exec_plan:
            lines += ["### Recommended execution", ""]
            lines.append(
                f"- Run Wind Tunnel: **{'yes' if exec_plan.get('run_wind_tunnel') else 'no'}**"
            )
            lines.append(f"- Run Chaos: **{'yes' if exec_plan.get('run_chaos') else 'no'}**")
            lines.append(
                f"- Synthetic future workload: **{'yes' if exec_plan.get('future_workload_required') else 'no'}**"
            )
            if exec_plan.get("scope_assets"):
                lines.append(
                    "- Scope assets: "
                    + ", ".join(f"`{item}`" for item in exec_plan.get("scope_assets", [])[:8])
                )
            if exec_plan.get("wind_tunnel_query_budget"):
                lines.append(
                    f"- Wind Tunnel query budget: **{exec_plan.get('wind_tunnel_query_budget')}** / {exec_plan.get('baseline_wind_tunnel_budget', exec_plan.get('wind_tunnel_query_budget'))}"
                )
            if exec_plan.get("chaos_mutation_budget"):
                lines.append(
                    f"- Chaos mutation budget: **{exec_plan.get('chaos_mutation_budget')}** / {exec_plan.get('baseline_chaos_budget', exec_plan.get('chaos_mutation_budget'))}"
                )
            if exec_plan.get("scope_reduction_pct"):
                lines.append(
                    f"- Expected validation scope reduction: **{exec_plan.get('scope_reduction_pct')}%**"
                )
            if exec_plan.get("estimated_compute_minutes_saved_per_run"):
                lines.append(
                    f"- Estimated compute saved per run: **{exec_plan.get('estimated_compute_minutes_saved_per_run')} min**"
                )
            if exec_plan.get("estimated_review_minutes_saved_per_run"):
                lines.append(
                    f"- Estimated review/triage saved per run: **{exec_plan.get('estimated_review_minutes_saved_per_run')} min**"
                )
            for reason in exec_plan.get("reasons", [])[:5]:
                lines.append(f"- Why: {reason}")
            lines.append("")
        return lines

    def _ast_mapping_lines(self) -> list[str]:
        gate = self.gate_result or {}
        proof = gate.get("proof_bundle") or {}
        findings = proof.get("findings") or []
        if not findings:
            return []
        lines = []
        summary = proof.get("summary") or {}
        if summary.get("finding_count"):
            lines.append(
                f"AST resonance findings: {summary.get('finding_count')} across application and analytics code paths"
            )
        by_lang: dict[str, int] = {}
        for item in findings:
            lang = str(item.get("language") or "code").lower()
            by_lang[lang] = by_lang.get(lang, 0) + 1
        if by_lang:
            lines.append(
                "Coverage by language: "
                + ", ".join(f"`{k}`={v}" for k, v in sorted(by_lang.items()))
            )
        for item in findings[:6]:
            asset_name = Path(str(item.get("asset_path", "unknown"))).name
            node_id = item.get("node_id", "unknown")
            reason = (
                item.get("expected_failure_mode")
                or item.get("reason")
                or "legacy field/path still referenced"
            )
            lines.append(
                f"`{asset_name}` ({item.get('language', 'code')}) still references `{node_id}` → {reason}"
            )
        return lines

    def _assumption_lines(self) -> list[str]:
        gate = self.gate_result or {}
        assumptions = gate.get("assumption_summary") or {}
        if not assumptions:
            return []
        lines: list[str] = []
        if assumptions.get("finding_count"):
            lines.append(
                f"Assumption Gate surfaced {assumptions.get('finding_count', 0)} undocumented downstream assumptions across proof sources."
            )
        types = assumptions.get("assumption_types") or {}
        if types:
            ordered = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))
            lines.append(
                "Most common assumption types: "
                + ", ".join(
                    f"{name.replace('_', ' ').title()} ({count})" for name, count in ordered[:4]
                )
            )
        for item in (assumptions.get("findings") or [])[:4]:
            lines.append(
                f"{item.get('node_id', 'unknown')}: {item.get('assumption_type', 'ASSUMPTION').replace('_', ' ').title()} in `{Path(item.get('source_path', 'unknown')).name}` → {item.get('reason', '')}"
            )
        return lines

    def _decision_summary_lines(self) -> list[str]:
        gate = self.gate_result or {}
        summary = gate.get("decision_summary") or {}
        if not summary:
            return []
        lines = []
        if summary.get("primary_reason"):
            lines.append(str(summary.get("primary_reason")))
        categories = summary.get("risk_categories") or []
        if categories:
            lines.append("Risk categories: " + ", ".join(str(item) for item in categories))
        counts = summary.get("evidence_counts") or {}
        if counts:
            lines.append(
                f"Evidence counts: blocking={counts.get('blocking_findings', 0)}, review={counts.get('review_findings', 0)}, assumptions={counts.get('assumption_findings', 0)}, proof refs={counts.get('proof_references', 0)}, FinOps drivers={counts.get('finops_drivers', 0)}, broken queries={counts.get('broken_queries', 0)}"
            )
        for item in summary.get("highlights", [])[:5]:
            lines.append(str(item))
        return lines

    def _risk_register_lines(self) -> list[str]:
        gate = self.gate_result or {}
        payload = gate.get("risk_register") or []
        lines = []
        for item in payload[:6]:
            lines.append(
                f"[{str(item.get('severity', 'medium')).upper()}] `{item.get('node_id', 'unknown')}` → {item.get('why_it_matters', 'risk detected')} (categories: {', '.join(item.get('categories', []) or [])}; confidence {item.get('confidence', 'n/a')})"
            )
        return lines

    def _remediation_lines(self) -> list[str]:
        gate = self.gate_result or {}
        payload = gate.get("remediation_blueprints") or []
        lines = []
        for item in payload[:6]:
            steps = item.get("validation_steps", []) or []
            validation = f" Validation: {steps[0]}" if steps else ""
            lines.append(
                f"`{item.get('node_id', 'unknown')}` → {item.get('smallest_safe_change', 'Apply the smallest safe change and re-run SemZero.')} (confidence: {item.get('confidence', 'medium')}).{validation}"
            )
        return lines

    def _savings_ledger_lines(self) -> list[str]:
        gate = self.gate_result or {}
        payload = gate.get("savings_ledger") or {}
        if not payload:
            return []
        lines = [str(payload.get("summary", "")).strip()] if payload.get("summary") else []
        lines.append(
            f"Projected weekly cost risk: ${float(payload.get('projected_weekly_cost_usd', 0.0) or 0.0):,.0f}; projected monthly cost risk: ${float(payload.get('projected_monthly_cost_usd', 0.0) or 0.0):,.0f}; estimated immediate savings: ${float(payload.get('estimated_savings_usd', 0.0) or 0.0):,.0f}."
        )
        patterns = payload.get("recurring_waste_patterns", []) or []
        if patterns:
            lines.append(
                "Recurring waste patterns: " + ", ".join(str(item) for item in patterns[:6])
            )
        return lines

    def _shadow_dashboard_lines(self) -> list[str]:
        gate = self.gate_result or {}
        payload = gate.get("shadow_summary") or {}
        if not payload:
            return []
        lines = [str(payload.get("summary", "")).strip()] if payload.get("summary") else []
        lines.append(
            f"Would-have-blocked: {payload.get('would_have_blocked', 0)} / {payload.get('run_count', 0)} run(s); would-have-required-review: {payload.get('would_have_required_review', 0)}."
        )
        lines.append(
            f"Feedback precision proxy: {float((payload.get('feedback_summary', {}) or {}).get('precision_proxy', 0.0) or 0.0):.0%}; overrides: {payload.get('override_count', 0)}; incidents: {payload.get('incident_count', 0)}."
        )
        recommendation = payload.get("enforcement_recommendation") or {}
        if recommendation:
            lines.append(
                f"Recommended rollout tier: {recommendation.get('tier', 'TIER_0_SHADOW_ONLY')} — {recommendation.get('description', '')}"
            )
        repo_trends = payload.get("repo_trends") or []
        if repo_trends:
            top = repo_trends[0]
            rec = top.get("enforcement_recommendation") or {}
            lines.append(
                f"Top repo trend: {top.get('repo', 'unknown_repo')} · runs={top.get('run_count', 0)} · savings=${float(top.get('estimated_savings_usd_total', 0.0) or 0.0):,.0f} · tier={rec.get('tier', 'TIER_0_SHADOW_ONLY')}"
            )
        team_trends = payload.get("team_trends") or []
        if team_trends:
            top = team_trends[0]
            rec = top.get("enforcement_recommendation") or {}
            lines.append(
                f"Top team trend: {top.get('team', 'unknown_team')} · runs={top.get('run_count', 0)} · savings=${float(top.get('estimated_savings_usd_total', 0.0) or 0.0):,.0f} · tier={rec.get('tier', 'TIER_0_SHADOW_ONLY')}"
            )
        patterns = payload.get("recurring_waste_patterns") or []
        if patterns:
            rendered = ", ".join(
                f"{item.get('pattern')} ({item.get('count')})"
                for item in patterns[:5]
                if isinstance(item, dict)
            )
            if rendered:
                lines.append("Top recurring waste patterns in shadow mode: " + rendered)
        return lines

    def _evidence_lines(self) -> list[str]:
        gate = self.gate_result or {}
        summary = gate.get("evidence_summary") or {}
        if not summary:
            return []
        lines = [
            f"Run id: {summary.get('run_id', 'n/a')}",
            f"Evidence captured: {summary.get('evidence_count', 0)} item(s) · observed={summary.get('observed_count', 0)} · inferred={summary.get('inferred_count', 0)}",
            f"Failed evidence items: {summary.get('failed_count', 0)}",
            f"Stages covered: {', '.join(summary.get('stages', []) or []) or 'n/a'}",
        ]
        graph = gate.get("graph_intelligence") or {}
        top_nodes = graph.get("top_nodes") or []
        if top_nodes:
            rendered = []
            for item in top_nodes[:4]:
                if isinstance(item, dict):
                    rendered.append(
                        f"{item.get('node_id', 'unknown')} ({item.get('score', 'n/a')})"
                    )
                else:
                    rendered.append(str(item))
            lines.append("Graph intelligence prioritised: " + ", ".join(rendered))
        if gate.get("shadow_mode"):
            lines.append(
                "Shadow mode: enabled — SemZero recorded evidence without blocking merge enforcement."
            )
        return lines

    def _wind_tunnel_section(self, receipt: dict[str, Any]) -> list[str]:
        lines = [
            "## Wind Tunnel",
            "",
            f"- **Verdict:** {receipt.get('verdict', 'UNKNOWN')}",
            f"- **Queries replayed:** {receipt.get('queries_replayed', 0)}",
            f"- **Broken:** {receipt.get('queries_broken', 0)}",
            f"- **Mismatches:** {receipt.get('queries_mismatch', 0)}",
            "",
        ]
        mix = receipt.get("query_mix_summary") or {}
        if mix:
            lines += ["### Replay mix", ""]
            if mix.get("historical_queries"):
                lines.append(f"- Historical workload: {mix['historical_queries']}")
            if mix.get("synthetic_queries"):
                lines.append(f"- Synthetic workload: {mix['synthetic_queries']}")
            if mix.get("future_queries"):
                lines.append(f"- Future workload: {mix['future_queries']}")
            if mix.get("regimes"):
                lines.append(
                    "- Regime scenarios: "
                    + ", ".join(str(item) for item in mix.get("regimes") or [])
                )
            lines.append("")
        risky = receipt.get("semantic_risks") or []
        if risky:
            lines += ["### Top semantic risks", ""]
            for item in risky[:5]:
                lines.append(f"- {item.get('risk_type', 'RISK')}: {item.get('description', '')}")
            lines.append("")
        budget = receipt.get("replay_budget_summary") or {}
        if budget:
            lines += [
                "### Replay budget efficiency",
                "",
                f"- Candidate queries: {budget.get('candidate_queries', 0)}",
                f"- Selected queries: {budget.get('selected_queries', 0)}",
                f"- Deferred queries: {budget.get('deferred_queries', 0)}",
                f"- Compute avoided by scoping: {budget.get('compute_saved_pct', 0)}%",
                f"- Focus-hit coverage: {budget.get('focus_hit_rate', 0)}%",
                f"- Replay fidelity score: {receipt.get('replay_fidelity_score', 0)}/100",
                "",
            ]
        if receipt.get("compute_cost_risk"):
            lines += [
                "### Compute-cost risk",
                "",
                f"- Estimated compute risk: {receipt.get('compute_cost_risk')}/100",
            ]
            for item in (receipt.get("compute_cost_notes") or [])[:4]:
                lines.append(f"- {item}")
            lines.append("")
        if receipt.get("top_failure_modes"):
            lines += (
                ["### Failure chain", ""]
                + [f"- {item}" for item in receipt.get("top_failure_modes", [])[:5]]
                + [""]
            )
        diffs = [
            item for item in (receipt.get("mismatch_queries") or []) if item.get("row_diff_summary")
        ]
        if diffs:
            lines += ["### Row-level mismatch preview", ""]
            for item in diffs[:3]:
                diff = item.get("row_diff_summary") or {}
                lines.append(
                    f"- `{item.get('query_id', 'query')}` changed columns: {', '.join(diff.get('changed_columns') or []) or 'preview hash drift'}"
                )
            lines.append("")
        debug_steps = receipt.get("suggested_debug_steps") or []
        if debug_steps:
            lines += ["### Debug next steps", ""] + [f"- {item}" for item in debug_steps[:6]] + [""]
        return lines

    def _chaos_section(self, report: dict[str, Any]) -> list[str]:
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        lines = [
            "## Chaos",
            "",
            f"- **Fragility score:** {summary.get('fragility_score', report.get('fragility_score', 'n/a'))}",
            f"- **Grade:** {report.get('fragility_grade', summary.get('fragility_grade', 'UNKNOWN'))}",
            f"- **Mutations applied:** {summary.get('mutations_applied', report.get('mutations_applied', 0))}",
            f"- **Mutations that broke workloads:** {summary.get('mutations_that_broke', report.get('mutations_that_broke', 0))}",
            "",
        ]
        anti = summary.get("top_anti_patterns") or report.get("top_anti_patterns") or []
        if anti:
            lines += ["### Fragility patterns", ""] + [f"- {item}" for item in anti[:5]] + [""]
        budget = report.get("budget_summary") or {}
        if budget:
            lines += [
                "### Mutation budget efficiency",
                "",
                f"- Candidate targets: {budget.get('candidate_targets', 0)}",
                f"- Selected mutations: {budget.get('selected_mutations', 0)}",
                f"- Compute avoided by scoping: {budget.get('compute_saved_pct', 0)}%",
                "",
            ]
        triggers = report.get("top_oncall_triggers") or []
        if triggers:
            lines += (
                ["### Top on-call triggers", ""] + [f"- {item}" for item in triggers[:5]] + [""]
            )
        hardening = report.get("recommended_hardening") or []
        recovery = report.get("recovery_summary") or summary.get("recovery_summary") or {}
        if recovery:
            lines += [
                "### Recovery verification",
                "",
                f"- Verified recoveries: {recovery.get('verified_recoveries', 0)}",
                f"- Manual backfills/restores required: {recovery.get('manual_backfill_required', 0)}",
                f"- Recoverability score: {recovery.get('recoverability_score', 0)}",
                "",
            ]
        if hardening:
            lines += ["### Hardening first", ""] + [f"- {item}" for item in hardening[:6]] + [""]
        return lines

    def _sticky_ops_lines(self) -> list[str]:
        gate = self.gate_result or {}
        artifact_paths = gate.get("artifact_paths") or {}
        lines: list[str] = []
        if artifact_paths.get("override_ledger"):
            lines.append(f"Override ledger attached: {artifact_paths.get('override_ledger')}")
        if artifact_paths.get("incident_ledger"):
            lines.append(f"Incident ledger attached: {artifact_paths.get('incident_ledger')}")
        recommendation = gate.get("recommended_execution") or {}
        if recommendation.get("estimated_review_minutes_saved_per_run") is not None:
            lines.append(
                f"Estimated review time saved per run: {recommendation.get('estimated_review_minutes_saved_per_run')} min"
            )
        return lines

    def _ecosystem_section(self) -> list[str]:
        bullets = self._ecosystem_lines()
        if not bullets:
            return []
        lines = ["## Ecosystem context", ""]
        lines.extend(f"- {line}" for line in bullets)
        lines.append("")
        return lines

    def _ecosystem_lines(self) -> list[str]:
        gate = self.gate_result or {}
        ecosystem = gate.get("ecosystem_context") or {}
        calibration = gate.get("calibration_summary") or {}
        lines: list[str] = []
        if ecosystem.get("focus_assets"):
            lines.append(
                "Focus assets from ecosystem hooks: "
                + ", ".join(f"`{item}`" for item in ecosystem.get("focus_assets", [])[:10])
            )
        if ecosystem.get("airflow", {}).get("temporal_paths"):
            lines.append(
                "Airflow temporal paths detected: "
                + "; ".join(ecosystem.get("airflow", {}).get("temporal_paths", [])[:3])
            )
        if ecosystem.get("dagster", {}).get("failing_assets"):
            lines.append(
                "Existing failing asset checks: "
                + ", ".join(
                    f"`{item}`"
                    for item in ecosystem.get("dagster", {}).get("failing_assets", [])[:6]
                )
            )
        if ecosystem.get("looker", {}).get("impacted_assets"):
            lines.append(
                "Looker/consumption assets in blast radius: "
                + ", ".join(
                    f"`{item}`"
                    for item in ecosystem.get("looker", {}).get("impacted_assets", [])[:6]
                )
            )
        if ecosystem.get("montecarlo", {}).get("focus_assets"):
            lines.append(
                "Monte Carlo observability context: "
                + ", ".join(
                    f"`{item}`"
                    for item in ecosystem.get("montecarlo", {}).get("focus_assets", [])[:6]
                )
            )
        if calibration.get("total_runs"):
            lines.append(
                f"Calibration memory: {calibration.get('total_runs')} past runs · block rate {calibration.get('block_rate')} · avg reliability {calibration.get('average_reliability_score')}"
            )
        return lines

    def _debug_checklist(self) -> list[str]:
        actions = self._debug_lines()
        if not actions:
            return []
        return ["## Debug checklist", ""] + [f"- {action}" for action in actions[:8]] + [""]

    def _debug_lines(self) -> list[str]:
        actions: list[str] = []
        seen = set()
        gate = self.gate_result or {}
        for action in gate.get("next_actions", [])[:6]:
            low = str(action).strip().lower()
            if low and low not in seen:
                actions.append(str(action).strip())
                seen.add(low)
        receipt = self.wind_tunnel_receipt or {}
        for action in receipt.get("suggested_debug_steps", [])[:4]:
            low = str(action).strip().lower()
            if low and low not in seen:
                actions.append(str(action).strip())
                seen.add(low)
        chaos = self.chaos_report or {}
        for action in chaos.get("recommended_hardening", [])[:4]:
            low = str(action).strip().lower()
            if low and low not in seen:
                actions.append(str(action).strip())
                seen.add(low)
        return actions

    def _gate_card_lines(self, gate: dict[str, Any]) -> list[str]:
        lines = [
            f"Verdict: {gate.get('verdict', 'UNKNOWN')}",
            f"Reliability score: {gate.get('reliability_score', 'n/a')}",
            f"On-call risk: {gate.get('oncall_risk', 'UNKNOWN')}",
            f"Blast radius: {gate.get('total_blast_radius', 0)}",
            f"Rollback/backfill risk: ${gate.get('total_estimated_backfill_cost_usd', 0):,.0f}",
        ]
        for item in (gate.get("next_actions") or [])[:5]:
            lines.append(item)
        return lines

    def _wind_card_lines(self, receipt: dict[str, Any]) -> list[str]:
        lines = [
            f"Verdict: {receipt.get('verdict', 'UNKNOWN')}",
            f"Queries replayed: {receipt.get('queries_replayed', 0)}",
            f"Broken queries: {receipt.get('queries_broken', 0)}",
            f"Mismatched queries: {receipt.get('queries_mismatch', 0)}",
        ]
        budget = receipt.get("replay_budget_summary") or {}
        if budget:
            lines.append(
                f"Scoped replay: {budget.get('selected_queries', 0)}/{budget.get('candidate_queries', 0)} queries, {budget.get('compute_saved_pct', 0)}% compute avoided"
            )
            lines.append(f"Replay fidelity score: {receipt.get('replay_fidelity_score', 0)}/100")
        for item in (receipt.get("top_failure_modes") or [])[:4]:
            lines.append(item)
        for item in (receipt.get("compute_cost_notes") or [])[:3]:
            lines.append(item)
        return lines

    def _chaos_card_lines(self, report: dict[str, Any]) -> list[str]:
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        lines = [
            f"Fragility score: {summary.get('fragility_score', report.get('fragility_score', 'n/a'))}",
            f"Grade: {report.get('fragility_grade', summary.get('fragility_grade', 'UNKNOWN'))}",
            f"Mutations applied: {summary.get('mutations_applied', report.get('mutations_applied', 0))}",
            f"Mutations that broke workloads: {summary.get('mutations_that_broke', report.get('mutations_that_broke', 0))}",
        ]
        budget = report.get("budget_summary") or {}
        if budget:
            lines.append(
                f"Mutation budget: {budget.get('selected_mutations', 0)}/{budget.get('candidate_targets', 0)} planned, {budget.get('compute_saved_pct', 0)}% scope avoided"
            )
        recovery = report.get("recovery_summary") or summary.get("recovery_summary") or {}
        if recovery:
            lines.append(f"Recoverability score: {recovery.get('recoverability_score', 0)}")
            lines.append(
                f"Manual backfills/restores: {recovery.get('manual_backfill_required', 0)}"
            )
        for item in (report.get("recommended_hardening") or [])[:4]:
            lines.append(item)
        return lines

    def _card_html(self, title: str, lines: list[str], wide: bool = False) -> str:
        klass = "card wide" if wide else "card"
        verdict = (self.gate_result or {}).get("verdict", "").upper()
        badge_class = (
            "safe" if verdict == "SAFE" else ("review" if verdict == "NEEDS_REVIEW" else "block")
        )
        items = "".join(f"<li>{escape(str(item))}</li>" for item in lines)
        badge = (
            f"<div class='kpi'><span class='badge {badge_class}'>{escape(verdict or 'INFO')}</span></div>"
            if title == "Merge recommendation" and verdict
            else ""
        )
        return f"<section class='{klass}'><h2>{escape(title)}</h2>{badge}<ul>{items}</ul></section>"

    def _visual_map_markdown(self) -> list[str]:
        proof = self.gate_result.get("proof_bundle", {}) if self.gate_result else {}
        findings = proof.get("findings", []) if isinstance(proof, dict) else []
        receipt = self.wind_tunnel_receipt or {}
        broken = receipt.get("broken_queries") or []
        lines: list[str] = []
        if findings:
            lines.append("**AST mapping**")
            for item in findings[:4]:
                lines.append(
                    f"- `{Path(str(item.get('asset_path', 'unknown'))).name}` → `{item.get('node_id', 'unknown')}`"
                )
        assessments = (self.gate_result or {}).get("assessments") or []
        if assessments:
            lines.append("**Impacted nodes**")
            for item in assessments[:4]:
                lines.append(
                    f"- `{item.get('node_id', 'unknown')}` → {item.get('compatibility', 'UNKNOWN')}"
                )
        if broken:
            lines.append("**Broken query map**")
            for q in broken[:5]:
                qid = q.get("query_id") or q.get("id") or "query"
                err = q.get("clone_error") or q.get("error") or "query failure"
                lines.append(f"- `{qid}` → {err[:120]}")
        return lines

    def _visual_map_svg(self) -> str:
        proof = self.gate_result.get("proof_bundle", {}) if self.gate_result else {}
        findings = proof.get("findings", []) if isinstance(proof, dict) else []
        broken = (self.wind_tunnel_receipt or {}).get("broken_queries") or []
        assets = []
        for item in findings[:4]:
            assets.append(
                (
                    Path(str(item.get("asset_path", "unknown"))).name,
                    item.get("node_id", "unknown"),
                    "ast",
                )
            )
        for item in ((self.gate_result or {}).get("assessments") or [])[:4]:
            assets.append(
                (item.get("node_id", "unknown"), item.get("compatibility", "UNKNOWN"), "asset")
            )
        for q in broken[:5]:
            qid = q.get("query_id") or q.get("id") or "query"
            label = (q.get("query_text") or q.get("sql") or "query")[:42]
            assets.append((qid, label, "query"))
        if not assets:
            return ""
        width = 1060
        height = 140 + 96 * len(assets)
        rows = []
        y = 64
        for idx, (left, right, kind) in enumerate(assets):
            lx, rx = 90, 710
            rows.append(
                f"<line class='edge' x1='{lx + 190}' y1='{y + 18}' x2='{rx - 20}' y2='{y + 18}' />"
            )
            rows.append(
                f"<rect class='node {'error' if kind == 'query' else ('warn' if kind == 'asset' else '')}' x='{lx}' y='{y}' rx='10' ry='10' width='190' height='38' />"
            )
            rows.append(f"<text class='label' x='{lx + 12}' y='{y + 23}'>{escape(left)}</text>")
            rows.append(
                f"<rect class='node {'warn' if kind in {'query', 'asset'} else ''}' x='{rx}' y='{y}' rx='10' ry='10' width='260' height='38' />"
            )
            rows.append(
                f"<text class='label' x='{rx + 12}' y='{y + 23}'>{escape(str(right)[:48])}</text>"
            )
            y += 72
        return f"""
<div class='legend'>
  <span><span class='swatch ast'></span>AST / source references</span>
  <span><span class='swatch query'></span>Broken replay queries</span>
  <span><span class='swatch asset'></span>Downstream impacted nodes</span>
</div>
<svg viewBox='0 0 {width} {height}' role='img' aria-label='SemZero visual query and error map'>
  <defs><marker id='arrow' markerWidth='10' markerHeight='10' refX='8' refY='3' orient='auto'><path d='M0,0 L0,6 L9,3 z' fill='#94a3b8'></path></marker></defs>
  <text class='label' x='90' y='28'>Source / AST or query id</text>
  <text class='label' x='710' y='28'>Impacted node / error evidence</text>
  {"".join(rows)}
</svg>
"""
