from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_C = {
    "red": "\033[91m",
    "yellow": "\033[93m",
    "green": "\033[92m",
    "blue": "\033[94m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _sev_colour(severity: str) -> str:
    return {
        "CRITICAL": _C["red"],
        "HIGH": _C["yellow"],
        "MEDIUM": _C["blue"],
        "LOW": _C["dim"],
    }.get(severity, "")


class TerminalReporter:
    def print_drift_report(self, report: dict) -> None:
        b, r = _C["bold"], _C["reset"]
        print(f"\n{b}══ SemZero Drift Report ══{r}")
        print(f"  Detected:  {report.get('detected_at', 'N/A')}")
        s = report.get("summary", {})
        total = s.get("total_changes", 0)
        if total == 0:
            print(f"  {_C['green']}✓ No schema drift detected.{r}\n")
            return
        print(f"  Changes:   {b}{total}{r} total")
        by_sev = s.get("by_severity", {})
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            count = by_sev.get(sev, 0)
            if count:
                print(f"    {_sev_colour(sev)}{sev}{r}: {count}")
        print(f"\n{b}── Events ─────────────────{r}")
        for event in report.get("events", []):
            sev = event["severity"]
            colour = _sev_colour(sev)
            print(f"  {colour}[{sev}]{r} {event['change_type']}: {event['detail']}")
        print()

    def print_blast_radius(self, report: dict) -> None:
        b, r = _C["bold"], _C["reset"]
        s = report.get("summary", {})
        print(f"\n{b}══ Blast Radius: {s.get('changed_node')} ══{r}")
        print(f"  Impacted nodes: {b}{s.get('total_impacted', 0)}{r}")
        print(f"  Max depth:      {s.get('max_depth', 0)} hops")
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            count = s.get(sev.lower(), 0)
            if count:
                print(f"    {_sev_colour(sev)}{sev}{r}: {count}")
        nodes = report.get("impacted_nodes", [])
        critical = [n for n in nodes if n["severity_label"] == "CRITICAL"]
        if critical:
            print(f"\n{b}── Critical impacts ───────{r}")
            for n in critical[:10]:
                print(f"  {_C['red']}●{_C['reset']} {n['node_id']} (depth {n['depth']})")
        print()

    def print_repair_plan(self, plan: dict) -> None:
        b, r = _C["bold"], _C["reset"]
        s = plan.get("summary", {})
        print(f"\n{b}══ Repair Plan ══{r}")
        print(f"  Actions:        {b}{s.get('total_actions', 0)}{r}")
        print(f"  Auto-execute:   {_C['green']}{s.get('auto_executable', 0)}{r}")
        print(f"  Needs approval: {_C['yellow']}{s.get('needs_approval', 0)}{r}")
        for action in plan.get("actions", []):
            needs_ok = action["approval_required"]
            tag = f"{_C['yellow']}[APPROVE]{r}" if needs_ok else f"{_C['green']}[AUTO]{r}"
            sev_colour = _sev_colour(action["severity"])
            print(f"  {tag} {sev_colour}{action['strategy']}{r} → {action['node_id']}")
            if action.get("notes"):
                print(f"      {_C['dim']}{action['notes']}{r}")
        print()


class HTMLReporter:
    """
    Generates a self-contained HTML report with:
    - Interactive schema graph (vis.js Network)
    - Drift event table
    - Repair plan with syntax-highlighted SQL
    """

    def generate(
        self,
        graph_json: dict,
        drift_report: Optional[dict] = None,
        blast_report: Optional[dict] = None,
        repair_plan: Optional[dict] = None,
        output_path: str = "data/semzero_report.html",
    ) -> Path:
        html = self._render(graph_json, drift_report, blast_report, repair_plan)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        log.info(f"HTML report saved to {path}")
        return path

    def _render(self, graph_json, drift_report, blast_report, repair_plan) -> str:
        nodes_js = self._build_vis_nodes(graph_json, blast_report)
        edges_js = self._build_vis_edges(graph_json)
        drift_html = self._render_drift(drift_report)
        repair_html = self._render_repair(repair_plan)
        generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        meta = graph_json.get("meta", {})

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SemZero — Schema Intelligence Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css">
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2d3144;
    --text: #e2e8f0; --dim: #64748b; --accent: #6366f1;
    --red: #ef4444; --yellow: #f59e0b; --blue: #3b82f6; --green: #22c55e;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Inter', system-ui, sans-serif; font-size: 14px; }}
  header {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 20px 32px; display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }}
  header h1 span {{ color: var(--accent); }}
  .meta {{ color: var(--dim); font-size: 12px; }}
  .layout {{ display: grid; grid-template-columns: 1fr 380px; height: calc(100vh - 65px); }}
  #graph {{ width: 100%; height: 100%; background: var(--bg); }}
  .sidebar {{ background: var(--surface); border-left: 1px solid var(--border); overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 20px; }}
  .card {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .card h3 {{ font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--dim); margin-bottom: 12px; }}
  .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .stat {{ background: var(--surface); border-radius: 6px; padding: 10px; text-align: center; }}
  .stat-val {{ font-size: 22px; font-weight: 700; }}
  .stat-label {{ font-size: 11px; color: var(--dim); margin-top: 2px; }}
  .CRITICAL {{ color: var(--red); }}
  .HIGH {{ color: var(--yellow); }}
  .MEDIUM {{ color: var(--blue); }}
  .LOW {{ color: var(--dim); }}
  .event-list {{ display: flex; flex-direction: column; gap: 6px; max-height: 280px; overflow-y: auto; }}
  .event {{ background: var(--surface); border-left: 3px solid; border-radius: 0 4px 4px 0; padding: 8px 10px; font-size: 12px; }}
  .event.CRITICAL {{ border-color: var(--red); }}
  .event.HIGH {{ border-color: var(--yellow); }}
  .event.MEDIUM {{ border-color: var(--blue); }}
  .event.LOW {{ border-color: var(--dim); }}
  .event-type {{ font-weight: 600; font-size: 11px; }}
  .event-detail {{ color: var(--dim); margin-top: 2px; line-height: 1.4; }}
  .repair-list {{ display: flex; flex-direction: column; gap: 6px; max-height: 320px; overflow-y: auto; }}
  .repair {{ background: var(--surface); border-radius: 4px; padding: 8px 10px; font-size: 12px; }}
  .repair-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
  .badge {{ font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 3px; text-transform: uppercase; }}
  .badge.auto {{ background: rgba(34,197,94,0.15); color: var(--green); }}
  .badge.approve {{ background: rgba(245,158,11,0.15); color: var(--yellow); }}
  .node-id {{ font-family: monospace; font-size: 11px; color: var(--accent); }}
  pre {{ background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 10px; font-size: 11px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; color: var(--green); margin-top: 6px; }}
  .clean {{ color: var(--green); text-align: center; padding: 12px; }}
  ::-webkit-scrollbar {{ width: 4px; }}
  ::-webkit-scrollbar-track {{ background: transparent; }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}
