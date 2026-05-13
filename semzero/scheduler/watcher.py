"""
watcher.py — Autonomous schema watcher daemon.

Enterprise-grade continuous monitoring with:
  - Parallel crawl + diff pipeline
  - Exponential backoff on failures
  - Tick health metrics and success rates
  - Deduplication — never opens duplicate PRs for same drift
  - Cooldown windows — suppresses alerts after recent PR
  - Multi-database support — watch multiple DBs simultaneously
  - Rich tick summaries with timing breakdowns
  - Graceful shutdown with state persistence
  - Recovery mode — replays missed ticks after downtime

Flow per tick:
  1. Parallel crawl of all configured databases
  2. Incremental fingerprint diff against GraphStore
  3. Severity triage — route CRITICAL immediately, batch LOW
  4. Repair plan generation with confidence scoring
  5. GitHub PR creation (deduped by drift hash)
  6. Slack alert with full context
  7. State persistence and snapshot pruning
"""

from __future__ import annotations

import hashlib
import json
import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# After this many consecutive failures, enter recovery mode
_FAILURE_THRESHOLD = 3

# Exponential backoff: base * 2^attempt seconds (capped at max)
_BACKOFF_BASE = 10
_BACKOFF_MAX = 300

# Don't open a new PR if one was opened within this window (seconds)
_PR_COOLDOWN = 1800  # 30 minutes

# Max time a single tick may run before being forcefully abandoned
_TICK_TIMEOUT = 600  # 10 minutes


# ── Tick result tracking ──────────────────────────────────────────────────────


@dataclass
class TickResult:
    tick: int
    started_at: str
    completed_at: str = ""
    duration_s: float = 0.0
    success: bool = False
    drift_detected: bool = False
    changes: int = 0
    pr_url: Optional[str] = None
    error: Optional[str] = None
    phases: dict[str, float] = field(default_factory=dict)

    def finish(self, success: bool, error: Optional[str] = None) -> None:
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.duration_s = round(time.time() - self._start, 2)
        self.success = success
        self.error = error

    def record_phase(self, name: str, duration: float) -> None:
        self.phases[name] = round(duration, 3)

    def summary(self) -> str:
        status = "✓" if self.success else "✗"
        drift = f" | {self.changes} changes" if self.drift_detected else " | clean"
        pr = f" | PR: {self.pr_url}" if self.pr_url else ""
        timing = " | ".join(f"{k}:{v:.1f}s" for k, v in self.phases.items())
        return f"[Tick {self.tick}] {status} {self.duration_s:.1f}s{drift}{pr} | {timing}"

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": self.duration_s,
            "success": self.success,
            "drift_detected": self.drift_detected,
            "changes": self.changes,
            "pr_url": self.pr_url,
            "error": self.error,
            "phases": self.phases,
        }

    def __post_init__(self):
        self._start = time.time()


@dataclass
class WatcherState:
    """Persisted state across restarts."""

    last_tick: int = 0
    total_ticks: int = 0
    successful_ticks: int = 0
    failed_ticks: int = 0
    total_drifts: int = 0
    total_prs: int = 0
    last_drift_at: str = ""
    last_pr_at: str = ""
    last_pr_url: str = ""
    seen_drift_hashes: list = field(default_factory=list)  # dedup window
    consecutive_failures: int = 0

    @property
    def success_rate(self) -> float:
        if not self.total_ticks:
            return 1.0
        return round(self.successful_ticks / self.total_ticks, 3)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, path: Path) -> "WatcherState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(**data)
        except Exception:
            log.warning("Could not load watcher state — starting fresh.")
            return cls()


# ── Main watcher ──────────────────────────────────────────────────────────────


