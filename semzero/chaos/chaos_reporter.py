"""
chaos_reporter.py — SemZero Chaos Mode Report Generators.

Two outputs for two audiences:

  ChaosTerminalReporter — For engineers running chaos in CI or locally.
    Shows exactly what broke, why, and what to do about it.
    Designed for terminal readability at any window width.

  ChaosHTMLReporter — For data teams and engineering managers.
    A self-contained HTML file that can be emailed, linked in Notion,
    or attached to a PR. No server required — opens in any browser.

    Sections:
      1. Executive Summary — Fragility Score, Grade, key stats
      2. Risk Heatmap — which tables are most dangerous
      3. Mutation Breakdown — what types of changes cause the most failures
      4. Fragility DNA — structural anti-patterns in the schema
      5. Cascade Risk Map — which nodes cause the most downstream damage
      6. Actionable Fixes — prioritised, specific recommendations with copy-paste SQL
      7. Full Mutation Log — every mutation tested, filterable
      8. Score Trend — week-over-week history

Design principles:
  - Accurate over impressive. If nothing broke, say nothing broke clearly.
  - Actionable over descriptive. Every CRITICAL comes with a specific fix.
  - No false precision. Scores are labelled with their mode (graph/dbt/snowflake).
  - Works with 3 tables or 3000. Layout adapts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .chaos_engine import ChaosReport, ResilienceLevel, FragilityDNA

log = logging.getLogger(__name__)

# ── Terminal colours ──────────────────────────────────────────────────────────
_C = {
    "red": "\033[91m",
    "yellow": "\033[93m",
    "green": "\033[92m",
    "blue": "\033[94m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}
R = _C["reset"]


def _sc(s: int) -> str:
    """Score colour."""
    if s >= 80:
        return _C["green"]
    if s >= 60:
        return _C["yellow"]
    return _C["red"]


def _gc(g: str) -> str:
    """Grade colour."""
    return _C["green"] if g in ("A", "B") else _C["yellow"] if g in ("C", "D") else _C["red"]


# ── Terminal reporter ─────────────────────────────────────────────────────────


class ChaosTerminalReporter:
    """
    Prints a structured, actionable chaos report to stdout.

    Designed to be useful even without an HTML viewer — every section
    contains the information a DE needs to take immediate action.
    """

    def print(self, report: ChaosReport) -> None:
        b = _C["bold"]
        s = report.summary()

        self._header(report, s)
        if report.error:
            print(f"\n  {_C['red']}✗ Run failed: {report.error}{R}\n")
            return

        self._score_section(report, s)
        self._stats_section(s)
        self._dna_section(report)
        self._critical_section(report)
        self._fragile_section(report)
        self._mutation_breakdown(report)
        self._verdict(report)

    def _header(self, report: ChaosReport, s: dict) -> None:
        b = _C["bold"]
        mode_labels = {
            "graph": "Graph-only (no dbt tests)",
            "dbt": "dbt test suite",
            "snowflake": "Snowflake clone + dbt",
        }
        mode_str = mode_labels.get(s.get("mode", "graph"), s.get("mode", "graph"))
        print(f"""
{b}╔══════════════════════════════════════════════════╗
║          SemZero — Chaos Mode Report             ║
╚══════════════════════════════════════════════════╝{R}
  Run ID:    {report.run_id}
  Mode:      {mode_str}
  Completed: {report.completed_at[:19] if report.completed_at else "—"} UTC
  Duration:  {report.duration_s:.1f}s
