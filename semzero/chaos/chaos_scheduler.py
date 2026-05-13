"""
chaos_scheduler.py — Weekly Chaos Mode automation.

Runs Chaos Mode on a schedule. Tracks Fragility Score over time.
Posts to Slack every Monday morning. Opens repair PRs for critical pipelines.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .chaos_engine import ChaosConfig, ChaosEngine, ChaosReport
from .chaos_reporter import ChaosTerminalReporter, ChaosHTMLReporter

log = logging.getLogger(__name__)


class ChaosScheduler:
    def __init__(
        self,
        config: ChaosConfig,
        schedule: str = "weekly",
    ) -> None:
        self.config = config
        self.schedule = schedule
        self.data_dir = Path(config.data_dir)
        self.hist_path = Path(config.history_path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._history = self._load_history()

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def run_once(self, graph_json: Optional[dict] = None) -> ChaosReport:
        engine = ChaosEngine(self.config)
        report = engine.run(graph_json=graph_json)
        self._process(report)
        return report

    def start(self) -> None:
        self._running = True
        interval = self._parse_schedule()

        print(f"""
  ╔══════════════════════════════════════════╗
  ║     SemZero Chaos Scheduler              ║
  ╚══════════════════════════════════════════╝

  Schedule:   {self.schedule}  ({interval}s)
  Mutations:  {self.config.mutation_count}
  Mode:       {"dbt+graph" if self.config.run_dbt_tests else "graph-only"}
  Slack:      {"✓" if self.config.slack_webhook else "─"}
  GitHub PRs: {"✓" if self.config.github_repo else "─"}

  Press Ctrl+C to stop.