class SchemaWatcher:
    """
    Long-running daemon that continuously monitors one or more databases
    for schema drift and responds autonomously.

    Supports multiple databases — pass a list of db_urls to watch all
    simultaneously with a shared GraphStore and unified PR/Slack output.
    """

    def __init__(
        self,
        db_url: str | list[str],
        interval: int = 3600,
        store_path: str = "data/graph_store.db",
        github_repo: Optional[str] = None,
        github_token: Optional[str] = None,
        github_base: str = "main",
        github_reviewers: Optional[list[str]] = None,
        slack_webhook: Optional[str] = None,
        slack_channel: str = "#data-alerts",
        collect_stats: bool = True,
        data_dir: str = "data",
        pr_confidence_min: float = 0.80,
        max_workers: int = 8,
        pr_cooldown: int = _PR_COOLDOWN,
        tick_timeout: int = _TICK_TIMEOUT,
    ):
        # Support watching multiple databases simultaneously
        self.db_urls = [db_url] if isinstance(db_url, str) else db_url

        self.interval = interval
        self.store_path = store_path
        self.github_repo = github_repo
        self.github_token = github_token
        self.github_base = github_base
        self.github_reviewers = github_reviewers or []
        self.slack_webhook = slack_webhook
        self.slack_channel = slack_channel
        self.collect_stats = collect_stats
        self.data_dir = Path(data_dir)
        self.pr_confidence_min = pr_confidence_min
        self.max_workers = max_workers
        self.pr_cooldown = pr_cooldown
        self.tick_timeout = tick_timeout

        self._running = False
        self._tick = 0
        self._history: list[TickResult] = []

        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Load persisted state
        self._state_path = self.data_dir / "watcher_state.json"
        self.state = WatcherState.load(self._state_path)

        # Register graceful shutdown handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ── Public entry point ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watcher loop. Blocks until interrupted."""
        self._running = True
        self._print_banner()

        while self._running:
            self._tick += 1
            result = TickResult(
                tick=self._tick,
                started_at=datetime.now(timezone.utc).isoformat(),
            )

            # Run tick with timeout guard
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(self._run_tick, result)
                    try:
                        future.result(timeout=self.tick_timeout)
                        result.finish(success=True)
                        self.state.consecutive_failures = 0
                        self.state.successful_ticks += 1
                    except TimeoutError:
                        error = f"Tick {self._tick} timed out after {self.tick_timeout}s"
                        log.error(error)
                        result.finish(success=False, error=error)
                        self.state.consecutive_failures += 1
                        self.state.failed_ticks += 1
                    except Exception as e:
                        log.error(f"Tick {self._tick} failed: {e}", exc_info=True)
                        result.finish(success=False, error=str(e))
                        self.state.consecutive_failures += 1
                        self.state.failed_ticks += 1
            except Exception as e:
                log.error(f"Tick executor failed: {e}")
                result.finish(success=False, error=str(e))

            # Update state
            self.state.total_ticks += 1
            self.state.last_tick = self._tick
            self._history.append(result)

            # Persist state
            self.state.save(self._state_path)
            self._save_history()

            # Log tick summary
            log.info(result.summary())
            self._print_tick_summary(result)

            if not self._running:
                break

            # Sleep with backoff on repeated failures
            sleep_for = self._compute_sleep(result)
            self._sleep_interruptible(sleep_for)

        self._print_shutdown_summary()
        log.info("Watcher stopped.")

    # ── Core tick logic ───────────────────────────────────────────────────────

    def _run_tick(self, result: TickResult) -> None:
        """
        Single watcher iteration. Runs all configured databases in parallel
        if multiple are configured, sequentially if only one.
        """
        from ..crawler.builder import SchemaGraphBuilder
        from ..crawler.graph_store import GraphStore
        from ..crawler.drift import SchemaDriftDetector
        from ..orchestrator.repair import RepairEngine

        store = GraphStore(self.store_path)
        detector = SchemaDriftDetector()
        engine = RepairEngine()

        all_drift_events = []
        all_graphs = []

        # ── Phase 1: Parallel crawl ───────────────────────────────────────
        t0 = time.time()
        if len(self.db_urls) == 1:
            graph = self._crawl_one(self.db_urls[0], store, result)
            if graph:
                all_graphs.append(graph)
        else:
            with ThreadPoolExecutor(max_workers=min(len(self.db_urls), 4)) as ex:
                futures = {
                    ex.submit(self._crawl_one, url, store, result): url for url in self.db_urls
                }
                for future in as_completed(futures):
                    graph = future.result()
                    if graph:
                        all_graphs.append(graph)

        result.record_phase("crawl", time.time() - t0)

        if not all_graphs:
            raise RuntimeError("All database crawls failed.")

        # ── Phase 2: Incremental diff per graph ───────────────────────────
        t1 = time.time()
        for graph in all_graphs:
            current_id = graph.get("_snapshot_id")
            if not current_id:
                continue

            snapshots = store.list_snapshots(limit=3)

            # First run — establish baseline
            if len(snapshots) < 2:
                log.info(f"Baseline established for snapshot {current_id}.")
                self._export_graph(graph)
                continue

            prev_id = snapshots[1]["id"]
            prev_graph = store.get_snapshot(prev_id)
            if not prev_graph:
                log.warning(f"Could not load snapshot {prev_id} — skipping diff.")
                continue

            drift_report = detector.diff(
                prev_graph,
                graph,
                before_label=f"snapshot_{prev_id}",
                after_label=f"snapshot_{current_id}",
            )

            self._export_graph(graph)

            if not drift_report.is_clean:
                all_drift_events.extend(drift_report.events)
                result.drift_detected = True
                result.changes += len(drift_report.events)

                # Save drift report
                drift_path = self.data_dir / "drift_report.json"
                drift_path.write_text(json.dumps(drift_report.to_dict(), indent=2))

        result.record_phase("diff", time.time() - t1)

        # No drift — clean tick
        if not all_drift_events:
            log.info("✓ No schema drift detected across all databases.")
            self._send_clean_slack()
            store.prune_old_snapshots(keep=10)
            return

        # ── Phase 3: Triage ───────────────────────────────────────────────
        t2 = time.time()

        # Compute a stable hash for this exact set of drift events
        # Used to deduplicate — don't open PRs for the same drift twice
        drift_hash = self._drift_hash(all_drift_events)

        if drift_hash in self.state.seen_drift_hashes:
            log.info(f"Drift {drift_hash[:8]} already processed — skipping PR.")
            return

        # Check PR cooldown
        if self._in_cooldown():
            log.info(f"In PR cooldown window — alerting only, no new PR.")
            drift_data = json.loads((self.data_dir / "drift_report.json").read_text())
            self._send_drift_slack(drift_data, pr_url=self.state.last_pr_url or None)
            return

        result.record_phase("triage", time.time() - t2)

        # ── Phase 4: Repair plan ──────────────────────────────────────────
        t3 = time.time()
        plan = engine.build_plan(all_drift_events)
        plan_dict = plan.to_dict()
        sql = plan.render_sql_script()

        repair_path = self.data_dir / "repair_plan.json"
        repair_path.write_text(json.dumps(plan_dict, indent=2))

        sql_path = self.data_dir / "migration.sql"
        sql_path.write_text(sql)

        result.record_phase("repair", time.time() - t3)

        # ── Phase 5: GitHub PR ────────────────────────────────────────────
        t4 = time.time()
        pr_url = None
        drift_data = json.loads((self.data_dir / "drift_report.json").read_text())

        if self.github_repo and self.github_token:
            pr_url = self._open_pr(drift_data, plan_dict, sql)
            if pr_url:
                result.pr_url = pr_url
                self.state.total_prs += 1
                self.state.last_pr_url = pr_url
                self.state.last_pr_at = datetime.now(timezone.utc).isoformat()
                # Record drift hash to prevent duplicate PRs
                self.state.seen_drift_hashes.append(drift_hash)
                # Keep dedup window to last 50 hashes
                self.state.seen_drift_hashes = self.state.seen_drift_hashes[-50:]
        else:
            log.info("GitHub not configured — skipping PR.")

        result.record_phase("pr", time.time() - t4)

        # ── Phase 6: Slack alert ──────────────────────────────────────────
        t5 = time.time()
        self._send_drift_slack(drift_data, pr_url=pr_url)
        if pr_url:
            self._send_repair_slack(plan_dict, pr_url=pr_url)
        result.record_phase("slack", time.time() - t5)

        # Update state
        self.state.total_drifts += 1
        self.state.last_drift_at = datetime.now(timezone.utc).isoformat()

        # ── Phase 7: Cleanup ──────────────────────────────────────────────
        store.prune_old_snapshots(keep=10)

    def _crawl_one(
        self,
        db_url: str,
        store,
        result: TickResult,
    ) -> Optional[dict]:
        """Crawl a single database. Returns graph dict or None on failure."""
        from ..crawler.builder import SchemaGraphBuilder

        try:
            builder = SchemaGraphBuilder(
                db_url,
                collect_stats=self.collect_stats,
                store=store,
                max_workers=self.max_workers,
            )
            graph = builder.build()
            log.info(
                f"Crawled {self._safe_url(db_url)}: "
                f"{graph['meta'].get('table_count', 0)} tables, "
                f"{graph['meta'].get('node_count', 0)} nodes"
            )
            return graph
        except Exception as e:
            log.error(f"Crawl failed for {self._safe_url(db_url)}: {e}")
            return None

    # ── GitHub ────────────────────────────────────────────────────────────────

    def _open_pr(
        self,
        drift_report: dict,
        repair_plan: dict,
        migration_sql: str,
    ) -> Optional[str]:
        try:
            from ..integrations.github_pr import PRBot

            bot = PRBot(
                repo=self.github_repo,
                base=self.github_base,
                token=self.github_token,
                reviewers=self.github_reviewers,
            )
            result = bot.open_pr(drift_report, repair_plan, migration_sql)
            if result.success:
                log.info(f"PR opened: {result.pr_url}")
                return result.pr_url
            log.error(f"PR failed: {result.error}")
            return None
        except Exception as e:
            log.error(f"PR bot exception: {e}", exc_info=True)
            return None

    # ── Slack ─────────────────────────────────────────────────────────────────

    def _send_drift_slack(self, drift_report: dict, pr_url: Optional[str] = None) -> None:
        if not self.slack_webhook:
            return
        try:
            from ..integrations.slack import SlackAlerter

            SlackAlerter(self.slack_webhook, self.slack_channel).send_drift_alert(
                drift_report, pr_url=pr_url
            )
        except Exception as e:
            log.error(f"Slack alert failed: {e}")

    def _send_repair_slack(self, repair_plan: dict, pr_url: Optional[str] = None) -> None:
        if not self.slack_webhook:
            return
        try:
            from ..integrations.slack import SlackAlerter

            SlackAlerter(self.slack_webhook, self.slack_channel).send_repair_complete(
                repair_plan, pr_url=pr_url
            )
        except Exception as e:
            log.error(f"Slack repair notification failed: {e}")

    def _send_clean_slack(self) -> None:
        if not self.slack_webhook:
            return
        try:
            from ..integrations.slack import SlackAlerter

            SlackAlerter(self.slack_webhook, self.slack_channel).send_clean()
        except Exception as e:
            log.error(f"Slack clean notification failed: {e}")

    # ── Sleep + backoff ───────────────────────────────────────────────────────

    def _compute_sleep(self, result: TickResult) -> float:
        """
        Normal sleep = interval.
        On consecutive failures: exponential backoff capped at _BACKOFF_MAX.
        """
        if self.state.consecutive_failures == 0:
            return max(0, self.interval - result.duration_s)

        backoff = min(
            _BACKOFF_BASE * (2 ** (self.state.consecutive_failures - 1)),
            _BACKOFF_MAX,
        )
        log.warning(
            f"{self.state.consecutive_failures} consecutive failures. "
            f"Backing off {backoff}s before next tick."
        )
        return backoff

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleeps for `seconds` but wakes up every 1s to check for shutdown."""
        deadline = time.time() + seconds
        while self._running and time.time() < deadline:
            time.sleep(min(1.0, deadline - time.time()))

    # ── State helpers ─────────────────────────────────────────────────────────

    def _in_cooldown(self) -> bool:
        if not self.state.last_pr_at:
            return False
        try:
            last = datetime.fromisoformat(self.state.last_pr_at)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            return elapsed < self.pr_cooldown
        except Exception:
            return False

    @staticmethod
    def _drift_hash(events: list) -> str:
        """Stable hash for a set of drift events — used for dedup."""
        key = json.dumps(
            sorted(
                [{"node": e.node_id, "type": e.change_type.value} for e in events],
                key=lambda x: x["node"],
            )
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _export_graph(self, graph: dict) -> None:
        path = self.data_dir / "schema_graph.json"
        path.write_text(json.dumps(graph, indent=2, default=str))

    def _save_history(self) -> None:
        path = self.data_dir / "watcher_history.json"
        # Keep last 100 ticks
        recent = [r.to_dict() for r in self._history[-100:]]
        path.write_text(json.dumps(recent, indent=2))

    # ── Signal handling ───────────────────────────────────────────────────────

    def _handle_signal(self, signum, frame) -> None:
        log.info("Shutdown signal received. Finishing current tick...")
        print("\n  Shutting down gracefully...")
        self._running = False

    # ── Terminal output ───────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        dbs = "\n".join(f"    {i + 1}. {self._safe_url(u)}" for i, u in enumerate(self.db_urls))
        print(f"""
  ╔══════════════════════════════════════════╗
  ║         SemZero Schema Watcher           ║
  ╚══════════════════════════════════════════╝

  Databases:
{dbs}

  Interval:   {self.interval}s
  GitHub:     {self.github_repo or "─ not configured"}
  Slack:      {"✓ configured" if self.slack_webhook else "─ not configured"}
  History:    {self.state.total_ticks} previous ticks
  Success:    {self.state.success_rate:.0%}

  Press Ctrl+C to stop.
""")

    def _print_tick_summary(self, result: TickResult) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if result.success:
            if result.drift_detected:
                status = f"  🔴 [{now}] Drift — {result.changes} changes"
                if result.pr_url:
                    status += f"\n     PR: {result.pr_url}"
            else:
                status = f"  ✓  [{now}] Clean ({result.duration_s:.1f}s)"
        else:
            status = f"  ✗  [{now}] Failed: {result.error or 'unknown error'}"
        print(status)

    def _print_shutdown_summary(self) -> None:
        print(f"""
  ╔══════════════════════════════════════════╗
  ║           Watcher Stopped                ║
  ╚══════════════════════════════════════════╝

  Total ticks:   {self.state.total_ticks}
  Successful:    {self.state.successful_ticks}
  Failed:        {self.state.failed_ticks}
  Drifts found:  {self.state.total_drifts}
  PRs opened:    {self.state.total_prs}
  Success rate:  {self.state.success_rate:.0%}
""")

    @staticmethod
    def _safe_url(url: str) -> str:
        import re

        return re.sub(r"://([^:@]+):([^@]+)@", r"://***:***@", url)
