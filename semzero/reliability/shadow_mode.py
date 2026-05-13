from __future__ import annotations

import html as _html
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENFORCEMENT_TIERS = {
    "TIER_0_SHADOW_ONLY": "Keep SemZero non-blocking; insufficient volume or weak precision evidence.",
    "TIER_1_ADVISORY": "Show PR comments and reports, but do not require review or block.",
    "TIER_2_REQUIRE_REVIEW": "Require a human review for repeated/high-confidence risk classes.",
    "TIER_3_SELECTIVE_BLOCK": "Hard-block only high-confidence, high-severity, well-calibrated risks.",
}


def _append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    src = Path(path)
    if not src.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _month_key(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _week_key(value: Any) -> str:
    text = str(value or "")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _target_repo(row: dict[str, Any]) -> str:
    repo = str(row.get("repo") or row.get("repository") or "").strip()
    if repo:
        return repo
    target = str(row.get("target") or "").strip()
    if target and ":" in target:
        return target.split(":", 1)[0]
    return "unknown_repo"


def _target_team(row: dict[str, Any]) -> str:
    team = str(row.get("team") or "").strip()
    return team or "unknown_team"


def _precision_from_feedback(rows: list[dict[str, Any]]) -> tuple[float, Counter]:
    by_outcome = Counter(str(row.get("outcome") or "unknown") for row in rows)
    confirmed = (
        by_outcome.get("confirmed", 0) + by_outcome.get("useful", 0) + by_outcome.get("fixed", 0)
    )
    noisy = by_outcome.get("noisy", 0) + by_outcome.get("false_positive", 0)
    return round(confirmed / max(1, confirmed + noisy), 4), by_outcome


def _recommend_enforcement(metrics: dict[str, Any], precision_proxy: float) -> dict[str, Any]:
    run_count = _safe_int(metrics.get("run_count"))
    would_block = _safe_int(metrics.get("would_have_blocked"))
    would_review = _safe_int(metrics.get("would_have_required_review"))
    high_conf = _safe_int(metrics.get("high_confidence_runs"))
    critical_assumptions = _safe_int(metrics.get("critical_assumptions"))
    savings = _safe_float(metrics.get("estimated_savings_usd_total"))
    block_rate = would_block / max(1, run_count)
    review_rate = would_review / max(1, run_count)

    reasons: list[str] = []
    tier = "TIER_0_SHADOW_ONLY"
    if run_count < 3:
        reasons.append("Collect at least 3 shadow runs before recommending enforcement.")
    elif precision_proxy < 0.5:
        reasons.append(
            "Developer feedback precision proxy is below 50%; keep calibrating before enforcement."
        )
    elif (
        precision_proxy >= 0.8
        and high_conf >= 2
        and (savings >= 500 or critical_assumptions >= 2)
        and block_rate <= 0.8
    ):
        tier = "TIER_3_SELECTIVE_BLOCK"
        reasons.append(
            "High feedback precision, repeated high-confidence risks, and material savings/critical-assumption evidence support selective blocking."
        )
    elif precision_proxy >= 0.65 and (
        would_review >= 2 or critical_assumptions >= 1 or savings >= 250
    ):
        tier = "TIER_2_REQUIRE_REVIEW"
        reasons.append(
            "Shadow evidence supports review-required policy for recurring or costly risk classes."
        )
    else:
        tier = "TIER_1_ADVISORY"
        reasons.append(
            "Evidence is useful but should remain advisory until more precision/volume accumulates."
        )

    if block_rate > 0.8 and run_count >= 3:
        reasons.append(
            "Would-block rate is very high; start with review/advisory slices to avoid rollout friction."
        )
        if tier == "TIER_3_SELECTIVE_BLOCK":
            tier = "TIER_2_REQUIRE_REVIEW"
    if review_rate > 0.9 and run_count >= 3:
        reasons.append(
            "Review pressure is broad; enforce only the highest-confidence categories first."
        )

    suggested_policy = {
        "hard_block": [],
        "require_review": [],
        "advisory": ["low_confidence_semantic", "low_confidence_assumption", "optimization_only"],
    }
    risk_counts = metrics.get("risk_category_counts") or {}
    if tier == "TIER_3_SELECTIVE_BLOCK":
        if risk_counts.get("financial", 0):
            suggested_policy["hard_block"].append("high_confidence_financial_blowup")
        if risk_counts.get("assumptions", 0) or risk_counts.get("semantic", 0):
            suggested_policy["hard_block"].append("high_confidence_semantic_or_assumption_break")
        suggested_policy["require_review"].extend(
            ["medium_confidence_semantic", "critical_assumption_without_runtime_proof"]
        )
    elif tier == "TIER_2_REQUIRE_REVIEW":
        suggested_policy["require_review"].extend(
            ["financial_blowup", "semantic_break", "critical_assumption", "runtime_fragility"]
        )
    elif tier == "TIER_1_ADVISORY":
        suggested_policy["advisory"].extend(
            ["financial_blowup", "semantic_break", "critical_assumption", "runtime_fragility"]
        )

    return {
        "tier": tier,
        "description": ENFORCEMENT_TIERS[tier],
        "reasons": reasons,
        "suggested_policy": suggested_policy,
        "minimum_next_step": "Keep shadow mode enabled and collect feedback."
        if tier == "TIER_0_SHADOW_ONLY"
        else "Pilot this policy on one repo/team before global rollout.",
    }


@dataclass
class ShadowFeedbackLedger:
    path: str = "data/shadow_feedback.jsonl"

    def record(
        self,
        receipt_id: str,
        target: str,
        actor: str,
        outcome: str,
        note: str = "",
        metadata: dict[str, Any] | None = None,
        team: str = "",
        repo: str = "",
        risk_category: str = "",
    ) -> dict[str, Any]:
        payload = {
            "kind": "shadow_feedback",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "receipt_id": receipt_id,
            "target": target,
            "actor": actor,
            "outcome": outcome,
            "note": note,
            "team": team or "unknown_team",
            "repo": repo or _target_repo({"target": target}),
            "risk_category": risk_category or "unknown",
            "metadata": metadata or {},
        }
        _append_jsonl(self.path, payload)
        return payload

    def load(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.path)

    def summary(self) -> dict[str, Any]:
        rows = self.load()
        precision_proxy, by_outcome = _precision_from_feedback(rows)
        by_repo = Counter(_target_repo(row) for row in rows)
        by_team = Counter(_target_team(row) for row in rows)
        return {
            "entry_count": len(rows),
            "latest_target": rows[-1].get("target") if rows else "",
            "by_outcome": dict(by_outcome),
            "by_repo": dict(by_repo),
            "by_team": dict(by_team),
            "precision_proxy": precision_proxy,
        }


@dataclass
class ShadowRunLedger:
    path: str = "data/shadow_runs.jsonl"

    def record(
        self,
        gate_result: dict[str, Any],
        artifact_paths: dict[str, str] | None = None,
        pr_number: int | None = None,
        team: str = "",
        repo: str = "",
    ) -> dict[str, Any]:
        decision = gate_result.get("decision_summary") or {}
        savings = gate_result.get("savings_ledger") or {}
        assumptions = gate_result.get("assumption_summary") or {}
        run_id = str(
            (gate_result.get("evidence_summary") or {}).get("run_id")
            or gate_result.get("evaluated_at")
            or "shadow_run"
        )
        verdict = str(gate_result.get("verdict", "UNKNOWN")).upper()
        payload = {
            "kind": "shadow_run",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "receipt_id": run_id,
            "pr_number": pr_number,
            "team": team or "unknown_team",
            "repo": repo or "unknown_repo",
            "verdict": verdict,
            "would_block": bool(verdict == "BLOCK"),
            "would_require_review": bool(verdict in {"BLOCK", "REQUIRE_REVIEW", "WARN"}),
            "risk_categories": list(decision.get("risk_categories") or []),
            "primary_reason": decision.get("primary_reason", ""),
            "confidence": decision.get("confidence", "medium"),
            "assumption_count": _safe_int(assumptions.get("finding_count", 0)),
            "critical_assumption_count": len(assumptions.get("critical_findings") or [])
            if isinstance(assumptions.get("critical_findings"), list)
            else _safe_int(assumptions.get("critical_findings", 0)),
            "estimated_savings_usd": _safe_float(savings.get("estimated_savings_usd", 0.0)),
            "projected_weekly_cost_usd": _safe_float(savings.get("projected_weekly_cost_usd", 0.0)),
            "projected_monthly_cost_usd": _safe_float(
                savings.get("projected_monthly_cost_usd", 0.0)
            ),
            "recurring_waste_patterns": list(savings.get("recurring_waste_patterns") or []),
            "top_nodes": list((assumptions.get("top_nodes") or [])[:5]),
            "artifact_paths": artifact_paths or {},
        }
        _append_jsonl(self.path, payload)
        return payload

    def load(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.path)


class ShadowDashboard:
    def __init__(
        self,
        shadow_runs_path: str = "data/shadow_runs.jsonl",
        feedback_path: str = "data/shadow_feedback.jsonl",
        override_path: str = "data/override_ledger.jsonl",
        incident_path: str = "data/incident_ledger.jsonl",
    ) -> None:
        self.shadow_runs_path = shadow_runs_path
        self.feedback_path = feedback_path
        self.override_path = override_path
        self.incident_path = incident_path

    def build(self) -> dict[str, Any]:
        runs = _read_jsonl(self.shadow_runs_path)
        feedback = _read_jsonl(self.feedback_path)
        overrides = _read_jsonl(self.override_path)
        incidents = _read_jsonl(self.incident_path)
        global_metrics = self._aggregate_scope(
            runs, feedback, scope_name="global", scope_value="all"
        )
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **global_metrics,
            "repo_trends": self._scope_table(runs, feedback, key="repo"),
            "team_trends": self._scope_table(runs, feedback, key="team"),
            "weekly_trends": self._time_table(runs, key_fn=_week_key),
            "monthly_trends": self._time_table(runs, key_fn=_month_key),
            "enforcement_recommendations_by_repo": {},
            "enforcement_recommendations_by_team": {},
            "override_count": len(overrides),
            "incident_count": len(incidents),
            "linked_incident_rate": round(
                sum(1 for row in incidents if row.get("linked_receipt_id"))
                / max(1, len(incidents)),
                4,
            ),
        }
        payload["enforcement_recommendation"] = _recommend_enforcement(
            payload, payload.get("feedback_summary", {}).get("precision_proxy", 0.0)
        )
        payload["enforcement_recommendations_by_repo"] = {
            item["repo"]: item["enforcement_recommendation"] for item in payload["repo_trends"]
        }
        payload["enforcement_recommendations_by_team"] = {
            item["team"]: item["enforcement_recommendation"] for item in payload["team_trends"]
        }
        payload["summary"] = self._summary_line(payload)
        return payload

    def _aggregate_scope(
        self,
        runs: list[dict[str, Any]],
        feedback: list[dict[str, Any]],
        scope_name: str,
        scope_value: str,
    ) -> dict[str, Any]:
        verdicts = Counter(str(row.get("verdict") or "UNKNOWN") for row in runs)
        categories: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        patterns: Counter[str] = Counter()
        confidence: Counter[str] = Counter()
        assumption_total = 0
        critical_assumptions = 0
        estimated_savings = 0.0
        weekly_cost = 0.0
        monthly_cost = 0.0
        high_confidence_runs = 0
        for row in runs:
            conf = str(row.get("confidence") or "unknown")
            confidence[conf] += 1
            if conf.lower() == "high":
                high_confidence_runs += 1
            assumption_total += _safe_int(row.get("assumption_count", 0))
            critical_assumptions += _safe_int(row.get("critical_assumption_count", 0))
            estimated_savings += _safe_float(row.get("estimated_savings_usd", 0.0))
            weekly_cost += _safe_float(row.get("projected_weekly_cost_usd", 0.0))
            monthly_cost += _safe_float(row.get("projected_monthly_cost_usd", 0.0))
            reasons[str(row.get("primary_reason") or "unknown")] += 1
            for item in row.get("risk_categories") or []:
                categories[str(item)] += 1
            for item in row.get("recurring_waste_patterns") or []:
                patterns[str(item)] += 1
        precision_proxy, feedback_by_outcome = _precision_from_feedback(feedback)
        would_block = sum(1 for row in runs if row.get("would_block"))
        would_review = sum(1 for row in runs if row.get("would_require_review"))
        metrics = {
            scope_name: scope_value,
            "run_count": len(runs),
            "would_have_blocked": would_block,
            "would_have_blocked_rate": round(would_block / max(1, len(runs)), 4),
            "would_have_required_review": would_review,
            "would_have_required_review_rate": round(would_review / max(1, len(runs)), 4),
            "verdict_distribution": dict(verdicts),
            "risk_category_counts": dict(categories),
            "primary_reasons": dict(reasons),
            "confidence_distribution": dict(confidence),
            "high_confidence_runs": high_confidence_runs,
            "estimated_savings_usd_total": round(estimated_savings, 2),
            "projected_weekly_cost_usd_total": round(weekly_cost, 2),
            "projected_monthly_cost_usd_total": round(monthly_cost, 2),
            "average_assumptions_per_run": round(assumption_total / max(1, len(runs)), 2),
            "average_critical_assumptions_per_run": round(
                critical_assumptions / max(1, len(runs)), 2
            ),
            "critical_assumptions": critical_assumptions,
            "recurring_waste_patterns": [
                {"pattern": k, "count": v} for k, v in patterns.most_common(8)
            ],
            "feedback_summary": {
                "entry_count": len(feedback),
                "by_outcome": dict(feedback_by_outcome),
                "precision_proxy": precision_proxy,
            },
        }
        metrics["enforcement_recommendation"] = _recommend_enforcement(metrics, precision_proxy)
        return metrics

    def _scope_table(
        self, runs: list[dict[str, Any]], feedback: list[dict[str, Any]], key: str
    ) -> list[dict[str, Any]]:
        values = sorted(
            {str(row.get(key) or f"unknown_{key}") for row in runs}
            | {str(row.get(key) or f"unknown_{key}") for row in feedback}
        )
        table: list[dict[str, Any]] = []
        for value in values:
            scoped_runs = [row for row in runs if str(row.get(key) or f"unknown_{key}") == value]
            scoped_feedback = [
                row for row in feedback if str(row.get(key) or f"unknown_{key}") == value
            ]
            if not scoped_runs and not scoped_feedback:
                continue
            item = self._aggregate_scope(
                scoped_runs, scoped_feedback, scope_name=key, scope_value=value
            )
            table.append(item)
        table.sort(
            key=lambda row: (
                _safe_float(row.get("estimated_savings_usd_total")),
                _safe_int(row.get("would_have_blocked")),
            ),
            reverse=True,
        )
        return table

    def _time_table(self, runs: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in runs:
            grouped[key_fn(row.get("recorded_at"))].append(row)
        table = []
        for bucket, rows in sorted(grouped.items()):
            item = self._aggregate_scope(rows, [], scope_name="period", scope_value=bucket)
            table.append(
                {
                    "period": bucket,
                    "run_count": item["run_count"],
                    "would_have_blocked": item["would_have_blocked"],
                    "would_have_required_review": item["would_have_required_review"],
                    "estimated_savings_usd_total": item["estimated_savings_usd_total"],
                    "projected_weekly_cost_usd_total": item["projected_weekly_cost_usd_total"],
                    "risk_category_counts": item["risk_category_counts"],
                }
            )
        return table

    def save_json(self, path: str) -> Path:
        payload = self.build()
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return out

    def save_html(self, path: str) -> Path:
        payload = self.build()
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        patterns = (
            "".join(
                f"<li><code>{_html.escape(str(item['pattern']))}</code> — {item['count']} run(s)</li>"
                for item in payload.get("recurring_waste_patterns", [])
            )
            or "<li>No recurring waste patterns recorded yet.</li>"
        )
        categories = (
            "".join(
                f"<li><code>{_html.escape(str(k))}</code> — {v}</li>"
                for k, v in sorted((payload.get("risk_category_counts") or {}).items())
            )
            or "<li>No risk-category data recorded yet.</li>"
        )
        feedback = (
            "".join(
                f"<li><code>{_html.escape(str(k))}</code> — {v}</li>"
                for k, v in sorted(
                    (payload.get("feedback_summary", {}).get("by_outcome") or {}).items()
                )
            )
            or "<li>No feedback recorded yet.</li>"
        )
        repo_rows = (
            "".join(
                f"<tr><td>{_html.escape(str(item.get('repo')))}</td><td>{item.get('run_count')}</td><td>{item.get('would_have_blocked')}</td><td>${_safe_float(item.get('estimated_savings_usd_total')):,.0f}</td><td>{_html.escape(str((item.get('enforcement_recommendation') or {}).get('tier')))}</td></tr>"
                for item in payload.get("repo_trends", [])[:12]
            )
            or "<tr><td colspan='5'>No repo trend data yet.</td></tr>"
        )
        team_rows = (
            "".join(
                f"<tr><td>{_html.escape(str(item.get('team')))}</td><td>{item.get('run_count')}</td><td>{item.get('would_have_blocked')}</td><td>${_safe_float(item.get('estimated_savings_usd_total')):,.0f}</td><td>{_html.escape(str((item.get('enforcement_recommendation') or {}).get('tier')))}</td></tr>"
                for item in payload.get("team_trends", [])[:12]
            )
            or "<tr><td colspan='5'>No team trend data yet.</td></tr>"
        )
        rec = payload.get("enforcement_recommendation") or {}
        rec_reasons = (
            "".join(f"<li>{_html.escape(str(reason))}</li>" for reason in rec.get("reasons", []))
            or "<li>No recommendation reasons yet.</li>"
        )
        html = f"""<html><head><meta charset='utf-8'><title>SemZero Shadow Dashboard</title>
<style>
body{{font-family:Inter,Arial,sans-serif;max-width:1200px;margin:32px auto;padding:0 18px;background:#f8fafc;color:#0f172a}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:20px;box-shadow:0 10px 24px rgba(15,23,42,.06);grid-column:span 4}}
.card.wide{{grid-column:span 12}} h1,h2{{margin:0 0 12px}} ul{{line-height:1.55}} .kpi{{font-size:28px;font-weight:700}} .muted{{color:#475569}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #e2e8f0;padding:8px;text-align:left}}
</style></head><body>
<h1>SemZero Shadow Dashboard</h1><p class='muted'>{_html.escape(str(payload.get("summary", "")))}</p>
<div class='grid'>
<section class='card'><h2>Runs</h2><div class='kpi'>{payload.get("run_count", 0)}</div><p>Would block: {payload.get("would_have_blocked", 0)} · Would review: {payload.get("would_have_required_review", 0)}</p></section>
<section class='card'><h2>Projected savings</h2><div class='kpi'>${_safe_float(payload.get("estimated_savings_usd_total")):,.0f}</div><p>Weekly risk surfaced: ${_safe_float(payload.get("projected_weekly_cost_usd_total")):,.0f}</p></section>
<section class='card'><h2>Feedback precision proxy</h2><div class='kpi'>{_safe_float(payload.get("feedback_summary", {}).get("precision_proxy")):.0%}</div><p>Overrides: {payload.get("override_count", 0)} · Incidents: {payload.get("incident_count", 0)}</p></section>
<section class='card wide'><h2>Enforcement recommendation</h2><p><strong>{_html.escape(str(rec.get("tier", "TIER_0_SHADOW_ONLY")))}</strong> — {_html.escape(str(rec.get("description", "")))}</p><ul>{rec_reasons}</ul></section>
<section class='card wide'><h2>Repo trends</h2><table><tr><th>Repo</th><th>Runs</th><th>Would block</th><th>Savings</th><th>Recommended tier</th></tr>{repo_rows}</table></section>
<section class='card wide'><h2>Team trends</h2><table><tr><th>Team</th><th>Runs</th><th>Would block</th><th>Savings</th><th>Recommended tier</th></tr>{team_rows}</table></section>
<section class='card wide'><h2>Risk categories</h2><ul>{categories}</ul></section>
<section class='card wide'><h2>Recurring waste patterns</h2><ul>{patterns}</ul></section>
<section class='card wide'><h2>Feedback</h2><ul>{feedback}</ul></section>
</div></body></html>"""
        out.write_text(html, encoding="utf-8")
        return out

    @staticmethod
    def _summary_line(payload: dict[str, Any]) -> str:
        rec = payload.get("enforcement_recommendation") or {}
        return (
            f"Shadow mode recorded {payload.get('run_count', 0)} run(s); SemZero would have blocked "
            f"{payload.get('would_have_blocked', 0)} and surfaced about ${_safe_float(payload.get('estimated_savings_usd_total')):,.0f} "
            f"in immediate savings. Recommended rollout tier: {rec.get('tier', 'TIER_0_SHADOW_ONLY')}."
        )