""")

        while self._running:
            log.info("Running chaos cycle...")
            try:
                report = self.run_once()
                log.info(f"Cycle: {report.fragility_score}/100 Grade {report.fragility_grade}")
            except Exception as e:
                log.error(f"Chaos cycle failed: {e}", exc_info=True)

            self._interruptible_sleep(interval)

    def _process(self, report: ChaosReport) -> None:
        report.save(str(self.data_dir / "chaos_report.json"))

        self._history.append(report.summary())
        self._history = self._history[-52:]
        self._save_history()

        ChaosTerminalReporter().print(report)

        if self.config.generate_html:
            ChaosHTMLReporter().generate(
                report=report,
                history=self._history,
                output_path=str(self.data_dir / "chaos_report.html"),
            )

        if self.config.slack_webhook:
            self._slack(report)

        if self.config.github_repo and self.config.github_token:
            self._open_prs(report)

    def _slack(self, report: ChaosReport) -> None:
        try:
            import requests

            s = report.summary()
            score = s["fragility_score"]
            col = "#22c55e" if score >= 80 else "#f59e0b" if score >= 60 else "#ef4444"
            emoji = "🟢" if score >= 80 else "🟡" if score >= 60 else "🔴"

            # Trend
            trend = ""
            if len(self._history) >= 2:
                prev = self._history[-2].get("fragility_score", score)
                d = score - prev
                trend = f" ({'↑' if d > 0 else '↓'}{abs(d)})" if d else " (→)"

            # DNA
            dna_text = ""
            if report.fragility_dna and report.fragility_dna.anti_pattern_score > 20:
                dna_text = (
                    f"\n*🧬 Fragility DNA:* anti-pattern score "
                    f"{report.fragility_dna.anti_pattern_score}/100"
                )

            # Critical list
            crit_text = ""
            if report.critical_pipelines:
                items = "\n".join(
                    f"• `{p.model_name}` — {p.recommendation[:70]}"
                    for p in report.critical_pipelines[:4]
                )
                crit_text = f"\n\n*🔴 Critical Pipelines:*\n{items}"

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "SemZero Weekly Fragility Report 🧨"},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{emoji} *Score: {score}/100 (Grade {s['fragility_grade']})*{trend}\n"
                            f"_{report.completed_at[:10]} · {s['mode']} mode · "
                            f"{s['mutations_applied']} mutations · "
                            f"{s['mutations_that_broke']} broke pipelines_"
                            f"{dna_text}"
                        ),
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*🔴 Critical*\n{s['critical_pipelines']}"},
                        {"type": "mrkdwn", "text": f"*⚠️ Fragile*\n{s['fragile_pipelines']}"},
                        {"type": "mrkdwn", "text": f"*✅ Resilient*\n{s['resilient_pipelines']}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*🧬 DNA Score*\n{s['anti_pattern_score']}/100",
                        },
                    ],
                },
            ]
            if crit_text:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": crit_text}})
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "SemZero Chaos Mode — Proactive Pipeline Resilience",
                        }
                    ],
                }
            )

            requests.post(
                self.config.slack_webhook,
                json={
                    "channel": self.config.slack_channel,
                    "attachments": [{"color": col, "blocks": blocks}],
                },
                timeout=10,
            )
        except Exception as e:
            log.error(f"Slack failed: {e}")

    def _open_prs(self, report: ChaosReport) -> None:
        if not report.critical_pipelines:
            return
        try:
            from ..integrations.github_pr import PRBot

            drift = {
                "detected_at": report.completed_at,
                "summary": {
                    "total_changes": len(report.critical_pipelines),
                    "by_severity": {"CRITICAL": len(report.critical_pipelines)},
                    "is_clean": False,
                },
                "events": [
                    {
                        "change_type": "CHAOS_CRITICAL",
                        "severity": "CRITICAL",
                        "node_id": p.model_name,
                        "before": None,
                        "after": None,
                        "detail": p.recommendation,
                    }
                    for p in report.critical_pipelines[:5]
                ],
            }
            plan = {
                "summary": {
                    "total_actions": len(report.critical_pipelines),
                    "auto_executable": sum(
                        1 for p in report.critical_pipelines if p.auto_fix_available
                    ),
                    "needs_approval": sum(
                        1 for p in report.critical_pipelines if not p.auto_fix_available
                    ),
                },
                "actions": [
                    {
                        "node_id": p.model_name,
                        "strategy": "MANUAL_REQUIRED",
                        "severity": "CRITICAL",
                        "approval_required": True,
                        "confidence": 1.0,
                        "sql": None,
                        "dbt_patch": f"# CHAOS: {p.model_name} — {p.recommendation}",
                        "notes": p.recommendation,
                        "drift_detail": f"Chaos run {report.run_id}",
                    }
                    for p in report.critical_pipelines[:5]
                ],
            }
            sql = (
                f"-- SemZero Chaos Mode Run {report.run_id}\n"
                f"-- Fragility Score: {report.fragility_score}/100\n"
            )
            bot = PRBot(repo=self.config.github_repo, token=self.config.github_token)
            result = bot.open_pr(drift, plan, sql)
            if result.success:
                log.info(f"Chaos PR: {result.pr_url}")
        except Exception as e:
            log.error(f"PR failed: {e}")

    def _load_history(self) -> list[dict]:
        if not self.hist_path.exists():
            return []
        try:
            return json.loads(self.hist_path.read_text())
        except Exception:
            return []

    def _save_history(self) -> None:
        self.hist_path.parent.mkdir(parents=True, exist_ok=True)
        self.hist_path.write_text(json.dumps(self._history, indent=2))

    def _parse_schedule(self) -> int:
        s = self.schedule.lower()
        if s == "weekly":
            return 7 * 24 * 3600
        if s == "daily":
            return 24 * 3600
        if s == "hourly":
            return 3600
        if s.startswith("interval:"):
            return int(s.split(":")[1])
        return 7 * 24 * 3600

    def _interruptible_sleep(self, seconds: int) -> None:
        deadline = time.time() + seconds
        while self._running and time.time() < deadline:
            time.sleep(min(60, deadline - time.time()))

    def _shutdown(self, signum, frame) -> None:
        log.info("Shutting down chaos scheduler...")
        self._running = False
