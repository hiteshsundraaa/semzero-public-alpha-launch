"""
test_full_loop.py — SemZero Full Integration Test.

Correct workflow order:

  1. CRAWL         — snapshot clean schema
  2. CHAOS         — score fragility proactively
  3. WIND TUNNEL   — validate migration on clone BEFORE touching live DB
  4. CHANGE GATE   — classify change, cross-ref Chaos
  5. APPLY         — apply migration to live DB (only after validation)
  6. RE-CRAWL      — detect actual drift
  7. REPAIR        — generate fix SQL
  8. RESTORE       — revert DB

This is the correct order. Wind Tunnel validates BEFORE production is touched.
That is the entire value proposition.

Usage:
  docker compose exec semzero python scripts/test_full_loop.py
  docker compose exec semzero python scripts/test_full_loop.py --mode validation-apply
  docker compose exec semzero python scripts/test_full_loop.py --skip-chaos
  docker compose exec semzero python scripts/test_full_loop.py --mutations 20
"""

from __future__ import annotations

import argparse, json, logging, os, re, sys, time
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s — %(message)s")

R = "\033[0m"
B = "\033[1m"
D = "\033[2m"
RD = "\033[91m"
YL = "\033[93m"
GR = "\033[92m"
CY = "\033[96m"


def hdr(n, total, title):
    print(f"\n{B}{CY}{'━' * 58}{R}\n{B}{CY}  Step {n}/{total}: {title}{R}\n{B}{CY}{'━' * 58}{R}\n")


def ok(m):
    print(f"  {GR}✓{R} {m}")


def warn(m):
    print(f"  {YL}⚠{R} {m}")


def info(m):
    print(f"  {D}{m}{R}")


# ── Config ────────────────────────────────────────────────────────────────────
DB_URL = os.environ.get("SEMZERO_DB_URL", "postgresql://semzero:semzero@postgres/demo")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

GRAPH_PATH = DATA_DIR / "schema_graph.json"
STORE_PATH = DATA_DIR / "graph_store.db"
DRIFT_PATH = DATA_DIR / "drift_report.json"
REPAIR_PATH = DATA_DIR / "repair_plan.json"
GATE_PATH = DATA_DIR / "gate_result.json"
CHAOS_PATH = DATA_DIR / "chaos_report.json"
HTML_PATH = DATA_DIR / "chaos_report.html"
RECEIPT_PATH = DATA_DIR / "simulation_receipt.json"

BREAK_TABLE = "orders"
BREAK_COL = "user_id"
BREAK_NEW = "account_id"


# ── DB helpers ────────────────────────────────────────────────────────────────


def get_engine():
    from sqlalchemy import create_engine

    return create_engine(DB_URL, pool_pre_ping=True)


def col_exists(engine, table, col):
    from sqlalchemy import text

    with engine.connect() as c:
        r = c.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c AND table_schema='public'"
            ),
            {"t": table, "c": col},
        ).fetchone()
    return r is not None