""")

    def _score_section(self, report: ChaosReport, s: dict) -> None:
        b = _C["bold"]
        sc = _sc(report.fragility_score)
        gc = _gc(report.fragility_grade)

        bar_len = 46
        filled = int(report.fragility_score / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        print(f"  {b}Fragility Score{R}")
        print(f"  {sc}{bar}{R}")
        print(
            f"  {sc}{b}{report.fragility_score}/100{R}   Grade {gc}{b}{report.fragility_grade}{R}"
        )

        if report.fragility_score < 80:
            # Show what's dragging the score down
            if s.get("anti_pattern_score", 0) > 20:
                print(
                    f"  {_C['dim']}  ↳ DNA anti-patterns: "
                    f"-{min(25, s['anti_pattern_score'] // 4)} pts{R}"
                )
            if report.drift_velocity > 2:
                print(
                    f"  {_C['dim']}  ↳ Drift velocity: "
                    f"{report.drift_velocity:.1f}/week → risk ×{report.risk_multiplier:.1f}{R}"
                )
        print()

        # Mode disclaimer for graph-only
        if s.get("mode", "graph") == "graph":
            print(f"  {_C['dim']}⚠  Graph-only mode: scores are based on cascade analysis,{R}")
            print(
                f"  {_C['dim']}   not real dbt test failures. Connect dbt for accurate scores.{R}"
            )
            print()

    def _stats_section(self, s: dict) -> None:
        b = _C["bold"]
        print(f"  {b}Results{R}")
        print(f"    Mutations applied:  {s['mutations_applied']}")
        print(
            f"    Mutations that broke: {_C['red']}{s['mutations_that_broke']}{R} "
            f"({s['mutations_that_broke'] / max(s['mutations_applied'], 1) * 100:.0f}%)"
        )
        if s["total_tests_run"] > 0:
            print(f"    Tests run:          {s['total_tests_run']}")
            print(f"    Tests failed:       {_C['red']}{s['total_tests_failed']}{R}")
        print()

        print(f"  {b}Pipeline Resilience{R}")
        print(
            f"    {_C['red']}🔴 CRITICAL:  {s['critical_pipelines']}{R}  — breaks under minor changes"
        )
        print(
            f"    {_C['yellow']}⚠️  FRAGILE:   {s['fragile_pipelines']}{R}  — breaks under some changes"
        )
        print(
            f"    {_C['green']}✅ RESILIENT: {s['resilient_pipelines']}{R}  — passes all tested mutations"
        )
        print()

    def _dna_section(self, report: ChaosReport) -> None:
        if not report.fragility_dna:
            return
        b = _C["bold"]
        dna = report.fragility_dna
        if dna.anti_pattern_score == 0:
            print(f"  {b}Fragility DNA{R}  {_C['green']}✓ No structural anti-patterns found{R}")
            print()
            return

        dna_col = _sc(100 - dna.anti_pattern_score)
        print(
            f"  {b}Fragility DNA{R}  Anti-pattern score: {dna_col}{dna.anti_pattern_score}/100{R}"
        )
        print(f"  {_C['dim']}  Structural issues independent of dbt test coverage{R}")

        if dna.wide_tables:
            print(
                f"    Wide tables (>30 cols):  {_C['yellow']}{len(dna.wide_tables)}{R} "
                f"— {_C['dim']}{', '.join(dna.wide_tables[:2])}{R}"
            )
        if dna.nullable_fk_columns:
            print(
                f"    Nullable FK columns:     {_C['yellow']}{len(dna.nullable_fk_columns)}{R} "
                f"— {_C['dim']}silent NULL propagation risk{R}"
            )
        if dna.deep_fk_chains:
            print(
                f"    Deep FK chains (>3 hop): {_C['yellow']}{len(dna.deep_fk_chains)}{R} "
                f"— {_C['dim']}cascading failure risk{R}"
            )
        if dna.central_columns:
            print(
                f"    Central bottlenecks:     {_C['yellow']}{len(dna.central_columns)}{R} "
                f"— {_C['dim']}{dna.central_columns[0].split(' ')[0]}{R}"
            )
        if dna.high_null_join_cols:
            print(
                f"    High-null join columns:  {_C['red']}{len(dna.high_null_join_cols)}{R} "
                f"— {_C['dim']}data integrity risk{R}"
            )
        if dna.isolated_tables:
            print(
                f"    Isolated tables:         {_C['dim']}{len(dna.isolated_tables)}{R} "
                f"— {_C['dim']}no FK relationships{R}"
            )
        print()

    def _critical_section(self, report: ChaosReport) -> None:
        if not report.critical_pipelines:
            return
        b = _C["bold"]
        print(f"  {b}CRITICAL Pipelines — Fix Before Next Deploy{R}")
        print(f"  {_C['dim']}These break under multiple common mutation types.{R}\n")
        for p in report.critical_pipelines[:8]:
            print(f"  {_C['red']}🔴 {p.model_name}{R}")
            print(f"     Score: {p.fragility_score}/100")
            print(f"     Breaks on: {', '.join(p.breaking_mutations[:4])}")
            print(f"     Action: {_C['dim']}{p.recommendation[:110]}{R}")
            if p.auto_fix_available:
                print(f"     {_C['green']}⚡ semzero repair --open-pr{R}")
            print()

    def _fragile_section(self, report: ChaosReport) -> None:
        if not report.fragile_pipelines:
            return
        b = _C["bold"]
        print(f"  {b}FRAGILE Pipelines — Fix Soon{R}")
        for p in report.fragile_pipelines[:5]:
            breaks = ", ".join(p.breaking_mutations[:2])
            print(f"  {_C['yellow']}⚠  {p.model_name}{R}  {_C['dim']}breaks on: {breaks}{R}")
        print()

    def _mutation_breakdown(self, report: ChaosReport) -> None:
        b = _C["bold"]
        failures: dict[str, int] = {}
        for r in report.mutation_results:
            if r.resilience not in (ResilienceLevel.RESILIENT, ResilienceLevel.UNTESTED):
                k = r.mutation_type.value
                failures[k] = failures.get(k, 0) + 1

        if not failures:
            return

        print(f"  {b}Most Dangerous Change Types{R}")
        total_broke = sum(failures.values())
        for mt, count in sorted(failures.items(), key=lambda x: -x[1])[:5]:
            pct = count / total_broke * 100
            bar = "▓" * min(24, int(pct / 5))
            print(f"  {_C['yellow']}{mt:<22}{R} {bar} {count} ({pct:.0f}%)")
        print()

    def _verdict(self, report: ChaosReport) -> None:
        b = _C["bold"]
        s = report.fragility_score
        if s >= 90:
            print(f"  {_C['green']}{b}✓ Excellent resilience. Schema is well-structured.{R}\n")
        elif s >= 80:
            print(f"  {_C['green']}{b}✓ Good resilience. Minor improvements available.{R}\n")
        elif s >= 60:
            print(
                f"  {_C['yellow']}{b}⚠ Moderate fragility. "
                f"Address {len(report.critical_pipelines)} critical pipeline(s) before deploying.{R}\n"
            )
        else:
            print(
                f"  {_C['red']}{b}✗ High fragility. "
                f"Schema would break under common real-world changes.\n"
                f"    Run: semzero repair --open-pr to start fixing.{R}\n"
            )


# ── HTML reporter ─────────────────────────────────────────────────────────────


class ChaosHTMLReporter:
    """
    Generates a self-contained HTML report.
    No server, no dependencies — open directly in any browser.
    Suitable for emailing to a data team or attaching to a GitHub PR.
    """

    def generate(
        self,
        report: ChaosReport,
        history: Optional[list[dict]] = None,
        output_path: str = "data/chaos_report.html",
    ) -> Path:
        html = self._render(report, history or [])
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        log.info(f"HTML report → {path}")
        return path

    def _render(self, report: ChaosReport, history: list[dict]) -> str:
        s = report.summary()
        dna = report.fragility_dna
        score = s["fragility_score"]
        grade = s.get("fragility_grade", "?")
        gen = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        score_hex = "#22c55e" if score >= 80 else "#f59e0b" if score >= 60 else "#ef4444"

        mode_label = {
            "graph": "Graph Mode",
            "dbt": "dbt Mode",
            "snowflake": "Snowflake Mode",
        }.get(s.get("mode", "graph"), "Graph Mode")

        mode_note = {
            "graph": "Scores derived from cascade analysis. Connect dbt for test-backed scores.",
            "dbt": "Scores backed by real dbt test failures.",
            "snowflake": "Scores backed by real dbt tests on Snowflake zero-copy clone.",
        }.get(s.get("mode", "graph"), "")

        # ── Pre-compute all data ──────────────────────────────────────────────

        # Mutation chart
        mut_labels = sorted({r.mutation_type.value for r in report.mutation_results})
        resilient_d = [
            sum(
                1
                for r in report.mutation_results
                if r.mutation_type.value == mt and r.resilience == ResilienceLevel.RESILIENT
            )
            for mt in mut_labels
        ]
        fragile_d = [
            sum(
                1
                for r in report.mutation_results
                if r.mutation_type.value == mt and r.resilience == ResilienceLevel.FRAGILE
            )
            for mt in mut_labels
        ]
        critical_d = [
            sum(
                1
                for r in report.mutation_results
                if r.mutation_type.value == mt and r.resilience == ResilienceLevel.CRITICAL
            )
            for mt in mut_labels
        ]
        untested_d = [
            sum(
                1
                for r in report.mutation_results
                if r.mutation_type.value == mt and r.resilience == ResilienceLevel.UNTESTED
            )
            for mt in mut_labels
        ]

        # Cascade scatter
        cascade_pts = [
            {
                "x": round(r.blast_score * 100, 1),
                "y": round(r.cascade.cascade_score * 100, 1) if r.cascade else 0,
                "r": max(4, min(18, (r.cascade.total_impacted if r.cascade else 0) * 2)),
                "label": r.node_id,
                "resilience": r.resilience.value,
            }
            for r in report.mutation_results
            if r.cascade and r.blast_score > 0
        ]

        # Risk heatmap — tables ranked by how many mutations broke them
        table_risk: dict[str, dict] = {}
        for r in report.mutation_results:
            table = r.node_id.split(".")[0] if "." in r.node_id else r.node_id
            if table not in table_risk:
                table_risk[table] = {"critical": 0, "fragile": 0, "resilient": 0, "total": 0}
            table_risk[table]["total"] += 1
            if r.resilience == ResilienceLevel.CRITICAL:
                table_risk[table]["critical"] += 1
            elif r.resilience == ResilienceLevel.FRAGILE:
                table_risk[table]["fragile"] += 1
            elif r.resilience == ResilienceLevel.RESILIENT:
                table_risk[table]["resilient"] += 1

        heatmap_tables = sorted(
            table_risk.items(),
            key=lambda x: -(x[1]["critical"] * 3 + x[1]["fragile"]),
        )[:15]

        # History trend
        h_labels = [h.get("run_id", "")[:6] for h in history[-16:]]
        h_scores = [h.get("fragility_score", 0) for h in history[-16:]]
        h_dna = [h.get("anti_pattern_score", 0) for h in history[-16:]]

        # DNA values
        dna_values = [
            min(100, len(dna.wide_tables) * 20) if dna else 0,
            min(100, len(dna.nullable_fk_columns) * 15) if dna else 0,
            min(100, len(dna.deep_fk_chains) * 25) if dna else 0,
            min(100, len(dna.central_columns) * 20) if dna else 0,
            min(100, len(dna.high_null_join_cols) * 20) if dna else 0,
            min(100, len(dna.isolated_tables) * 8) if dna else 0,
        ]

        # ── Build HTML sections ───────────────────────────────────────────────

        # DNA detail rows
        dna_rows = ""
        if dna:
            dna_row_data = [
                (
                    "Wide tables (>30 cols)",
                    dna.wide_tables,
                    "Hard to track changes. Break one column, affect many.",
                ),
                (
                    "Nullable FK columns",
                    dna.nullable_fk_columns,
                    "NULL values silently propagate downstream. Use NOT NULL + DEFAULT.",
                ),
                (
                    "Deep FK chains (>3 hops)",
                    dna.deep_fk_chains,
                    "A single column change cascades through multiple downstream tables.",
                ),
                (
                    "Central bottleneck columns",
                    dna.central_columns,
                    "High betweenness centrality — these columns bridge the schema.",
                ),
                (
                    "High-null join columns (>5%)",
                    dna.high_null_join_cols,
                    "Join columns with high null rate cause silent data loss.",
                ),
                (
                    "Isolated tables",
                    dna.isolated_tables,
                    "No FK relationships. May indicate orphaned tables or missing constraints.",
                ),
            ]
            for label, items, why in dna_row_data:
                if not items:
                    severity = "ok"
                    badge = "✅ Clean"
                    items_str = "—"
                elif len(items) >= 3:
                    severity = "critical"
                    badge = f"🔴 {len(items)} found"
                    items_str = " · ".join(str(i)[:35] for i in items[:4])
                else:
                    severity = "warning"
                    badge = f"⚠️ {len(items)} found"
                    items_str = " · ".join(str(i)[:35] for i in items[:4])

                col = {
                    "ok": "var(--green)",
                    "warning": "var(--yellow)",
                    "critical": "var(--red)",
                }[severity]

                dna_rows += f"""