</style>
</head>
<body>
<header>
  <h1>Sem<span>Zero</span> <span style="color:var(--dim);font-weight:400;font-size:14px;">Schema Intelligence Report</span></h1>
  <div class="meta">
    {meta.get("dialect", "").upper()} &nbsp;·&nbsp;
    {meta.get("table_count", 0)} tables &nbsp;·&nbsp;
    {meta.get("node_count", 0)} nodes &nbsp;·&nbsp;
    Generated {generated}
  </div>
</header>
<div class="layout">
  <div id="graph"></div>
  <div class="sidebar">
    <div class="card">
      <h3>Schema Overview</h3>
      <div class="stat-grid">
        <div class="stat"><div class="stat-val">{meta.get("table_count", 0)}</div><div class="stat-label">Tables</div></div>
        <div class="stat"><div class="stat-val">{meta.get("node_count", 0)}</div><div class="stat-label">Nodes</div></div>
        <div class="stat"><div class="stat-val">{meta.get("edge_count", 0)}</div><div class="stat-label">Edges</div></div>
        <div class="stat"><div class="stat-val">{meta.get("version", 1)}</div><div class="stat-label">Version</div></div>
      </div>
    </div>
    {drift_html}
    {repair_html}
  </div>
</div>
<script>
const nodes = new vis.DataSet({json.dumps(nodes_js)});
const edges = new vis.DataSet({json.dumps(edges_js)});
const container = document.getElementById('graph');
const options = {{
  nodes: {{
    shape: 'dot',
    borderWidth: 1.5,
    font: {{ color: '#e2e8f0', size: 11, face: 'Inter, system-ui' }},
    shadow: true,
  }},
  edges: {{
    width: 1,
    color: {{ color: '#2d3144', highlight: '#6366f1' }},
    smooth: {{ type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.4 }},
    arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
  }},
  physics: {{
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {{ gravitationalConstant: -80, springLength: 80, damping: 0.5 }},
    stabilization: {{ iterations: 150 }},
  }},
  interaction: {{ hover: true, tooltipDelay: 100, navigationButtons: true }},
}};
new vis.Network(container, {{ nodes, edges }}, options);
</script>
</body>
</html>"""

    def _build_vis_nodes(self, graph_json: dict, blast_report: Optional[dict]) -> list:
        critical_ids = set()
        if blast_report:
            for n in blast_report.get("impacted_nodes", []):
                if n["severity_label"] == "CRITICAL":
                    critical_ids.add(n["node_id"])

        vis_nodes = []
        for node in graph_json["nodes"]:
            nid = node["id"]
            label = node.get("label", "")
            is_table = label == "Table"

            colour = "#6366f1" if is_table else "#334155"
            size = 18 if is_table else 8
            if nid in critical_ids:
                colour = "#ef4444"
                size = 14

            title = f"<b>{nid}</b><br>Type: {label}"
            if not is_table:
                title += f"<br>dtype: {node.get('dtype', '?')}"
                title += f"<br>null_rate: {node.get('null_rate', 0):.1%}"
                title += f"<br>cardinality: {node.get('cardinality', 0):.3f}"
                if node.get("is_primary_key"):
                    title += "<br>🔑 Primary Key"

            vis_nodes.append(
                {
                    "id": nid,
                    "label": nid.split(".")[-1] if not is_table else nid,
                    "color": colour,
                    "size": size,
                    "title": title,
                    "group": label,
                }
            )
        return vis_nodes

    def _build_vis_edges(self, graph_json: dict) -> list:
        colour_map = {
            "PART_OF": "#1e2533",
            "REFERENCES": "#4f46e5",
        }
        return [
            {
                "from": e["source"],
                "to": e["target"],
                "color": colour_map.get(e.get("relation", ""), "#2d3144"),
                "width": 2 if e.get("relation") == "REFERENCES" else 0.8,
                "title": e.get("relation", ""),
            }
            for e in graph_json["edges"]
        ]

    def _render_drift(self, drift_report: Optional[dict]) -> str:
        if not drift_report:
            return ""
        s = drift_report.get("summary", {})
        total = s.get("total_changes", 0)
        if total == 0:
            return """
