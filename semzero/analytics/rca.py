"""
rca.py — Root Cause Analysis Forensic Agent.

The most valuable feature for daily data engineering work.
Answers the question every engineer asks every morning:
"Why is this number wrong?"

Given a broken node (a metric, a table, a column, a pipeline),
the RCA agent:

  1. Walks the graph BACKWARD from the failure point
  2. Loads the drift history from GraphStore
  3. Correlates every upstream node with schema changes in the timeline
  4. Scores each candidate cause by structural proximity + change recency
  5. Returns a ranked list of probable root causes with confidence scores
  6. Generates a human-readable explanation chain

Example output:
  ══ Root Cause Analysis: orders.revenue ══

  Most likely cause (confidence 94%):
    users.email → users.email_address  [COLUMN_RENAMED]
    Changed at: 2026-03-15 02:14 UTC
    By snapshot: sqlite_20260315_021400

  Failure chain:
    users.email (renamed)
      → users (Table)
        → orders.user_id (FK reference broken)
          → orders (Table)
            → orders.revenue (YOUR BROKEN NODE)

  Other candidates:
    orders.status TYPE_CHANGED (42% confidence) — 3 hops away
    products.price STATS_DRIFTED (18% confidence) — 5 hops away

Usage:
  from semzero.analysis.rca import RCAAgent
  agent = RCAAgent(graph_json, store_path="data/graph_store.db")
  report = agent.investigate("orders.revenue")
  print(report.explain())

CLI:
  semzero trace --node orders.revenue
  semzero trace --node revenue_dashboard --since 24h
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import networkx as nx

log = logging.getLogger(__name__)

# ── Scoring weights ───────────────────────────────────────────────────────────

# How much each factor contributes to root cause confidence score
_W_PROXIMITY = 0.40  # How close the change is to the broken node (hops)
_W_RECENCY = 0.35  # How recently the change happened
_W_SEVERITY = 0.25  # How severe the change type is

# Severity scores for change types
_CHANGE_SEVERITY = {
    "COLUMN_REMOVED": 1.0,
    "TABLE_REMOVED": 1.0,
    "TYPE_CHANGED": 0.9,
    "COLUMN_RENAMED": 0.85,
    "TABLE_RENAMED": 0.8,
    "NULLABLE_CHANGED": 0.5,
    "STATS_DRIFTED": 0.4,
    "COLUMN_ADDED": 0.1,
    "TABLE_ADDED": 0.05,
}

# Recency decay — changes older than this get low recency scores
_RECENCY_WINDOW_HOURS = 72


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class CauseCandidate:
    """A single candidate root cause with scoring breakdown."""

    node_id: str
    change_type: str
    severity: str
    changed_at: str
    snapshot_label: str
    hops_from_failure: int
    path_to_failure: list[str]

    # Score components
    proximity_score: float = 0.0
    recency_score: float = 0.0
    severity_score: float = 0.0
    confidence: float = 0.0

    # Human-readable explanation
    before_state: Optional[dict] = None
    after_state: Optional[dict] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "change_type": self.change_type,
            "severity": self.severity,
            "changed_at": self.changed_at,
            "snapshot_label": self.snapshot_label,
            "hops_from_failure": self.hops_from_failure,
            "path_to_failure": self.path_to_failure,
            "confidence": round(self.confidence, 4),
            "score_breakdown": {
                "proximity": round(self.proximity_score, 4),
                "recency": round(self.recency_score, 4),
                "severity": round(self.severity_score, 4),
            },
            "detail": self.detail,
            "before": self.before_state,
            "after": self.after_state,
        }


@dataclass
class RCAReport:
    """Full root cause analysis report for a broken node."""

    broken_node: str
    investigated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    candidates: list[CauseCandidate] = field(default_factory=list)
    upstream_nodes: list[str] = field(default_factory=list)
    drift_events_checked: int = 0
    graph_snapshots_checked: int = 0
    error: Optional[str] = None

    @property
    def top_cause(self) -> Optional[CauseCandidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def has_cause(self) -> bool:
        return bool(self.candidates)

    def explain(self) -> str:
        """
        Returns a human-readable terminal explanation of the root cause.
        This is the output engineers actually read.
        """
        b = "\033[1m"
        r = "\033[0m"
        red = "\033[91m"
        yellow = "\033[93m"
        green = "\033[92m"
        dim = "\033[2m"
        blue = "\033[94m"

        lines = [
            f"\n{b}══ Root Cause Analysis: {self.broken_node} ══{r}",
            f"  Investigated: {self.investigated_at[:19]} UTC",
            f"  Upstream nodes scanned: {len(self.upstream_nodes)}",
            f"  Drift events checked:   {self.drift_events_checked}",
            f"  Snapshots checked:      {self.graph_snapshots_checked}",
        ]

        if self.error:
            lines.append(f"\n  {red}Error: {self.error}{r}")
            return "\n".join(lines)

        if not self.candidates:
            lines.append(
                f"\n  {green}✓ No schema changes found upstream of '{self.broken_node}' "
                f"in the drift history.{r}"
                "\n  The issue may be data quality, not schema drift."
                "\n  Check: source system, ETL job logs, upstream API changes."
            )
            return "\n".join(lines)

        top = self.top_cause
        conf_colour = red if top.confidence >= 0.8 else (yellow if top.confidence >= 0.5 else dim)

        lines.append(
            f"\n  {b}Most likely cause{r}  {conf_colour}({top.confidence:.0%} confidence){r}"
        )
        lines.append(f"  {red}▶{r}  {b}{top.node_id}{r}")
        lines.append(f"     Change:    {top.change_type}")
        lines.append(f"     Severity:  {top.severity}")
        lines.append(f"     When:      {top.changed_at[:19]} UTC")
        lines.append(f"     Snapshot:  {top.snapshot_label}")
        if top.detail:
            lines.append(f"     Detail:    {top.detail}")

        # Failure chain
        if top.path_to_failure:
            lines.append(f"\n  {b}Failure chain:{r}")
            for i, node in enumerate(top.path_to_failure):
                indent = "  " * (i + 1)
                arrow = "→ " if i > 0 else "✗ "
                is_origin = i == 0
                is_broken = node == self.broken_node
                colour = red if is_origin or is_broken else dim
                label = " ← ROOT CAUSE" if is_origin else (" ← BROKEN NODE" if is_broken else "")
                lines.append(f"  {indent}{colour}{arrow}{node}{r}{b}{label}{r}")

        # Other candidates
        others = self.candidates[1:4]
        if others:
            lines.append(f"\n  {b}Other candidates:{r}")
            for c in others:
                lines.append(
                    f"  {dim}  {c.node_id}  {c.change_type}  "
                    f"({c.confidence:.0%} confidence, {c.hops_from_failure} hops){r}"
                )

        # Recommended actions
        lines.append(f"\n  {b}Recommended actions:{r}")
        if top.change_type in ("COLUMN_RENAMED", "COLUMN_REMOVED"):
            lines.append(
                f"  {blue}1.{r} Run: semzero repair --drift data/drift_report.json --open-pr"
            )
            lines.append(f"  {blue}2.{r} Review PR before merging — check all downstream consumers")
        elif top.change_type == "TYPE_CHANGED":
            lines.append(f"  {blue}1.{r} Validate data compatibility before applying the CAST fix")
            lines.append(f"  {blue}2.{r} Run: semzero repair --drift data/drift_report.json")
        elif top.change_type == "STATS_DRIFTED":
            lines.append(f"  {blue}1.{r} Investigate source system for data quality issues")
            lines.append(f"  {blue}2.{r} Check upstream API or ETL job for silent changes")
        else:
            lines.append(
                f"  {blue}1.{r} Run: semzero repair --drift data/drift_report.json --open-pr"
            )

        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "broken_node": self.broken_node,
            "investigated_at": self.investigated_at,
            "has_cause": self.has_cause,
            "top_cause": self.top_cause.to_dict() if self.top_cause else None,
            "all_candidates": [c.to_dict() for c in self.candidates],
            "upstream_nodes_scanned": len(self.upstream_nodes),
            "drift_events_checked": self.drift_events_checked,
            "graph_snapshots_checked": self.graph_snapshots_checked,
            "error": self.error,
        }

    def save(self, path: str = "data/rca_report.json") -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))
        log.info(f"RCA report saved to {p}")
        return p


# ── RCA Agent ─────────────────────────────────────────────────────────────────


class RCAAgent:
    """
    Forensic root cause analysis agent.

    Uses the schema graph (for structural traversal) and the GraphStore
    drift history (for change timeline) to identify what upstream schema
    change most likely caused a downstream failure.
    """

    def __init__(
        self,
        graph_json: dict,
        store_path: str = "data/graph_store.db",
        lookback_hours: int = 72,
    ):
        self.graph_json = graph_json
        self.store_path = store_path
        self.lookback_hours = lookback_hours

        # Build two traversal graphs:
        #
        # G_forward: original direction — used to get path from cause to failure
        #
        # G_rca: mixed-direction graph for backward RCA traversal
        #   PART_OF edges REVERSED:    col→table becomes table→col
        #                              (so from a table we can reach its columns)
        #   REFERENCES edges ORIGINAL: orders.user_id→users.id stays
        #                              (so from orders we can reach users)
        #
        # This lets us traverse: orders → orders.user_id → users.id → users
        #                                                            → users.contact_email
        self.G_forward = nx.DiGraph()
        self.G_rca = nx.DiGraph()

        for node in graph_json.get("nodes", []):
            self.G_forward.add_node(node["id"], **node)
            self.G_rca.add_node(node["id"], **node)

        for edge in graph_json.get("edges", []):
            src = edge["source"]
            tgt = edge["target"]
            relation = edge.get("relation", "")
            weight = edge.get("weight", 1.0)

            # Forward graph — original direction
            self.G_forward.add_edge(src, tgt, relation=relation, weight=weight)

            # RCA graph — PART_OF reversed, REFERENCES kept original
            if relation == "PART_OF":
                # Reverse: table → column (so we can reach columns from tables)
                self.G_rca.add_edge(tgt, src, relation=relation, weight=weight)
            else:
                # REFERENCES and others: keep original direction
                # orders.user_id → users.id means users is upstream of orders
                self.G_rca.add_edge(src, tgt, relation=relation, weight=weight)

    # ── Main entry point ──────────────────────────────────────────────────────

    def investigate(
        self,
        broken_node_id: str,
        since: Optional[datetime] = None,
    ) -> RCAReport:
        """
        Investigate why a node is broken by walking the graph backward
        and correlating with drift history.

        Args:
            broken_node_id: The node that is broken (table, column, metric)
            since:          Only consider drift events after this time.
                            Defaults to lookback_hours before now.
        """
        report = RCAReport(broken_node=broken_node_id)

        # Validate node exists
        if broken_node_id not in self.G_rca:
            report.error = f"Node '{broken_node_id}' not found in schema graph."
            log.error(report.error)
            return report

        # Set time window
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)

        log.info(
            f"Investigating '{broken_node_id}' — "
            f"looking back to {since.strftime('%Y-%m-%d %H:%M')} UTC"
        )

        # ── Step 1: Find all upstream nodes via backward traversal ────────
        upstream = self._get_upstream_nodes(broken_node_id)
        report.upstream_nodes = list(upstream.keys())
        log.info(f"Found {len(upstream)} upstream nodes to check.")

        # ── Step 2: Load drift history from GraphStore ────────────────────
        drift_timeline = self._load_drift_timeline(since)
        report.graph_snapshots_checked = len(drift_timeline)
        report.drift_events_checked = sum(len(events) for events in drift_timeline.values())
        log.info(
            f"Loaded {report.drift_events_checked} drift events "
            f"from {report.graph_snapshots_checked} snapshots."
        )

        if not drift_timeline:
            log.info("No drift history found in the lookback window.")
            return report

        # ── Step 3: Correlate upstream nodes with drift events ────────────
        candidates = self._correlate(broken_node_id, upstream, drift_timeline, since)

        # ── Step 4: Score and rank candidates ────────────────────────────
        for c in candidates:
            c.confidence = (
                _W_PROXIMITY * c.proximity_score
                + _W_RECENCY * c.recency_score
                + _W_SEVERITY * c.severity_score
            )

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        report.candidates = candidates

        if candidates:
            log.info(
                f"RCA complete: {len(candidates)} candidates. "
                f"Top cause: {candidates[0].node_id} "
                f"({candidates[0].confidence:.0%} confidence)"
            )
        else:
            log.info("RCA complete: no upstream drift events found.")

        return report

    # ── Graph traversal ───────────────────────────────────────────────────────

    def _get_upstream_nodes(self, node_id: str) -> dict[str, int]:
        """
        Returns all upstream nodes with their hop distance from the broken node.
        Uses the reversed graph so we traverse backward through the dependency chain.
        """
        try:
            lengths = nx.single_source_shortest_path_length(self.G_rca, node_id)
            return {n: d for n, d in lengths.items() if n != node_id}
        except nx.NetworkXError:
            return {}

    def _get_path_to_failure(self, from_node: str, broken_node: str) -> list[str]:
        """
        Returns the shortest path from a root cause candidate
        to the broken node in the FORWARD graph direction.
        """
        try:
            return nx.shortest_path(self.G_forward, from_node, broken_node)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return [from_node, broken_node]

    # ── Drift history ─────────────────────────────────────────────────────────

    def _load_drift_timeline(self, since: datetime) -> dict[str, list[dict]]:
        """
        Loads all drift reports from the GraphStore drift history.
        Returns dict of {snapshot_label: [drift_events]}.

        Falls back to reading data/drift_report.json if GraphStore
        doesn't have history yet.
        """
        timeline: dict[str, list[dict]] = {}

        # Try GraphStore first
        try:
            from ..crawler.graph_store import GraphStore

            store = GraphStore(self.store_path)
            snapshots = store.list_snapshots(limit=50)

            for snap in snapshots:
                snap_time_str = snap.get("created_at", "")
                if not snap_time_str:
                    continue
                try:
                    snap_time = datetime.fromisoformat(snap_time_str)
                    if snap_time.tzinfo is None:
                        snap_time = snap_time.replace(tzinfo=timezone.utc)
                    if snap_time < since:
                        continue
                except ValueError:
                    continue

                # Load actual snapshot to get drift events
                full_snap = store.get_snapshot(snap["id"])
                if full_snap:
                    timeline[snap["label"]] = self._extract_events_from_snapshot(
                        full_snap, snap["label"]
                    )

        except Exception as e:
            log.warning(f"Could not load GraphStore history: {e}")

        # Always read drift_report.json if it exists and has events
        # This is the primary source during development and for the watcher
        drift_path = Path("data/drift_report.json")
        if drift_path.exists():
            try:
                data = json.loads(drift_path.read_text())
                events = data.get("events", [])
                label = data.get("after_snapshot", "latest_drift")
                if events:
                    timeline[label] = events
                    log.info(f"Loaded {len(events)} events from drift_report.json")
            except Exception as e:
                log.warning(f"Could not read drift_report.json: {e}")

        return timeline

    def _extract_events_from_snapshot(self, snapshot: dict, label: str) -> list[dict]:
        """
        Extracts drift events embedded in snapshot metadata.
        The watcher saves drift_report.json alongside each tick.
        """
        # Check if snapshot has embedded drift info
        meta = snapshot.get("meta", {})
        if "drift_events" in meta:
            return meta["drift_events"]
        return []

    # ── Correlation engine ────────────────────────────────────────────────────

    def _correlate(
        self,
        broken_node: str,
        upstream: dict[str, int],
        drift_timeline: dict[str, list[dict]],
        since: datetime,
    ) -> list[CauseCandidate]:
        """
        For every drift event in history, check if the changed node
        is upstream of the broken node. If so, score it as a candidate.
        """
        candidates: list[CauseCandidate] = []
        seen: set[str] = set()  # deduplicate same node + change type

        now = datetime.now(timezone.utc)

        for snapshot_label, events in drift_timeline.items():
            for event in events:
                changed_node = event.get("node_id", "")
                change_type = event.get("change_type", "")
                dedup_key = f"{changed_node}:{change_type}"

                if dedup_key in seen:
                    continue

                # Is this changed node upstream of the broken node?
                # Check 1: direct match — the node itself is upstream
                direct_match = changed_node in upstream

                # Check 2: table match — the changed node's parent table
                # is upstream. This catches sibling columns:
                # e.g. users.contact_email changed, and users.id is
                # upstream of orders via FK — so users.contact_email
                # is still a valid root cause candidate.
                parent_table = changed_node.split(".")[0] if "." in changed_node else changed_node
                table_match = any(
                    n == parent_table or n.startswith(f"{parent_table}.") for n in upstream
                )

                if not direct_match and not table_match:
                    continue

                seen.add(dedup_key)

                # Use direct hop distance if available, else estimate via table
                hops = upstream.get(changed_node)
                if hops is None:
                    # Estimate: find the closest upstream node in the same table
                    table_hops = [
                        d
                        for n, d in upstream.items()
                        if n == parent_table or n.startswith(f"{parent_table}.")
                    ]
                    hops = min(table_hops) + 1 if table_hops else 2

                # Get the path from this candidate to the broken node
                path = self._get_path_to_failure(changed_node, broken_node)

                # ── Proximity score ───────────────────────────────────────
                # 1.0 at 1 hop, decays with distance
                proximity = max(0.0, 1.0 - ((hops - 1) * 0.15))

                # ── Recency score ─────────────────────────────────────────
                # 1.0 for very recent, decays over lookback window
                changed_at_str = event.get("changed_at", "")
                recency = 0.5  # neutral default
                changed_at = snapshot_label  # fallback

                if changed_at_str:
                    try:
                        ct = datetime.fromisoformat(changed_at_str)
                        if ct.tzinfo is None:
                            ct = ct.replace(tzinfo=timezone.utc)
                        changed_at = changed_at_str
                        age_hours = (now - ct).total_seconds() / 3600
                        recency = max(0.0, 1.0 - (age_hours / _RECENCY_WINDOW_HOURS))
                    except ValueError:
                        pass

                # ── Severity score ────────────────────────────────────────
                sev_score = _CHANGE_SEVERITY.get(change_type, 0.3)

                candidate = CauseCandidate(
                    node_id=changed_node,
                    change_type=change_type,
                    severity=event.get("severity", "UNKNOWN"),
                    changed_at=changed_at,
                    snapshot_label=snapshot_label,
                    hops_from_failure=hops,
                    path_to_failure=path,
                    proximity_score=proximity,
                    recency_score=recency,
                    severity_score=sev_score,
                    before_state=event.get("before"),
                    after_state=event.get("after"),
                    detail=event.get("detail", ""),
                )
                candidates.append(candidate)

        return candidates