<tr>
  <td style="font-weight:500">{label}</td>
  <td><span style="color:{col}">{badge}</span></td>
  <td style="font-size:11px;color:var(--dim)">{items_str}</td>
  <td style="font-size:11px;color:var(--dim)">{why}</td>
</tr>"""

        # Pipeline fragility rows
        p_rows = ""
        for p in report.pipeline_fragility[:30]:
            col = {
                "CRITICAL": "var(--red)",
                "FRAGILE": "var(--yellow)",
                "RESILIENT": "var(--green)",
                "UNTESTED": "var(--dim)",
            }.get(p.resilience.value, "var(--dim)")
            emoji = {
                "CRITICAL": "🔴",
                "FRAGILE": "⚠️",
                "RESILIENT": "✅",
                "UNTESTED": "❓",
            }.get(p.resilience.value, "❓")
            fix_badge = (
                '<span style="color:var(--green);font-size:10px;'
                'background:rgba(34,197,94,0.1);padding:2px 6px;border-radius:3px">'
                "⚡ Auto-fix</span>"
                if p.auto_fix_available
                else ""
            )
            score_bar_w = p.fragility_score
            p_rows += f"""
<tr>
  <td><code>{p.model_name}</code></td>
  <td style="color:{col};white-space:nowrap">{emoji} {p.resilience.value}</td>
  <td>
    <div style="display:flex;align-items:center;gap:8px">
      <div style="width:60px;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
        <div style="width:{score_bar_w}%;height:100%;background:{col}"></div>
      </div>
      <span style="font-size:11px">{p.fragility_score}/100</span>
    </div>
  </td>
  <td style="font-size:11px">{", ".join(p.breaking_mutations[:3])}</td>
  <td style="font-size:11px;color:var(--dim);max-width:240px">{p.recommendation[:100]}</td>
  <td>{fix_badge}</td>