<div class="card">
  <h3>Drift Detection</h3>
  <div class="clean">✓ No schema drift detected</div>
</div>"""

        by_sev = s.get("by_severity", {})
        stat_html = "".join(
            f'<div class="stat"><div class="stat-val {sev}">{by_sev.get(sev, 0)}</div>'
            f'<div class="stat-label">{sev}</div></div>'
            for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        )
        events_html = "".join(
            f'<div class="event {e["severity"]}">'
            f'<div class="event-type {e["severity"]}">{e["change_type"]}</div>'
            f'<div class="event-detail">{e["detail"]}</div></div>'
            for e in drift_report.get("events", [])
        )
        return f"""
<div class="card">
  <h3>Drift Detection — {total} Changes</h3>
  <div class="stat-grid" style="margin-bottom:12px">{stat_html}</div>
  <div class="event-list">{events_html}</div>
</div>"""

    def _render_repair(self, repair_plan: Optional[dict]) -> str:
        if not repair_plan:
            return ""
        s = repair_plan.get("summary", {})
        actions = repair_plan.get("actions", [])

        items_html = ""
        for a in actions:
            badge_cls = "approve" if a["approval_required"] else "auto"
            badge_text = "Needs Approval" if a["approval_required"] else "Auto"
            sql_block = f"<pre>{a['sql']}</pre>" if a.get("sql") else ""
            items_html += f"""
<div class="repair">
  <div class="repair-header">
    <span class="node-id">{a["node_id"]}</span>
    <span class="badge {badge_cls}">{badge_text}</span>
  </div>
  <div style="font-size:11px;color:var(--dim)">{a["strategy"]} · {a["severity"]}</div>
  {sql_block}
</div>"""

        return f"""
<div class="card">
  <h3>Repair Plan — {s.get("total_actions", 0)} Actions</h3>
  <div class="stat-grid" style="margin-bottom:12px">
    <div class="stat"><div class="stat-val" style="color:var(--green)">{s.get("auto_executable", 0)}</div><div class="stat-label">Auto</div></div>
    <div class="stat"><div class="stat-val" style="color:var(--yellow)">{s.get("needs_approval", 0)}</div><div class="stat-label">Review</div></div>
  </div>
  <div class="repair-list">{items_html}</div>
</div>"""