def rename_col(engine, table, old, new):
    from sqlalchemy import text

    with engine.begin() as c:
        c.execute(text(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"'))


def crawl(label=""):
    from semzero.crawler.builder import SchemaGraphBuilder

    b = SchemaGraphBuilder(DB_URL, store_path=str(STORE_PATH), max_workers=4)
    g = b.build(label=label)
    b.save(str(GRAPH_PATH))
    return g


def latest_two_snapshots():
    from semzero.crawler.graph_store import GraphStore

    store = GraphStore(str(STORE_PATH))
    snaps = store.list_snapshots(limit=5)
    if len(snaps) < 2:
        raise RuntimeError(f"Only {len(snaps)} snapshot(s)")
    return store.get_snapshot(snaps[1]["id"]), store.get_snapshot(snaps[0]["id"])


# ── Migration SQL ─────────────────────────────────────────────────────────────


def build_migration_sql(table: str, old_col: str, new_col: str) -> str:
    return f'ALTER TABLE "{table}" RENAME COLUMN "{old_col}" TO "{new_col}";'


def drift_to_sql(drift_dict: dict) -> str:
    """Convert drift events to SQL, deduplicating rename artifacts."""
    events = drift_dict.get("events", [])
    rename_targets: set[tuple[str, str]] = set()
    for ev in events:
        if ev.get("change_type") == "COLUMN_RENAMED":
            nid, detail = ev.get("node_id", ""), ev.get("detail", "")
            if "." in nid:
                tbl = nid.split(".")[0]
                m = re.search(r"renamed to ['\"]?[\w.]*\.(\w+)['\"]?", detail, re.I)
                if m:
                    rename_targets.add((tbl, m.group(1)))

    lines = []
    for ev in events:
        ct, nid = ev.get("change_type", ""), ev.get("node_id", "")
        if "." not in nid:
            continue
        tbl, col = nid.split(".", 1)
        after = ev.get("after") or {}
        detail = ev.get("detail", "")

        if ct == "COLUMN_RENAMED":
            m = re.search(r"renamed to ['\"]?[\w.]*\.(\w+)['\"]?", detail, re.I)
            if m:
                lines.append(f'ALTER TABLE "{tbl}" RENAME COLUMN "{col}" TO "{m.group(1)}";')
        elif ct == "COLUMN_ADDED":
            if (tbl, col) in rename_targets:
                continue
            dtype = after.get("dtype", "VARCHAR")
            lines.append(f'ALTER TABLE "{tbl}" ADD COLUMN "{col}" {dtype} NULL;')
        elif ct == "COLUMN_REMOVED":
            lines.append(f'ALTER TABLE "{tbl}" DROP COLUMN "{col}";')
        elif ct == "TYPE_CHANGED":
            new_type = after.get("dtype", "VARCHAR")
            lines.append(f'ALTER TABLE "{tbl}" ALTER COLUMN "{col}" TYPE {new_type};')
        elif ct == "NULLABLE_CHANGED":
            kw = "DROP NOT NULL" if after.get("nullable", True) else "SET NOT NULL"
            lines.append(f'ALTER TABLE "{tbl}" ALTER COLUMN "{col}" {kw};')
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-chaos", action="store_true")
    parser.add_argument("--skip-gate", action="store_true")
    parser.add_argument("--skip-tunnel", action="store_true")
    parser.add_argument("--mutations", type=int, default=50)
    parser.add_argument(
        "--mode",
        choices=["safe", "validation-apply"],
        default="safe",
        help="safe = never apply live DB changes after a BLOCK; validation-apply = controlled apply/revert for harness proofing",
    )
    args = parser.parse_args()

    TOTAL = 8
    t_start = time.time()

    # Find which FK column actually exists
    engine = get_engine()
    break_col = BREAK_COL
    break_new = BREAK_NEW
    for candidate in ["user_id", "customer_id", "buyer_id"]:
        if col_exists(engine, BREAK_TABLE, candidate):
            break_col = candidate
            break_new = "account_id" if candidate != "account_id" else "account_uuid"
            break
    engine.dispose()

    # The intended migration SQL — used for Wind Tunnel BEFORE live apply
    migration_sql = build_migration_sql(BREAK_TABLE, break_col, break_new)

    print(f"""
{B}
  ╔══════════════════════════════════════════════════════╗
  ║   SemZero — Full Integration Loop                   ║
  ║   Chaos → Wind Tunnel → Gate → Apply? → Repair      ║
  ╚══════════════════════════════════════════════════════╝
{R}
  Database:        {DB_URL}
  Intended change: {BREAK_TABLE}.{break_col} → {break_new}
  Mutations:       {args.mutations}
  
  {D}Note: Wind Tunnel runs BEFORE the live migration.
  Safe mode halts before live apply on BLOCK. validation-apply performs a controlled apply/revert for harness proofing. All findings should be treated as observed evidence only when replay or live validation actually runs.{R}
""")

    chaos_score = 0
    chaos_grade = "?"
    chaos_crits = []
    gate_verdict = "UNKNOWN"
    receipt = None
    applied = False

    # ── Step 1: Crawl current clean schema ────────────────────────────────────
    hdr(1, TOTAL, "Schema Crawl — Current State")
    try:
        graph = crawl("before_mutation")
        m = graph.get("meta", {})
        ok(
            f"{m.get('table_count', 0)} tables · {m.get('node_count', 0)} nodes · "
            f"{m.get('edge_count', 0)} FK edges"
        )
        info(f"Snapshot ID: {graph.get('_snapshot_id', '?')}")
    except Exception as e:
        print(f"  {RD}✗ Crawl failed: {e}{R}")
        sys.exit(1)

    # ── Step 2: Chaos Mode ────────────────────────────────────────────────────
    hdr(2, TOTAL, "Chaos Mode — Proactive Fragility Analysis")
    if args.skip_chaos:
        warn("Skipped (--skip-chaos)")
    else:
        info(f"{args.mutations} mutations · graph-only · parallel")
        try:
            from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine
            from semzero.chaos.chaos_reporter import ChaosHTMLReporter

            cfg = ChaosConfig(
                mutation_count=args.mutations,
                run_dbt_tests=False,
                parallel_mutations=True,
                max_workers=4,
                generate_html=True,
                data_dir=str(DATA_DIR),
            )
            report = ChaosEngine(cfg).run(graph_json=graph)
            report.save(str(CHAOS_PATH))
            ChaosHTMLReporter().generate(report=report, output_path=str(HTML_PATH))

            s = report.summary()
            chaos_score = s["fragility_score"]
            chaos_grade = s["fragility_grade"]
            chaos_crits = [p.model_name for p in report.critical_pipelines[:5]]

            sc = GR if chaos_score >= 80 else YL if chaos_score >= 60 else RD
            print(f"  {B}Fragility Score: {sc}{chaos_score}/100 Grade {chaos_grade}{R}")
            ok(
                f"Mutations:  {s['mutations_applied']} applied · "
                f"{s['mutations_that_broke']} caused failures"
            )
            ok(
                f"Pipelines:  🔴 {s['critical_pipelines']} critical · "
                f"⚠️  {s['fragile_pipelines']} fragile"
            )
            ok(f"DNA:        {s['anti_pattern_score']}/100")
            if chaos_crits:
                print(f"\n  {B}Critical pipelines:{R}")
                for c in chaos_crits:
                    print(f"    {RD}🔴 {c}{R}")
            ok(f"HTML → {HTML_PATH}")
        except Exception as e:
            warn(f"Chaos failed (non-fatal): {e}")
            import traceback

            traceback.print_exc()

    # ── Step 3: Wind Tunnel — BEFORE live mutation ────────────────────────────
    hdr(3, TOTAL, "Wind Tunnel — Validate Migration Before It Ships")
    if args.skip_tunnel:
        warn("Skipped (--skip-tunnel)")
    else:
        print(f"  {B}Migration to test:{R} {migration_sql[:70]}")
        info("Cloning clean DB → applying migration to clone → replaying queries")
        info("Production database is NOT touched during this step")
        print()
        try:
            from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

            wt_cfg = WindTunnelConfig(
                db_url=DB_URL,
                max_queries=80,
                query_timeout_s=15,
                run_semantic_analysis=True,
                dry_run=False,
                data_dir=str(DATA_DIR),
                post_to_pr=False,
                query_source="synthetic",
            )

            tunnel = MigrationWindTunnel(wt_cfg)
            receipt = tunnel.run(
                migration_sql=migration_sql,
                graph_json=graph,  # clean graph before any mutation
            )

            v = receipt.verdict.value if hasattr(receipt.verdict, "value") else str(receipt.verdict)
            vc = GR if v == "SAFE" else YL if "PATCH" in v else RD

            print(f"  {B}Wind Tunnel Verdict: {vc}{v}{R}\n")
            ok(f"Clone:      {receipt.clone_name}  (destroyed after test)")
            ok(f"Queries:    {receipt.queries_replayed} replayed")
            print(f"  {GR}Passed:     {receipt.queries_passed}{R}")

            if receipt.queries_broken:
                print(f"  {RD}Broken:     {receipt.queries_broken}{R}")
                print(f"\n  {RD}These queries will break if this migration ships:{R}")
                for q in receipt.broken_queries[:5]:
                    txt = getattr(q, "query_text", str(q))[:80]
                    err = getattr(q, "clone_error", "")
                    print(f"    {RD}✗{R} {txt}")
                    if err:
                        info(f"      Error: {err[:80]}")

            if receipt.queries_mismatch:
                print(f"  {YL}Row mismatch: {receipt.queries_mismatch}{R}")

            print(f"  {B}Confidence: {receipt.confidence_score}%{R}")
            ok(f"Duration:   {receipt.duration_s:.1f}s")

            if receipt.semantic_risks:
                print(f"\n  {YL}Semantic risks detected:{R}")
                for risk in receipt.semantic_risks[:3]:
                    desc = getattr(risk, "description", str(risk))[:100]
                    sev = getattr(risk, "severity", "")
                    print(f"    {RD if sev == 'CRITICAL' else YL}⚠ {desc}{R}")

            if receipt.patches_available:
                print(f"\n  {GR}Auto-patches for broken queries:{R}")
                for p in receipt.patches_available[:3]:
                    print(f"    ⚡ {str(p)[:80]}")

            ok(f"Receipt → {RECEIPT_PATH}")

        except Exception as e:
            warn(f"Wind Tunnel failed (non-fatal): {e}")
            import traceback

            traceback.print_exc()

    # ── Step 4: Change Gate ───────────────────────────────────────────────────
    hdr(4, TOTAL, "Change Gate — Compatibility Oracle")
    if args.skip_gate:
        warn("Skipped (--skip-gate)")
    else:
        # Build simulated drift report from the intended migration
        # (pre-apply — the gate evaluates what WOULD change)
        simulated_drift = {
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": {"total_changes": 1, "by_severity": {"HIGH": 1}, "is_clean": False},
            "events": [
                {
                    "change_type": "COLUMN_RENAMED",
                    "severity": "HIGH",
                    "node_id": f"{BREAK_TABLE}.{break_col}",
                    "before": {"dtype": "INTEGER", "nullable": True, "cardinality": 0.9},
                    "after": {"dtype": "INTEGER", "nullable": True, "cardinality": 0.9},
                    "detail": f"Column '{BREAK_TABLE}.{break_col}' may have been renamed "
                    f"to '{BREAK_TABLE}.{break_new}'.",
                }
            ],
        }
        try:
            from semzero.integrations.change_gate import ChangeGate, GateConfig

            cfg2 = GateConfig(
                github_token=os.environ.get("SEMZERO_GITHUB_TOKEN", ""),
                github_repo=os.environ.get("SEMZERO_GITHUB_REPO", ""),
                block_on_destructive=True,
                block_on_narrowing=True,
                run_wind_tunnel=False,
                data_dir=str(DATA_DIR),
            )
            gate_obj = ChangeGate(graph, cfg2)
            result = gate_obj.evaluate(simulated_drift)
            result.save(str(GATE_PATH))
            gate_verdict = result.verdict.value

            vc = GR if gate_verdict == "SAFE" else YL if gate_verdict == "NEEDS_REVIEW" else RD
            print(f"  {B}Verdict:      {vc}{gate_verdict}{R}")
            print(f"  Blast radius: {result.total_blast_radius} downstream nodes\n")

            for a in result.assessments[:6]:
                emoji = {
                    "ADDITIVE_SAFE": f"{GR}✅",
                    "ADDITIVE_BREAKING": f"{YL}⚠️",
                    "RENAME_HIGH_CONFIDENCE": f"{YL}🔄",
                    "RENAME_LOW_CONFIDENCE": f"{YL}⚠️",
                    "DESTRUCTIVE_DELETE": f"{RD}🚫",
                    "TYPE_WIDENING": f"{GR}✅",
                    "TYPE_NARROWING": f"{RD}🚫",
                    "NULLABLE_HARDENING": f"{YL}⚠️",
                    "SEMANTIC_BREAKING": f"{RD}🚫",
                }.get(a.compatibility.value, "❓")
                print(f"  {emoji}{R}  {a.node_id:<35} {D}{a.compatibility.value}{R}")

            if result.blocked_by:
                print(f"\n  {RD}Blocking:{R}")
                for b in result.blocked_by[:3]:
                    print(f"    → {b}")

            # Cross-ref Chaos findings
            overlap = set(chaos_crits) & {BREAK_TABLE}
            if overlap:
                print(f"\n  {RD}⚡ Chaos Mode context:{R}")
                print(f"    🔴 `{BREAK_TABLE}` was CRITICAL in last Chaos run")
                print(f"    This rename is a known breaking pattern")

            # Wind Tunnel cross-ref
            if receipt and receipt.queries_broken > 0:
                print(f"\n  {RD}⚡ Wind Tunnel context:{R}")
                print(f"    {receipt.queries_broken} queries break after this migration")
                print(f"    Confidence: {receipt.confidence_score}%")
                if gate_verdict in ("NEEDS_REVIEW", "SAFE"):
                    gate_verdict = "BLOCK"
                    print(f"    {RD}⬆ Verdict escalated → BLOCK{R}")

            ok(f"Gate result → {GATE_PATH}")
        except Exception as e:
            warn(f"Change Gate failed: {e}")
            import traceback

            traceback.print_exc()

    # ── Step 5: Apply migration to live DB ────────────────────────────────────
    hdr(5, TOTAL, f"Apply Migration to Live Database")
    blocked_by_receipt = bool(receipt and (getattr(receipt, "queries_broken", 0) or 0) > 0)
    blocked_by_gate = gate_verdict in ("BLOCK", "DENY")
    should_block_live_apply = blocked_by_receipt or blocked_by_gate
    if should_block_live_apply and args.mode != "validation-apply":
        warn("Skipping live apply because SemZero blocked the change in safe mode.")
        if blocked_by_gate:
            info(f"Gate verdict prevented apply: {gate_verdict}")
        if blocked_by_receipt and receipt:
            info(
                f"Wind Tunnel prevented apply: {receipt.queries_broken} query break(s) detected in clone"
            )
        info("Reports and repair guidance are still generated below.")
    else:
        if should_block_live_apply:
            warn("Controlled validation apply enabled despite BLOCK verdicts.")
        else:
            info(
                "No blocking verdicts detected. Applying validated migration to the live validation database."
            )
        print()
        engine = get_engine()
        try:
            if col_exists(engine, BREAK_TABLE, break_new):
                warn(f"{BREAK_TABLE}.{break_new} already exists — previous run left it")
                info(f"Revert: ALTER TABLE {BREAK_TABLE} RENAME COLUMN {break_new} TO {break_col};")
            elif col_exists(engine, BREAK_TABLE, break_col):
                rename_col(engine, BREAK_TABLE, break_col, break_new)
                ok(f'ALTER TABLE "{BREAK_TABLE}" RENAME COLUMN "{break_col}" → "{break_new}"')
                info("Live validation database updated")
                applied = True
            else:
                warn(f"Column {break_col} not found in {BREAK_TABLE}")
        except Exception as e:
            warn(f"Migration failed: {e}")
        finally:
            engine.dispose()

    # ── Step 6: Re-crawl + diff ───────────────────────────────────────────────
    hdr(6, TOTAL, "Re-Crawl — Detect Actual Drift")
    drift_dict = {"events": [], "summary": {"total_changes": 0}}
    graph2 = graph
    try:
        graph2 = crawl("after_mutation")
        ok(
            f"{graph2['meta'].get('table_count', 0)} tables · "
            f"Snapshot ID: {graph2.get('_snapshot_id', '?')}"
        )

        from semzero.crawler.drift import SchemaDriftDetector

        before, after = latest_two_snapshots()
        dr = SchemaDriftDetector().diff(
            before,
            after,
            before_label="before_mutation",
            after_label="after_mutation",
        )
        drift_dict = dr.to_dict()
        DRIFT_PATH.write_text(json.dumps(drift_dict, indent=2, default=str))

        n = drift_dict["summary"]["total_changes"]
        if n == 0:
            warn("No drift detected — snapshots identical")
        else:
            ok(f"{n} change(s) detected")
            for ev in drift_dict.get("events", [])[:5]:
                sev = ev.get("severity", "")
                sc = RD if sev == "CRITICAL" else YL if sev == "HIGH" else D
                print(f"    {sc}→ {ev.get('change_type')}: {ev.get('node_id')}{R}")
    except Exception as e:
        warn(f"Drift detection failed: {e}")
        import traceback

        traceback.print_exc()

    # ── Step 7: Repair ────────────────────────────────────────────────────────
    hdr(7, TOTAL, "Repair — Generate Fix SQL")
    try:
        from semzero.orchestrator.repair import RepairEngine
        from semzero.crawler.drift import DriftEvent, ChangeType, Severity

        events = []
        for ev in drift_dict.get("events", []):
            try:
                events.append(
                    DriftEvent(
                        change_type=ChangeType(ev["change_type"]),
                        severity=Severity(ev["severity"]),
                        node_id=ev["node_id"],
                        before=ev.get("before"),
                        after=ev.get("after"),
                        detail=ev.get("detail", ""),
                    )
                )
            except Exception:
                pass

        if events:
            engine_r = RepairEngine()
            plan_fn = getattr(engine_r, "build_plan", getattr(engine_r, "plan", None))
            if plan_fn:
                plan = plan_fn(events)
                if hasattr(plan, "to_dict"):
                    REPAIR_PATH.write_text(json.dumps(plan.to_dict(), indent=2, default=str))
                sql = (
                    plan.render_sql_script()
                    if hasattr(plan, "render_sql_script")
                    else "-- SQL rendering unavailable\n"
                )
                (DATA_DIR / "migration.sql").write_text(sql)
                rs = plan.summary() if hasattr(plan, "summary") else {}
                ok(
                    f"Actions: {rs.get('total_actions', 0)} · "
                    f"auto: {rs.get('auto_executable', 0)} · "
                    f"approval: {rs.get('needs_approval', 0)}"
                )
                ok(f"SQL → {DATA_DIR / 'migration.sql'}")
                if os.environ.get("SEMZERO_GITHUB_TOKEN"):
                    info("→ semzero repair --open-pr to ship")
        else:
            warn("No drift events to repair")
    except Exception as e:
        warn(f"Repair failed: {e}")
        import traceback

        traceback.print_exc()

    # ── Step 8: Restore ───────────────────────────────────────────────────────
    hdr(8, TOTAL, "Restore Database")
    engine = get_engine()
    try:
        if applied and col_exists(engine, BREAK_TABLE, break_new):
            rename_col(engine, BREAK_TABLE, break_new, break_col)
            ok(f'Reverted: "{BREAK_TABLE}"."{break_new}" → "{break_col}"')
        elif applied:
            warn(f"{break_new} not found — may already be reverted")
        else:
            info("Nothing to revert")
    except Exception as e:
        warn(f"Revert failed: {e}")
        warn(f"Manual: ALTER TABLE {BREAK_TABLE} RENAME COLUMN {break_new} TO {break_col};")
    finally:
        engine.dispose()

    # ── Summary ───────────────────────────────────────────────────────────────
    dur = time.time() - t_start
    sc = GR if chaos_score >= 80 else YL if chaos_score >= 60 else RD
    vc = GR if gate_verdict == "SAFE" else YL if gate_verdict == "NEEDS_REVIEW" else RD

    wt_str = "—"
    if receipt:
        v = receipt.verdict.value if hasattr(receipt.verdict, "value") else str(receipt.verdict)
        wt_c = GR if v == "SAFE" else YL if "PATCH" in v else RD
        wt_str = (
            f"{wt_c}{receipt.confidence_score}% ({v}) · "
            f"{receipt.queries_passed}/{receipt.queries_replayed} passed · "
            f"{receipt.queries_broken} broken{R}"
        )

    repair_n = "—"
    if REPAIR_PATH.exists():
        try:
            d = json.loads(REPAIR_PATH.read_text())
            n = d.get("summary", {}).get("total_actions", 0)
            repair_n = f"{GR}{n} action(s){R}"
        except Exception:
            pass

    print(f"""
{B}{CY}{"═" * 58}{R}
{B}  Full Loop Complete — {dur:.1f}s{R}
{B}{CY}{"═" * 58}{R}

  {B}Fragility Score{R}   {sc}{chaos_score}/100  Grade {chaos_grade}{R}
  {B}Gate Verdict{R}      {vc}{gate_verdict}{R}
  {B}Wind Tunnel{R}       {wt_str}
  {B}Repair Actions{R}    {repair_n}

  {B}Files:{R}
    Chaos HTML     → open data/chaos_report.html
    WT Receipt     → data/simulation_receipt.json
    Drift report   → data/drift_report.json
    Gate result    → data/gate_result.json
    Repair SQL     → data/migration.sql

  {B}The story:{R}
  {D}Chaos Mode scored the schema {chaos_score}/100 Grade {chaos_grade} this week.
  A SWE wanted to rename {BREAK_TABLE}.{break_col} → {break_new}.
  Wind Tunnel cloned the DB and validated {receipt.queries_replayed if receipt else 0} queries.
  {receipt.queries_broken if receipt else 0} {"query breaks" if (receipt and receipt.queries_broken == 1) else "queries break"}.
  Gate verdict: {gate_verdict}.
  Repair SQL ready — semzero repair --open-pr to ship the fix.{R}
""")


if __name__ == "__main__":
    main()