</tr>"""

        # Mutation log rows
        m_rows = ""
        for r in report.mutation_results:
            col = {
                "CRITICAL": "var(--red)",
                "FRAGILE": "var(--yellow)",
                "RESILIENT": "var(--green)",
                "UNTESTED": "var(--dim)",
            }.get(r.resilience.value, "var(--dim)")
            emoji = {
                "CRITICAL": "🔴",
                "FRAGILE": "⚠️",
                "RESILIENT": "✅",
                "UNTESTED": "❓",
            }.get(r.resilience.value, "❓")
            cas_score = f"{r.cascade.cascade_score:.2f}" if r.cascade else "—"
            cas_depth = str(r.cascade.max_depth) if r.cascade else "—"
            cas_nodes = str(r.cascade.total_impacted) if r.cascade else "—"
            m_rows += f"""
<tr data-resilience="{r.resilience.value}">
  <td><code style="font-size:10px">{r.mutation_type.value}</code></td>
  <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis"
      title="{r.detail}">{r.detail[:60]}</td>
  <td style="color:{col};white-space:nowrap">{emoji} {r.resilience.value}</td>
  <td style="font-size:11px;color:var(--dim)">{r.targeting_reason[:45]}</td>
  <td style="font-size:11px">{r.tests_failed}/{r.tests_run}</td>
  <td style="color:var(--yellow);font-size:11px">{cas_score}</td>
  <td style="font-size:11px">{cas_depth}</td>
  <td style="font-size:11px">{cas_nodes}</td>
  <td style="color:var(--dim);font-size:11px">{r.duration_s:.1f}s</td>
</tr>"""

        # Actionable fixes section
        fixes_html = ""
        if report.critical_pipelines or report.fragile_pipelines:
            fixes_html = '<div class="card full"><h3>Actionable Fixes — Prioritised</h3>'
            fixes_html += '<div style="display:flex;flex-direction:column;gap:12px;margin-top:4px">'
            for i, p in enumerate(report.critical_pipelines[:5], 1):
                fixes_html += f"""
<div style="border:1px solid var(--red);border-radius:8px;padding:14px;background:rgba(239,68,68,0.04)">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <span style="color:var(--red);font-weight:700;font-size:13px">#{i} 🔴 {p.model_name}</span>
      <span style="color:var(--dim);font-size:11px;margin-left:10px">
        Breaks on: {", ".join(p.breaking_mutations[:3])}
      </span>
    </div>
    <span style="color:var(--dim);font-size:11px">Score: {p.fragility_score}/100</span>
  </div>
  <div style="margin-top:8px;font-size:12px;color:var(--text)">{p.recommendation}</div>
  {"<div style='margin-top:8px'><code style='color:var(--green);font-size:11px'>semzero repair --open-pr</code></div>" if p.auto_fix_available else ""}
</div>"""

            for p in report.fragile_pipelines[:3]:
                fixes_html += f"""
<div style="border:1px solid var(--yellow);border-radius:8px;padding:12px;background:rgba(245,158,11,0.04)">
  <span style="color:var(--yellow);font-weight:600;font-size:12px">⚠️ {p.model_name}</span>
  <span style="color:var(--dim);font-size:11px;margin-left:10px">{p.recommendation[:100]}</span>
</div>"""

            fixes_html += "</div></div>"

        # Heatmap rows
        heatmap_rows = ""
        for table_name, risk in heatmap_rows[:12] if False else heatmap_tables:
            total = max(risk["total"], 1)
            crit_w = risk["critical"] / total * 100
            frag_w = risk["fragile"] / total * 100
            res_w = risk["resilient"] / total * 100
            risk_score = (risk["critical"] * 3 + risk["fragile"]) / total
            level = "🔴" if risk_score > 1.5 else "⚠️" if risk_score > 0.3 else "✅"
            heatmap_rows += f"""
<tr>
  <td><code>{table_name}</code></td>
  <td>{level}</td>
  <td>
    <div style="display:flex;height:10px;border-radius:3px;overflow:hidden;width:120px;gap:1px">
      <div style="width:{crit_w:.0f}%;background:var(--red)"></div>
      <div style="width:{frag_w:.0f}%;background:var(--yellow)"></div>
      <div style="width:{res_w:.0f}%;background:var(--green)"></div>
    </div>
  </td>
  <td style="font-size:11px;color:var(--red)">{risk["critical"]}</td>
  <td style="font-size:11px;color:var(--yellow)">{risk["fragile"]}</td>
  <td style="font-size:11px;color:var(--green)">{risk["resilient"]}</td>
  <td style="font-size:11px">{risk["total"]}</td>
</tr>"""

        # Trend section (pre-computed, no nested f-string)
        if len(history) >= 2:
            trend_card = """
  <div class="card full">
    <h3>Score Trend</h3>
    <canvas id="trendChart" height="55"></canvas>
  </div>"""
            trend_js = f"""
new Chart(document.getElementById('trendChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(h_labels)},
    datasets: [
      {{
        label: 'Fragility Score',
        data: {json.dumps(h_scores)},
        borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.1)',
        fill: true, tension: 0.4, pointRadius: 4,
      }},
      {{
        label: 'DNA Score',
        data: {json.dumps(h_dna)},
        borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.05)',
        fill: false, tension: 0.4, pointRadius: 3, borderDash: [4,2],
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#2d3144' }} }},
      y: {{ min: 0, max: 100, ticks: {{ color: '#64748b' }}, grid: {{ color: '#2d3144' }} }}
    }}
  }}
}});"""
        else:
            trend_card = ""
            trend_js = ""

        # ── Assemble full HTML ────────────────────────────────────────────────

        # Pre-compute heatmap HTML (nested f-strings not supported in Python 3.11)
        if not heatmap_tables:
            heatmap_table_html = "<p style='color:var(--dim);font-size:12px'>No table risk data — run chaos with more mutations.</p>"
        else:
            heatmap_table_html = (
                "<table><thead><tr>"
                "<th>Table</th><th>Risk</th><th>Profile</th>"
                '<th style="color:var(--red)">Critical</th>'
                '<th style="color:var(--yellow)">Fragile</th>'
                '<th style="color:var(--green)">Resilient</th>'
                "<th>Total</th>"
                "</tr></thead>"
                f"<tbody>{heatmap_rows}</tbody></table>"
            )

        # Pre-compute pipeline table HTML
        if not report.pipeline_fragility:
            pipeline_table_html = "<p style='color:var(--green);padding:16px;font-size:13px'>✅ All pipelines passed all mutations.</p>"
        else:
            pipeline_table_html = (
                "<table><thead><tr>"
                "<th>Pipeline / Model</th><th>Resilience</th><th>Score</th>"
                "<th>Breaking Mutations</th><th>Recommendation</th><th>Fix</th>"
                "</tr></thead>"
                f"<tbody>{p_rows}</tbody></table>"
            )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SemZero Chaos Mode — {gen}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root {{
  --bg:#0f1117; --surface:#1a1d27; --surface2:#1e2235;
  --border:#2d3144; --text:#e2e8f0; --dim:#64748b;
  --accent:#6366f1; --red:#ef4444; --yellow:#f59e0b;
  --blue:#3b82f6; --green:#22c55e; --purple:#a855f7;
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{
  background:var(--bg); color:var(--text);
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  font-size:14px; line-height:1.6;
}}
header {{
  background:var(--surface); border-bottom:1px solid var(--border);
  padding:16px 28px; display:flex; justify-content:space-between; align-items:center;
  position:sticky; top:0; z-index:100;
}}
header h1 {{ font-size:18px; font-weight:700; letter-spacing:-0.3px; }}
header h1 em {{ color:var(--accent); font-style:normal; }}
.badges {{ display:flex; gap:8px; align-items:center; }}
.badge {{
  font-size:11px; padding:3px 9px; border-radius:4px;
  background:rgba(99,102,241,0.15); color:var(--accent);
}}
.badge-mode {{
  background:rgba(34,197,94,0.1); color:var(--green);
}}
.badge-warn {{
  background:rgba(245,158,11,0.1); color:var(--yellow);
}}
.meta {{ color:var(--dim); font-size:12px; display:flex; gap:14px; }}
.layout {{
  display:grid; grid-template-columns:repeat(3, 1fr);
  gap:16px; padding:20px 28px;
  max-width:1600px; margin:0 auto;
}}
.full  {{ grid-column:1/-1; }}
.half  {{ grid-column:span 2; }}
.card {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:10px; padding:18px;
}}
.card h3 {{
  font-size:10px; font-weight:700; text-transform:uppercase;
  letter-spacing:1.2px; color:var(--dim); margin-bottom:14px;
}}
.score-wrap {{ text-align:center; padding:10px 0; }}
.score-num {{
  font-size:76px; font-weight:800;
  color:{score_hex}; line-height:1; letter-spacing:-3px;
}}
.score-grade {{ font-size:28px; font-weight:700; color:{score_hex}; margin-top:4px; }}
.score-sub {{ color:var(--dim); font-size:12px; margin-top:6px; }}
.gauge {{
  height:8px; background:var(--border); border-radius:4px;
  overflow:hidden; margin:12px 0 6px;
}}
.gauge-fill {{
  height:100%; border-radius:4px; background:{score_hex};
  width:{score}%;
  transition:width 1.4s cubic-bezier(.4,0,.2,1);
}}
.mode-note {{
  font-size:11px; color:var(--dim); text-align:center;
  padding:6px; border-radius:4px; background:rgba(99,102,241,0.05);
  border:1px solid var(--border); margin-top:6px;
}}
.stat-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
.stat {{
  background:var(--bg); border-radius:6px; padding:12px; text-align:center;
  border:1px solid var(--border);
}}
.stat-v {{ font-size:28px; font-weight:700; }}
.stat-l {{ font-size:10px; color:var(--dim); margin-top:3px; text-transform:uppercase; }}
.red    {{ color:var(--red); }}
.yellow {{ color:var(--yellow); }}
.green  {{ color:var(--green); }}
.purple {{ color:var(--purple); }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{
  text-align:left; padding:8px 10px; border-bottom:2px solid var(--border);
  font-size:10px; text-transform:uppercase; letter-spacing:0.6px; color:var(--dim);
  position:sticky; top:0; background:var(--surface);
}}
td {{ padding:8px 10px; border-bottom:1px solid rgba(45,49,68,0.4); vertical-align:middle; }}
tr:hover td {{ background:rgba(255,255,255,0.02); }}
code {{
  background:rgba(99,102,241,0.12); color:var(--accent);
  padding:2px 6px; border-radius:3px; font-size:11px; font-family:monospace;
}}
.filter-bar {{
  display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;
}}
.filter-btn {{
  padding:4px 12px; border-radius:20px; font-size:11px; cursor:pointer;
  border:1px solid var(--border); background:var(--bg); color:var(--dim);
  transition:all 0.15s;
}}
.filter-btn:hover, .filter-btn.active {{
  border-color:var(--accent); color:var(--accent);
  background:rgba(99,102,241,0.1);
}}
.filter-btn.active-critical {{ border-color:var(--red);color:var(--red);background:rgba(239,68,68,0.1); }}
.filter-btn.active-fragile  {{ border-color:var(--yellow);color:var(--yellow);background:rgba(245,158,11,0.1); }}
.filter-btn.active-resilient{{ border-color:var(--green);color:var(--green);background:rgba(34,197,94,0.1); }}
::-webkit-scrollbar {{ width:4px; height:4px; }}
::-webkit-scrollbar-thumb {{ background:var(--border); border-radius:2px; }}
@media (max-width:900px) {{
  .layout {{ grid-template-columns:1fr; }}
  .half, .full {{ grid-column:1/-1; }}
}}
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center;gap:12px">
    <h1>Sem<em>Zero</em></h1>
    <div class="badges">
      <span class="badge">Chaos Mode</span>
      <span class="badge-mode badge">{mode_label}</span>
      {
            "<span class='badge-warn badge'>⚠ Graph-only scores</span>"
            if s.get("mode", "graph") == "graph"
            else ""
        }
    </div>
  </div>
  <div class="meta">
    <span>Run {report.run_id}</span>
    <span>·</span>
    <span>{s["mutations_applied"]} mutations</span>
    <span>·</span>
    <span>{gen}</span>
  </div>
</header>

<div class="layout">

  <!-- ── 1. Fragility Score ── -->
  <div class="card">
    <h3>Fragility Score</h3>
    <div class="score-wrap">
      <div class="score-num">{score}</div>
      <div class="score-grade">Grade {grade}</div>
      <div class="score-sub">out of 100</div>
    </div>
    <div class="gauge"><div class="gauge-fill"></div></div>
    <div class="mode-note">{mode_note}</div>
    {
            ""
            if not report.drift_velocity
            else f'<div style="color:var(--yellow);font-size:11px;text-align:center;margin-top:8px">'
            f"⚡ Drift velocity {report.drift_velocity:.1f}/week → risk ×{report.risk_multiplier:.1f}"
            f"</div>"
        }
  </div>

  <!-- ── 2. Mutation Results ── -->
  <div class="card">
    <h3>Mutation Results</h3>
    <div class="stat-grid">
      <div class="stat">
        <div class="stat-v">{s["mutations_applied"]}</div>
        <div class="stat-l">Mutations Applied</div>
      </div>
      <div class="stat">
        <div class="stat-v red">{s["mutations_that_broke"]}</div>
        <div class="stat-l">Caused Failures</div>
      </div>
      <div class="stat">
        <div class="stat-v">{s["total_tests_run"]}</div>
        <div class="stat-l">Tests Run</div>
      </div>
      <div class="stat">
        <div class="stat-v red">{s["total_tests_failed"]}</div>
        <div class="stat-l">Tests Failed</div>
      </div>
    </div>
  </div>

  <!-- ── 3. Pipeline Resilience ── -->
  <div class="card">
    <h3>Pipeline Resilience</h3>
    <div style="display:flex;flex-direction:column;gap:8px;margin-top:4px">
      <div class="stat">
        <div class="stat-v red">🔴 {s["critical_pipelines"]}</div>
        <div class="stat-l">Critical — Fix Now</div>
      </div>
      <div class="stat">
        <div class="stat-v yellow">⚠️ {s["fragile_pipelines"]}</div>
        <div class="stat-l">Fragile — Fix Soon</div>
      </div>
      <div class="stat">
        <div class="stat-v green">✅ {s["resilient_pipelines"]}</div>
        <div class="stat-l">Resilient</div>
      </div>
    </div>
  </div>

  <!-- ── 4. Table Risk Heatmap ── -->
  <div class="card half">
    <h3>Table Risk Heatmap — Most Vulnerable Tables</h3>
    {heatmap_table_html}
  </div>

  <!-- ── 5. Fragility DNA ── -->
  <div class="card">
    <h3>Fragility DNA — Anti-pattern Score: {s["anti_pattern_score"]}/100</h3>
    <canvas id="dnaChart" height="170"></canvas>
  </div>

  <!-- ── 6. Mutation Breakdown ── -->
  <div class="card half">
    <h3>Resilience by Mutation Type</h3>
    <canvas id="mutChart" height="90"></canvas>
  </div>

  <!-- ── 7. Cascade Risk Map ── -->
  <div class="card">
    <h3>Cascade Risk Map</h3>
    <p style="font-size:11px;color:var(--dim);margin-bottom:10px">
      X = blast score · Y = cascade severity · Size = downstream nodes impacted
    </p>
    <canvas id="cascadeChart" height="150"></canvas>
  </div>

  <!-- ── 8. Trend (only if history) ── -->
  {trend_card}

  <!-- ── 9. DNA Anti-pattern Details ── -->
  <div class="card full">
    <h3>Fragility DNA — Structural Anti-patterns</h3>
    <p style="font-size:11px;color:var(--dim);margin-bottom:12px">
      Detected from graph structure. Independent of dbt test coverage.
      These exist regardless of what your tests cover.
    </p>
    <table>
      <thead>
        <tr>
          <th>Anti-pattern</th><th>Status</th>
          <th>Affected</th><th>Why It Matters</th>
        </tr>
      </thead>
      <tbody>{dna_rows}</tbody>
    </table>
  </div>

  <!-- ── 10. Actionable Fixes ── -->
  {fixes_html}

  <!-- ── 11. Pipeline Fragility Table ── -->
  <div class="card full">
    <h3>Pipeline Fragility Detail — {len(report.pipeline_fragility)} pipelines assessed</h3>
    {pipeline_table_html}
  </div>

  <!-- ── 12. Full Mutation Log ── -->
  <div class="card full">
    <h3>Full Mutation Log — {len(report.mutation_results)} mutations</h3>
    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterMutations('ALL')">All</button>
      <button class="filter-btn" onclick="filterMutations('CRITICAL')">🔴 Critical</button>
      <button class="filter-btn" onclick="filterMutations('FRAGILE')">⚠️ Fragile</button>
      <button class="filter-btn" onclick="filterMutations('RESILIENT')">✅ Resilient</button>
      <button class="filter-btn" onclick="filterMutations('UNTESTED')">❓ Untested</button>
    </div>
    <div style="overflow-x:auto">
    <table id="mutationLog">
      <thead>
        <tr>
          <th>Mutation Type</th><th>Detail</th><th>Result</th>
          <th>Why Targeted</th><th>Failed/Run</th>
          <th>Cascade Score</th><th>Depth</th><th>Downstream</th><th>Time</th>
        </tr>
      </thead>
      <tbody>{m_rows}</tbody>
    </table>
    </div>
  </div>

</div><!-- end .layout -->

<script>
// ── Charts ────────────────────────────────────────────────────────────────────
const chartBase = {{
  responsive: true,
  plugins: {{ legend: {{ labels: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }} }},
  scales: {{
    x: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#2d3144' }} }},
    y: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#2d3144' }} }},
  }}
}};

// Mutation breakdown
new Chart(document.getElementById('mutChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(mut_labels)},
    datasets: [
      {{ label: 'Resilient', data: {
            json.dumps(resilient_d)
        }, backgroundColor: 'rgba(34,197,94,0.75)' }},
      {{ label: 'Fragile',   data: {
            json.dumps(fragile_d)
        },   backgroundColor: 'rgba(245,158,11,0.75)' }},
      {{ label: 'Critical',  data: {
            json.dumps(critical_d)
        },  backgroundColor: 'rgba(239,68,68,0.75)' }},
      {{ label: 'Untested',  data: {
            json.dumps(untested_d)
        },  backgroundColor: 'rgba(100,116,139,0.5)' }},
    ]
  }},
  options: {{
    ...chartBase, responsive: true,
    scales: {{
      x: {{ ...chartBase.scales.x, stacked: true }},
      y: {{ ...chartBase.scales.y, stacked: true }},
    }}
  }}
}});

// DNA radar
new Chart(document.getElementById('dnaChart'), {{
  type: 'radar',
  data: {{
    labels: ['Wide Tables', 'Nullable FKs', 'Deep Chains', 'Central Cols', 'Null Joins', 'Isolated'],
    datasets: [{{
      label: 'Anti-pattern exposure',
      data: {json.dumps(dna_values)},
      backgroundColor: 'rgba(168,85,247,0.2)',
      borderColor: 'rgba(168,85,247,0.8)',
      pointBackgroundColor: '#a855f7',
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      r: {{
        min: 0, max: 100,
        ticks: {{ color: '#64748b', font: {{ size: 9 }}, stepSize: 25, backdropColor: 'transparent' }},
        grid: {{ color: '#2d3144' }},
        pointLabels: {{ color: '#94a3b8', font: {{ size: 10 }} }},
        angleLines: {{ color: '#2d3144' }}
      }}
    }}
  }}
}});

// Cascade bubble chart
const cascadeData = {json.dumps(cascade_pts)};
new Chart(document.getElementById('cascadeChart'), {{
  type: 'bubble',
  data: {{
    datasets: [
      {{
        label: 'Critical',
        data: cascadeData.filter(d => d.resilience === 'CRITICAL'),
        backgroundColor: 'rgba(239,68,68,0.65)',
      }},
      {{
        label: 'Fragile',
        data: cascadeData.filter(d => d.resilience === 'FRAGILE'),
        backgroundColor: 'rgba(245,158,11,0.65)',
      }},
      {{
        label: 'Resilient',
        data: cascadeData.filter(d => d.resilience === 'RESILIENT'),
        backgroundColor: 'rgba(34,197,94,0.4)',
      }},
    ]
  }},
  options: {{
    ...chartBase, responsive: true,
    scales: {{
      x: {{ ...chartBase.scales.x, min: 0, max: 100,
            title: {{ display: true, text: 'Blast Score %', color: '#64748b' }} }},
      y: {{ ...chartBase.scales.y, min: 0, max: 100,
            title: {{ display: true, text: 'Cascade Score %', color: '#64748b' }} }},
    }}
  }}
}});

{trend_js}

// ── Mutation log filter ────────────────────────────────────────────────────────
function filterMutations(type) {{
  const rows  = document.querySelectorAll('#mutationLog tbody tr');
  const btns  = document.querySelectorAll('.filter-btn');
  btns.forEach(b => b.classList.remove('active','active-critical','active-fragile','active-resilient'));
  event.target.classList.add(
    type === 'CRITICAL'  ? 'active-critical'  :
    type === 'FRAGILE'   ? 'active-fragile'   :
    type === 'RESILIENT' ? 'active-resilient' : 'active'
  );
  rows.forEach(r => {{
    r.style.display = (type === 'ALL' || r.dataset.resilience === type) ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""
