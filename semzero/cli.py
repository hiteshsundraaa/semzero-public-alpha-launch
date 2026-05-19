"""
cli.py — SemZero production CLI.

Commands:
  semzero doctor       --db-url <url>
  semzero scan         --db-url <url>
  semzero crawl        --db-url <url>
  semzero diff         --before <snapshot_id | json> --after <snapshot_id | json>
  semzero blast        --node <node_id>
  semzero match        --source <json> --target <json>
  semzero repair       --drift <json> [--open-pr]
  semzero report       --graph <json> [--drift <json>] [--repair <json>]
  semzero watch        --db-url <url> --interval <seconds>
  semzero history
  semzero chaos        --db-url <url>
  semzero chaos-schedule --db-url <url>
  semzero wind-tunnel  --db-url <url> --migration <file|sql>
  semzero gate         --db-url <url> --drift <json> [--pr <number>]
  semzero premerge     --graph <json> --drift <json> [--migration <sql|file>]
  semzero trace        --node <node_id>
  semzero ops-report   --gate <json> [--wind-tunnel <json>] [--chaos <json>]
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import click

from .version import __version__, release_info


# ── Logging setup ──────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("snowflake").setLevel(logging.WARNING)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        click.echo(f"Error: file not found: {path}", err=True)
        sys.exit(1)
    return json.loads(p.read_text())


def _save_json(data: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str))


def _read_migration(migration: str) -> str:
    """Accept a file path or raw SQL string."""
    p = Path(migration)
    if p.exists():
        sql = p.read_text().strip()
        click.echo(f"  Migration file: {p} ({len(sql):,} chars)")
        return sql
    click.echo(f"  Migration: inline SQL ({len(migration):,} chars)")
    return migration


def _default_proof_paths() -> list[str]:
    common = [
        "models",
        "sql",
        "transformations",
        "pipelines",
        "dags",
        "src",
        "app",
        "backend",
        "api",
        "prisma",
    ]
    return [name for name in common if Path(name).exists()]


# ── Root group ─────────────────────────────────────────────────────────────────


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.version_option("0.8.0a2", prog_name="semzero")
def cli(verbose: bool):
    """SemZero — dbt PR Assumption Gate for hidden SQL assumptions.\n
    Run in shadow mode to review assumption drift, blast radius, replay-lite evidence, and cost exposure before merge.
    """
    _setup_logging(verbose)


@cli.command("release-info")
@click.option("--output", default="", show_default=False, help="Optional JSON output path")
def release_info_cmd(output: str):
    """Show SemZero release metadata and version lineage."""
    payload = release_info.to_dict()
    if output:
        _save_json(payload, output)
        click.echo(f"  Release metadata → {output}")
    click.echo(json.dumps(payload, indent=2))


@cli.command("commands")
def commands_cmd():
    """Show the current SemZero command surface and where to find the command guide."""
    click.echo("\n  SemZero command surface\n")
    click.echo("  Daily / high-level commands")
    click.echo("    semzero quickstart Show the shortest first-user path and repo setup hints")
    click.echo("    semzero demo       Run the focused killer demo from a source checkout")
    click.echo("    semzero doctor-assumption-ci Check dbt repo readiness for SemZero CI")
    click.echo("    semzero baseline-check Verify a clean dbt repo still matches a stored SemZero baseline")
    click.echo("    semzero smoke-summary Classify SemZero smoke artifacts as PASS/PARTIAL/FAIL")
    click.echo("    semzero check     Reuse the best current receipt and summarize risk")
    click.echo("    semzero explain   Explain why the current receipt blocked/warned")
    click.echo("    semzero recheck   Run a fresh high-level validation wrapper")
    click.echo(
        "    semzero assumption-gate  Run focused dbt hidden-assumption + blast-radius PR gate"
    )
    click.echo(
        "    semzero assumption-ci    CI wrapper for dbt Assumption Gate + PR summary artifacts"
    )
    click.echo("    semzero compare   Compare two receipts or report bundles")
    click.echo("    semzero report    Render the current HTML graph/report artifact")
    click.echo("    semzero fix       Generate repair guidance or rollback help")
    click.echo("    semzero init-ci   Scaffold legacy broad SemZero CI starter config")
    click.echo("    semzero init-assumption-ci Scaffold focused dbt Assumption Gate PR workflow")
    click.echo("    semzero shadow    Run the full premerge workflow in shadow mode")
    click.echo("    semzero shadow-dashboard Build would-have-blocked / would-have-saved proof")
    click.echo(
        "    semzero assumption-dashboard Aggregate Assumption Gate receipts by family/blast radius"
    )
    click.echo("    semzero assumption-lineage  Build Assumption Lineage Lite graph from receipts")
    click.echo("    semzero assumption-decay  Track recurring/stale/fragile assumptions over time")
    click.echo(
        "    semzero assumption-memory  Summarize organization/team/model assumption drift memory"
    )
    click.echo(
        "    semzero assumption-precision-eval Evaluate useful/noisy/over-broad assumption findings"
    )
    click.echo(
        "    semzero assumption-dogfood-report Build demo report from dogfood receipts/dashboard"
    )
    click.echo(
        "    semzero assumption-feedback  Record developer agreement/disagreement for shadow calibration"
    )
    click.echo(
        "    semzero assumption-exception Record reason-required accepted-risk/suppression exceptions"
    )
    click.echo("    semzero shadow-feedback  Record developer feedback for shadow calibration")
    click.echo("    semzero streaming-shadow   Run Kafka/topic schema + consumer-contract checks")
    click.echo("    python -m semzero Use the CLI without shell entrypoint issues")
    click.echo("\n  Experimental / legacy engine commands")
    click.echo("    semzero doctor | scan | crawl | diff | blast | match | repair")
    click.echo("    semzero gate | wind-tunnel | chaos | premerge | validate-e2e")
    click.echo("    semzero assumption-gate | assumption-ci")
    click.echo("    semzero trace | watch | history | ops-report | release-info")
    click.echo("    semzero streaming-shadow | shadow-trends")
    click.echo("\n  Docs")
    click.echo("    docs/COMMANDS.md\n")




@cli.command("quickstart")
@click.option("--repo", default=".", show_default=True, help="Repository root to inspect for dbt/SemZero setup hints.")
def quickstart(repo: str):
    """Show the shortest path from clone/install to first SemZero PR review."""
    root = Path(repo)
    click.echo("\nSemZero quickstart")
    click.echo("===================")
    click.echo("Goal: run SemZero as a shadow-mode dbt PR Assumption Gate.\n")

    has_dbt = (root / "dbt_project.yml").exists()
    has_manifest = (root / "target" / "manifest.json").exists()
    has_workflow = (root / ".github" / "workflows" / "semzero_assumption_gate.yml").exists()
    has_semzero = (root / ".semzero").exists()

    click.echo("Detected in this repo:")
    click.echo(f"  dbt_project.yml: {'yes' if has_dbt else 'no'}")
    click.echo(f"  target/manifest.json: {'yes' if has_manifest else 'no'}")
    click.echo(f"  SemZero workflow: {'yes' if has_workflow else 'no'}")
    click.echo(f"  .semzero config dir: {'yes' if has_semzero else 'no'}")

    click.echo("\nFastest local demo:")
    click.echo("  python scripts/run_killer_demo.py")

    click.echo("\nAdd SemZero to a dbt repo:")
    click.echo("  semzero init-assumption-ci --output-dir .")
    click.echo("  git add .github/workflows/semzero_assumption_gate.yml .semzero/")
    click.echo('  git commit -m "Add SemZero assumption gate"')

    click.echo("\nManual local run:")
    click.echo("  semzero assumption-ci --dbt-manifest target/manifest.json --base-ref origin/main")

    if not has_manifest:
        click.echo("\nManifest note:")
        click.echo("  SemZero needs dbt target/manifest.json. Run `dbt compile` first, or commit/build the manifest in CI.")
    if not has_workflow:
        click.echo("\nNext best step:")
        click.echo("  Run `semzero init-assumption-ci --output-dir .` in your dbt repo.")
    click.echo()


@cli.command("demo")
def demo_cmd():
    """Run the bundled killer demo when this source repo is checked out."""
    import subprocess

    script = Path("scripts/run_killer_demo.py")
    if not script.exists():
        click.echo("\nSemZero killer demo")
        click.echo("====================")
        click.echo("The source checkout demo script was not found at scripts/run_killer_demo.py.")
        click.echo("\nRun the demo from a SemZero source checkout:")
        click.echo("  git clone https://github.com/hiteshsundraaa/semzero.git")
        click.echo("  cd semzero")
        click.echo('  pip install -e ".[dev]"')
        click.echo("  semzero demo")
        click.echo("\nOr run directly:")
        click.echo("  python scripts/run_killer_demo.py\n")
        return
    result = subprocess.run([sys.executable, str(script)], text=True)
    if result.returncode != 0:
        raise click.ClickException(f"Killer demo failed with exit code {result.returncode}.")


@cli.command("doctor-assumption-ci")
@click.option("--repo", default=".", show_default=True, help="dbt repository root to inspect.")
@click.option("--dbt-manifest", default="target/manifest.json", show_default=True, help="Manifest path relative to --repo unless absolute.")
def doctor_assumption_ci(repo: str, dbt_manifest: str):
    """Check whether a dbt repo is ready for the SemZero Assumption Gate workflow."""
    root = Path(repo)
    manifest = Path(dbt_manifest)
    if not manifest.is_absolute():
        manifest = root / manifest

    checks = []

    def add(name: str, ok: bool, fix: str = ""):
        checks.append((name, ok, fix))

    add("repository path exists", root.exists(), f"Create or check path: {root}")
    add("dbt_project.yml present", (root / "dbt_project.yml").exists(), "Run this in a dbt project root or add dbt_project.yml.")
    add("dbt manifest present", manifest.exists(), "Run `dbt compile` so target/manifest.json exists, or pass --dbt-manifest.")
    add("SemZero workflow present", (root / ".github" / "workflows" / "semzero_assumption_gate.yml").exists(), "Run `semzero init-assumption-ci --output-dir .`.")
    add(".semzero directory present", (root / ".semzero").exists(), "Run `semzero init-assumption-ci --output-dir .` to create starter config.")
    add("Git repo detected", (root / ".git").exists(), "Initialize git or run inside the cloned repo; CI uses git diff.")

    optional = [
        ("Replay Lite fixtures", root / ".semzero" / "replay_fixtures.json"),
        ("business criticality registry", root / ".semzero" / "business_criticality.yml"),
        ("cost profiles", root / ".semzero" / "cost_profiles.yml"),
        ("dbt catalog", root / "target" / "catalog.json"),
        ("dbt run results", root / "target" / "run_results.json"),
    ]

    click.echo("\nSemZero Assumption CI doctor")
    click.echo("============================")
    click.echo(f"Repo: {root.resolve() if root.exists() else root}")
    click.echo("\nRequired checks:")
    failed = 0
    for name, ok, fix in checks:
        marker = "OK" if ok else "MISSING"
        click.echo(f"  [{marker}] {name}")
        if not ok:
            failed += 1
            click.echo(f"         fix: {fix}")

    click.echo("\nOptional evidence inputs:")
    for name, path in optional:
        click.echo(f"  [{'found' if path.exists() else 'not found'}] {name}: {path}")

    click.echo("\nRecommended next command:")
    if failed:
        click.echo("  semzero init-assumption-ci --output-dir .")
        click.echo("  dbt compile")
    else:
        click.echo("  semzero assumption-ci --dbt-manifest target/manifest.json --base-ref origin/main")
    click.echo()
    if failed:
        raise click.ClickException(f"{failed} required setup check(s) need attention.")


@cli.command("baseline-check")
@click.option("--repo", required=True, help="Clean dbt repository root to check.")
@click.option(
    "--baseline-dir",
    required=True,
    help="Directory containing the stored clean baseline receipt.json.",
)
@click.option("--output", required=True, help="JSON output path for the baseline check result.")
@click.option(
    "--priority-tolerance",
    default=3,
    show_default=True,
    type=int,
    help="Allowed finding priority drift in points.",
)
def baseline_check_cmd(repo: str, baseline_dir: str, output: str, priority_tolerance: int):
    """Verify a clean repo still matches a stored SemZero baseline."""
    from semzero.repo_understanding.mutation_harness import run_baseline_check

    result = run_baseline_check(
        repo=repo,
        baseline_dir=baseline_dir,
        output=output,
        priority_tolerance=priority_tolerance,
    )
    status = result.get("status")
    click.echo("\n  SemZero Baseline Check")
    click.echo(f"  Status: {status}")
    if result.get("mismatches"):
        click.echo(f"  Mismatches: {len(result['mismatches'])}")
        for mismatch in result["mismatches"][:5]:
            click.echo(f"    - {mismatch}")
    if result.get("reason"):
        click.echo(f"  Reason: {result['reason']}")
    click.echo(f"  Result → {output}\n")
    if status != "PASS":
        raise click.ClickException("Clean repo output did not match a valid stored baseline.")


@cli.command("smoke-summary")
@click.option("--artifact-dir", required=True, help="Directory containing smoke run artifacts.")
@click.option("--scenario", required=True, help="Smoke scenario id to classify.")
@click.option(
    "--output-dir",
    default="",
    show_default=False,
    help="Directory for smoke_summary.json/md. Defaults to --artifact-dir.",
)
@click.option(
    "--expected-changed-file-count",
    default=1,
    show_default=True,
    type=int,
    help="Expected changed-file count for clean smoke branches.",
)
def smoke_summary_cmd(
    artifact_dir: str,
    scenario: str,
    output_dir: str,
    expected_changed_file_count: int,
):
    """Classify SemZero smoke artifacts without manually inspecting JSON."""
    from semzero.repo_understanding.mutation_harness import write_smoke_summary_artifacts

    artifacts = write_smoke_summary_artifacts(
        artifact_dir,
        scenario=scenario,
        output_dir=output_dir or None,
        expected_changed_file_count=expected_changed_file_count,
    )
    summary = artifacts["smoke_summary"]["summary"]
    click.echo("\n  SemZero Smoke Summary")
    click.echo(f"  Scenario: {summary['scenario']}")
    click.echo(f"  Result: {summary['result']}")
    click.echo(f"  Reason: {summary['reason']}")
    click.echo(f"  Primary: {summary['actual_primary'] or 'none'}")
    click.echo(f"  Changed files: {summary['changed_file_count']}")
    click.echo(f"  Compile: {summary['compile_status']}\n")
    if summary["result"] == "FAIL":
        raise click.ClickException("Smoke summary classified this run as FAIL.")


@cli.command("init-ci")
@click.option(
    "--preset",
    default="github",
    show_default=True,
    type=click.Choice(["github", "snowflake", "databricks", "sqlite"]),
)
@click.option(
    "--output-dir",
    default=".",
    show_default=True,
    help="Repository root where SemZero starter files should be written.",
)
@click.option(
    "--force", is_flag=True, default=False, help="Overwrite starter files if they already exist."
)
def init_ci(preset, output_dir, force):
    """Scaffold a drop-in CI workflow and starter config for fast time-to-value."""
    root = Path(output_dir)
    workflow_dir = root / ".github" / "workflows"
    docs_dir = root / ".semzero"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    preset_env = {
        "github": "SEMZERO_DB_URL=<warehouse-url>",
        "snowflake": "SEMZERO_DB_URL=snowflake://<user>:<password>@<account>/<database>/<schema>?warehouse=<warehouse>",
        "databricks": "SEMZERO_DB_URL=databricks://token:<token>@<server-hostname>?http_path=/sql/1.0/endpoints/<warehouse>&catalog=<catalog>&schema=<schema>",
        "sqlite": "SEMZERO_DB_URL=sqlite:///tmp/semzero_demo.db",
    }[preset]

    workflow = """name: SemZero Quickstart

on:
  pull_request:
    paths:
      - '**/*.sql'
      - 'models/**/*.sql'
      - 'models/**/*.yml'
      - 'dbt/**'

jobs:
  semzero:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install SemZero
        run: |
          pip install -e .
          if [ -n "${{ vars.SEMZERO_EXTRA_PIP_PACKAGES }}" ]; then pip install ${{ vars.SEMZERO_EXTRA_PIP_PACKAGES }}; fi
      - name: SemZero shadow check
        env:
          SEMZERO_DB_URL: ${{ secrets.SEMZERO_DB_URL }}
        run: |
          mkdir -p data
          semzero commands
          # Equivalent expert path: semzero premerge --shadow-mode ...
          semzero shadow --graph data/schema_graph.json --drift data/drift_report.json --db-url "$SEMZERO_DB_URL" || true
"""
    env_txt = f"""# SemZero starter environment
{preset_env}
SEMZERO_GITHUB_TOKEN=<github-token>
SEMZERO_GITHUB_REPO=<owner/repo>
SEMZERO_EXTRA_PIP_PACKAGES=
"""
    cmd_txt = """semzero commands
semzero check pr 184
semzero explain --search-dir data
semzero recheck --mode premerge --db-url "$SEMZERO_DB_URL" --migration migrations/latest.sql
semzero report --search-dir data --format html --output data/semzero_receipt.html
"""

    files = {
        workflow_dir / "semzero_quickstart.yml": workflow,
        docs_dir / "config.env.example": env_txt,
        docs_dir / "quickstart_commands.txt": cmd_txt,
    }
    for path, payload in files.items():
        if path.exists() and not force:
            raise click.ClickException(
                f"Starter file already exists: {path}. Use --force to overwrite."
            )
        path.write_text(payload, encoding="utf-8")

    click.echo(f"\n  SemZero quickstart scaffolded for preset: {preset}")
    click.echo(f"  Workflow → {workflow_dir / 'semzero_quickstart.yml'}")
    click.echo(f"  Env file → {docs_dir / 'config.env.example'}")
    click.echo(f"  Example commands → {docs_dir / 'quickstart_commands.txt'}\n")


@cli.command("init-assumption-ci")
@click.option(
    "--output-dir",
    default=".",
    show_default=True,
    help="Repository root where the focused Assumption Gate workflow should be written.",
)
@click.option(
    "--force", is_flag=True, default=False, help="Overwrite starter files if they already exist."
)
@click.option(
    "--profile",
    "profile_adapter",
    type=click.Choice(["none", "duckdb"]),
    default="none",
    show_default=True,
    help="Optionally scaffold a minimal dbt CI profile. Use duckdb for public/sample repos without warehouse credentials.",
)
def init_assumption_ci(output_dir: str, force: bool, profile_adapter: str):
    """Scaffold the focused dbt Assumption Gate GitHub PR workflow."""
    root = Path(output_dir)
    workflow_dir = root / ".github" / "workflows"
    semzero_dir = root / ".semzero"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    semzero_dir.mkdir(parents=True, exist_ok=True)
    dbt_profile_name = "default"
    dbt_project_path = root / "dbt_project.yml"
    if dbt_project_path.exists():
        for line in dbt_project_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("profile:"):
                dbt_profile_name = stripped.split(":", 1)[1].split("#", 1)[0].strip().strip("'\"") or "default"
                break


    workflow = """name: SemZero Assumption Gate

on:
  pull_request:
    paths:
      - 'models/**/*.sql'
      - 'models/**/*.yml'
      - 'models/**/*.yaml'
      - 'macros/**/*.sql'
      - 'snapshots/**/*.sql'
      - 'seeds/**/*'
      - 'dbt_project.yml'
      - 'packages.yml'
      - '.semzero/**'

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: semzero-assumption-gate-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: true

env:
  SEMZERO_ARTIFACT_DIR: data/semzero_assumption_gate
__SEMZERO_DBT_PROFILES_ENV__

jobs:
  assumption-gate:
    name: dbt PR Assumption Gate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install SemZero and optional project dependencies
        env:
          SEMZERO_PACKAGE_SPEC: ${{ vars.SEMZERO_PACKAGE_SPEC }}
        run: |
          python -m pip install --upgrade pip

          if [ -n "$SEMZERO_PACKAGE_SPEC" ]; then
            pip install "$SEMZERO_PACKAGE_SPEC"
          elif [ -f semzero/cli.py ] && grep -q "init-assumption-ci" semzero/cli.py; then
            echo "SEMZERO_PACKAGE_SPEC not set; detected local SemZero source checkout."
            pip install -e .
          else
            echo "::error::SemZero is not installed and SEMZERO_PACKAGE_SPEC is not configured."
            echo "Set repository variable SEMZERO_PACKAGE_SPEC to a SemZero package source."
            echo "Example: git+https://github.com/hiteshsundraaa/semzero-public-alpha-launch.git@main"
            exit 1
          fi

          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          if [ -n "${{ vars.SEMZERO_EXTRA_PIP_PACKAGES }}" ]; then
            pip install ${{ vars.SEMZERO_EXTRA_PIP_PACKAGES }}
          fi

      - name: Build dbt manifest when missing
        run: |
          if [ ! -f target/manifest.json ] && [ -f dbt_project.yml ]; then
            if command -v dbt >/dev/null 2>&1; then
              dbt deps || true
              dbt compile || true
            else
              echo "dbt executable not found; skipping automatic manifest build."
            fi
          fi
          if [ ! -f target/manifest.json ]; then
            echo "::error::SemZero could not find target/manifest.json."
            echo "Run dbt compile in CI, commit/provide a manifest artifact, or install the dbt adapter via SEMZERO_EXTRA_PIP_PACKAGES."
            exit 1
          fi

      - name: Run SemZero Assumption Gate in shadow mode
        run: |
          OPTIONAL_FLAGS=""
          if [ -f target/catalog.json ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --dbt-catalog target/catalog.json"; fi
          if [ -f target/run_results.json ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --dbt-run-results target/run_results.json"; fi
          if [ -f .semzero/cost_profiles.yml ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --cost-profiles .semzero/cost_profiles.yml"; fi
          if [ -f .semzero/warehouse_history.csv ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --warehouse-history .semzero/warehouse_history.csv"; fi
          if [ -f .semzero/business_criticality.yml ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --criticality-registry .semzero/business_criticality.yml"; fi
          if [ -f .semzero/replay_fixtures.json ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --replay-fixtures .semzero/replay_fixtures.json"; fi
          if [ -f .semzero/assumption_exceptions.jsonl ]; then OPTIONAL_FLAGS="$OPTIONAL_FLAGS --exceptions-file .semzero/assumption_exceptions.jsonl"; fi

          semzero assumption-ci \
            --dbt-manifest target/manifest.json \
            --base-ref origin/${{ github.base_ref }} \
            --mode shadow \
            --project-dir . \
            --output-dir "$SEMZERO_ARTIFACT_DIR" \
            $OPTIONAL_FLAGS

      - name: Upload SemZero evidence
        if: ${{ always() && hashFiles('data/semzero_assumption_gate/**') != '' }}
        uses: actions/upload-artifact@v4
        with:
          name: semzero-assumption-gate-${{ github.event.pull_request.number || github.sha }}
          path: data/semzero_assumption_gate/
          if-no-files-found: ignore

      - name: Comment on PR with sticky SemZero review
        if: ${{ always() && github.event_name == 'pull_request' && hashFiles('data/semzero_assumption_gate/comment.md') != '' }}
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const body = fs.readFileSync('data/semzero_assumption_gate/comment.md', 'utf8');
            const marker = '<!-- semzero-assumption-gate -->';
            const artifactNote = '\\n\\n---\\nSemZero ran in **shadow mode**. Evidence artifacts are attached to this workflow run.';
            const finalBody = `${marker}\\n${body}${artifactNote}`;
            const comments = await github.rest.issues.listComments({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              per_page: 100,
            });
            const existing = comments.data.find(c => c.body && c.body.includes(marker));
            if (existing) {
              await github.rest.issues.updateComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                comment_id: existing.id,
                body: finalBody,
              });
            } else {
              await github.rest.issues.createComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                issue_number: context.issue.number,
                body: finalBody,
              });
            }
"""
    workflow = workflow.replace(
        "__SEMZERO_DBT_PROFILES_ENV__",
        "  DBT_PROFILES_DIR: .semzero" if profile_adapter != "none" else "",
    )

    policy = """# SemZero Assumption Gate starter policy
mode: shadow
# Keep this focused: dbt Assumption Gate first. Future adapters should plug into
# the same receipt/finding/node model, but are intentionally not enabled here.
require_review_on:
  - temporal_bucket
  - incremental_filter
  - join_cardinality
  - enum_domain_closure
  - null_default_fallback
"""
    semzero_readme = """# SemZero Assumption Gate configuration

These files are optional inputs for the focused dbt PR Assumption Gate.

Start in shadow mode. Add Replay Lite fixtures, warehouse-history exports, and business-criticality labels only when they improve evidence quality.
"""
    replay_example = """{
  "temporal_bucket": {
    "rows": [
      {"before_bucket": "2026-05-01", "after_bucket": "2026-04-30"},
      {"before_bucket": "2026-05-01", "after_bucket": "2026-05-01"}
    ]
  }
}
"""
    cost_example = """# Optional. Used only for cost/exposure context.
default:
  engine: snowflake
  estimated_cost_per_gb_scanned_usd: 0.02
  monthly_run_count: 30
"""
    criticality_example = """# Optional. Map dbt resources/exposures to business criticality.
exposure.executive_revenue_dashboard:
  severity: BOARD_CRITICAL
  owner: finance
model.finance_daily_revenue:
  severity: HIGH
  owner: finance
"""
    exceptions_example = """# JSONL accepted-risk/suppression records go here when needed.
# Keep exceptions time-bound and reviewed.
"""
    files = {
        workflow_dir / "semzero_assumption_gate.yml": workflow,
        semzero_dir / "assumption_gate_policy.yml": policy,
        semzero_dir / "README.md": semzero_readme,
        semzero_dir / "replay_fixtures.example.json": replay_example,
        semzero_dir / "cost_profiles.example.yml": cost_example,
        semzero_dir / "business_criticality.example.yml": criticality_example,
        semzero_dir / "assumption_exceptions.example.jsonl": exceptions_example,
    }
    if profile_adapter == "duckdb":
        files[semzero_dir / "profiles.yml"] = f"""# Minimal dbt profile for SemZero CI dogfooding.
# Uses DuckDB so public/sample repos can compile without warehouse credentials.
{dbt_profile_name}:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: semzero_ci.duckdb
      threads: 4
"""

    for path, payload in files.items():
        if path.exists() and not force:
            raise click.ClickException(
                f"Starter file already exists: {path}. Use --force to overwrite."
            )
        path.write_text(payload, encoding="utf-8")
    click.echo("\n  SemZero Assumption Gate CI scaffolded")
    click.echo(f"  Workflow → {workflow_dir / 'semzero_assumption_gate.yml'}")
    click.echo(f"  Policy → {semzero_dir / 'assumption_gate_policy.yml'}")
    if profile_adapter == "duckdb":
        click.echo(f"  dbt CI profile → {semzero_dir / 'profiles.yml'}")
        click.echo("  Recommended GitHub variable: SEMZERO_EXTRA_PIP_PACKAGES=dbt-core dbt-duckdb")
    click.echo("\n  Next steps:")
    click.echo("    1. Make sure CI can create target/manifest.json with dbt compile.")
    click.echo("    2. Commit .github/workflows/semzero_assumption_gate.yml and .semzero/.")
    click.echo("    3. Open a dbt SQL PR; SemZero comments in shadow mode by default.")
    click.echo("    4. If setup fails, run: semzero doctor-assumption-ci --repo .")
    click.echo()


@cli.command("assumption-gate")
@click.option("--dbt-manifest", required=True, help="Path to dbt target/manifest.json")
@click.option(
    "--changed-file",
    "changed_files",
    multiple=True,
    help="Changed dbt model/schema file. Pass once per changed file.",
)
@click.option(
    "--changed-files",
    "changed_files_blob",
    default="",
    help="Comma/newline-separated changed file list, useful in CI.",
)
@click.option(
    "--changed-diff",
    default="",
    help="Optional unified diff text/file for stronger trigger evidence and why-now explanations.",
)
@click.option(
    "--mode",
    default="shadow",
    show_default=True,
    type=click.Choice(["shadow", "advisory", "require-review"]),
)
@click.option(
    "--table-sizes",
    default="",
    help="Optional JSON mapping model name/unique_id to table size in GB for rough cost estimates.",
)
@click.option(
    "--cost-profiles",
    default="",
    help="Optional JSON/YAML warehouse-aware cost profiles for Snowflake/Databricks/dbt monthly exposure estimates.",
)
@click.option(
    "--warehouse-history",
    default="",
    help="Optional offline Snowflake query_history / Databricks job-run / dbt runtime CSV or JSON export for cost calibration without live credentials.",
)
@click.option(
    "--replay-fixtures",
    default="",
    help="Optional JSON fixture/sample data for Assumption Validation Replay Lite. Non-blocking; not full warehouse replay.",
)
@click.option(
    "--dbt-catalog",
    default="",
    help="Optional dbt target/catalog.json for column metadata and richer artifact context.",
)
@click.option(
    "--dbt-run-results",
    default="",
    help="Optional dbt target/run_results.json for model timing/status context.",
)
@click.option(
    "--project-dir",
    default="",
    help="Optional dbt project root for resolving compiled_path entries from manifest.json.",
)
@click.option(
    "--criticality-registry",
    default="",
    help="Optional JSON/YAML registry mapping dbt nodes to business criticality labels/severities.",
)
@click.option(
    "--exceptions-file",
    default="",
    help="Optional JSONL accepted-risk/suppression exception ledger. Advisory only; findings remain visible.",
)
@click.option(
    "--output",
    default="data/assumption_gate_receipt.json",
    show_default=True,
    help="JSON receipt output path.",
)
@click.option(
    "--comment-out",
    default="data/assumption_gate_comment.md",
    show_default=True,
    help="Markdown PR comment output path.",
)
def assumption_gate(
    dbt_manifest,
    changed_files,
    changed_files_blob,
    changed_diff,
    mode,
    table_sizes,
    cost_profiles,
    warehouse_history,
    replay_fixtures,
    dbt_catalog,
    dbt_run_results,
    project_dir,
    criticality_registry,
    exceptions_file,
    output,
    comment_out,
):
    """Run the focused dbt Assumption Gate: hidden SQL assumptions + blast radius + receipt."""
    from semzero.integrations.dbt_assumption_gate import (
        DbtAssumptionGate,
        load_table_sizes,
        load_cost_profiles,
        load_business_criticality,
        load_assumption_exceptions,
        load_warehouse_history,
        load_replay_fixtures,
        render_pr_comment,
    )
    from semzero.repo_understanding.dbt_repo_snapshot import write_dbt_repo_snapshot

    expanded: list[str] = []
    expanded.extend([item for item in changed_files if str(item).strip()])
    if changed_files_blob:
        for raw in changed_files_blob.replace(",", "\n").splitlines():
            raw = raw.strip()
            if raw:
                expanded.append(raw)
    if not expanded:
        raise click.ClickException("Provide at least one --changed-file or --changed-files entry.")

    criticality_payload = load_business_criticality(criticality_registry or None)
    gate = DbtAssumptionGate(
        dbt_manifest,
        table_sizes=load_table_sizes(table_sizes or None),
        cost_profiles=load_cost_profiles(cost_profiles or None),
        warehouse_history=load_warehouse_history(warehouse_history or None),
        replay_fixtures=load_replay_fixtures(replay_fixtures or None),
        criticality_registry=criticality_payload,
        exceptions=load_assumption_exceptions(exceptions_file or None),
        catalog_path=dbt_catalog or None,
        run_results_path=dbt_run_results or None,
        project_dir=project_dir or None,
    )
    diff_text = ""
    if changed_diff:
        diff_path = Path(changed_diff)
        if diff_path.exists() and diff_path.is_file():
            diff_text = diff_path.read_text(encoding="utf-8", errors="ignore")
        else:
            diff_text = changed_diff
    receipt = gate.run(expanded, mode=mode, changed_diff=diff_text)
    receipt_payload = receipt.to_dict()

    try:
        from semzero.repo_understanding.causality import attach_causality_to_receipt_payload

        receipt_payload = attach_causality_to_receipt_payload(
            receipt_payload,
            repo_snapshot=None,
        )
    except Exception as exc:
        receipt_payload.setdefault("summary", {})["causality_summary"] = {
            "kind": "semzero_causality_summary_v1",
            "status": "CAUSALITY_ERROR",
            "message": str(exc),
        }

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        json.dumps(receipt_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    comment = render_pr_comment(receipt)
    Path(comment_out).parent.mkdir(parents=True, exist_ok=True)
    Path(comment_out).write_text(comment, encoding="utf-8")

    click.echo("\n  SemZero Assumption Gate")
    click.echo(f"  Verdict: {receipt.verdict}  |  Mode: {mode}")
    click.echo(f"  Findings: {receipt_payload['summary']['finding_count']}")
    click.echo(f"  Changed resources: {receipt_payload['summary']['changed_resource_count']}")
    click.echo(f"  Blast radius resources: {receipt_payload['summary']['blast_radius_resource_count']}")
    cost = receipt_payload["summary"].get("estimated_extra_cost_per_run_usd")
    if cost is not None:
        monthly = receipt_payload["summary"].get("estimated_extra_cost_per_month_usd")
        suffix = f" | ${monthly}/month" if monthly is not None else ""
        click.echo(f"  Rough cost exposure: ${cost}/run{suffix}")
    click.echo(f"  Receipt → {output}")
    click.echo(f"  PR comment → {comment_out}\n")



@cli.command("repo-index")
@click.option(
    "--dbt-manifest",
    default="target/manifest.json",
    show_default=True,
    help="Path to dbt target/manifest.json.",
)
@click.option(
    "--output",
    default="data/semzero_repo_snapshot.json",
    show_default=True,
    help="Where to write the repo snapshot JSON.",
)
@click.option(
    "--repo",
    default="",
    help="Repository identifier. Defaults to GITHUB_REPOSITORY or unknown.",
)
@click.option(
    "--project-dir",
    default=".",
    show_default=True,
    help="Repository root for git metadata.",
)
@click.option(
    "--criticality-registry",
    default="",
    help="Optional JSON/YAML registry mapping dbt nodes to sensitivity labels.",
)
def repo_index_cmd(dbt_manifest, output, repo, project_dir, criticality_registry):
    """Build a SemZero repo-understanding snapshot from a dbt manifest."""
    from semzero.integrations.dbt_assumption_gate import load_business_criticality
    from semzero.repo_understanding.dbt_repo_snapshot import write_dbt_repo_snapshot

    manifest = Path(dbt_manifest)
    if not manifest.exists():
        raise click.ClickException(f"dbt manifest not found: {dbt_manifest}")

    snapshot = write_dbt_repo_snapshot(
        manifest,
        output,
        repo=repo or os.environ.get("GITHUB_REPOSITORY", "unknown"),
        repo_root=project_dir,
        criticality_registry=load_business_criticality(criticality_registry or None),
    )

    summary = snapshot.get("summary", {})
    click.echo("\n  SemZero Repo Snapshot")
    click.echo(f"  Resources indexed: {summary.get('indexed_resource_count', 0)}")
    click.echo(f"  Models: {summary.get('model_count', 0)}")
    click.echo(f"  Tests: {summary.get('test_count', 0)}")
    click.echo(f"  Inferred contracts: {summary.get('dependency_contract_count', 0)}")
    click.echo(f"  Snapshot → {output}\n")



@cli.command("assumption-ci")
@click.option(
    "--dbt-manifest",
    default="target/manifest.json",
    show_default=True,
    help="Path to dbt target/manifest.json.",
)
@click.option(
    "--base-ref",
    default="",
    help="Git base ref for PR diff, e.g. origin/main. Defaults to GITHUB_BASE_REF or origin/main.",
)
@click.option(
    "--changed-files",
    default="",
    help="Optional comma/newline-separated changed files. If omitted, git diff is used.",
)
@click.option(
    "--changed-diff",
    default="",
    help="Optional unified diff text/file. If omitted, git diff is used.",
)
@click.option(
    "--mode",
    default="shadow",
    show_default=True,
    type=click.Choice(["shadow", "advisory", "require-review"]),
)
@click.option(
    "--table-sizes", default="", help="Optional JSON table-size metadata for rough cost estimates."
)
@click.option(
    "--cost-profiles",
    default="",
    help="Optional JSON/YAML warehouse-aware cost profiles for Snowflake/Databricks/dbt monthly exposure estimates.",
)
@click.option(
    "--warehouse-history",
    default="",
    help="Optional offline Snowflake query_history / Databricks job-run / dbt runtime CSV or JSON export for cost calibration without live credentials.",
)
@click.option(
    "--replay-fixtures",
    default="",
    help="Optional JSON fixture/sample data for Assumption Validation Replay Lite. Non-blocking; not full warehouse replay.",
)
@click.option(
    "--dbt-catalog",
    default="",
    help="Optional dbt target/catalog.json for column metadata and richer artifact context.",
)
@click.option(
    "--dbt-run-results",
    default="",
    help="Optional dbt target/run_results.json for model timing/status context.",
)
@click.option(
    "--project-dir",
    default="",
    help="Optional dbt project root for resolving compiled_path entries from manifest.json.",
)
@click.option(
    "--criticality-registry",
    default="",
    help="Optional JSON/YAML registry mapping dbt nodes to business criticality labels/severities.",
)
@click.option(
    "--exceptions-file",
    default="",
    help="Optional JSONL accepted-risk/suppression exception ledger. Defaults to output-dir/assumption_exceptions.jsonl when present.",
)
@click.option(
    "--output-dir",
    default="data/semzero_assumption_gate",
    show_default=True,
    help="Directory for receipt/comment/changed-file artifacts.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit non-zero when verdict is REQUIRE_REVIEW. Off by default for shadow adoption.",
)
@click.option(
    "--write-github-summary/--no-write-github-summary",
    default=True,
    show_default=True,
    help="Append the PR comment to GITHUB_STEP_SUMMARY when available.",
)
@click.option(
    "--write-shadow-ranking/--no-write-shadow-ranking",
    default=True,
    show_default=True,
    help="Write shadow-only hypothesis ranking artifacts without changing the production PR comment.",
)
def assumption_ci(
    dbt_manifest,
    base_ref,
    changed_files,
    changed_diff,
    mode,
    table_sizes,
    cost_profiles,
    warehouse_history,
    replay_fixtures,
    dbt_catalog,
    dbt_run_results,
    project_dir,
    criticality_registry,
    exceptions_file,
    output_dir,
    strict,
    write_github_summary,
    write_shadow_ranking,
):
    """CI wrapper for the focused dbt Assumption Gate.

    This command discovers changed dbt files from a PR diff, runs `assumption-gate`,
    writes stable artifacts, and appends the same PR-ready comment to the GitHub
    Step Summary. It intentionally stays core-only: dbt Assumption Gate, typed
    receipts, blast radius, and shadow/advisory workflow.
    """
    import subprocess
    from semzero.integrations.dbt_assumption_gate import (
        DbtAssumptionGate,
        load_table_sizes,
        load_cost_profiles,
        load_business_criticality,
        load_assumption_exceptions,
        load_warehouse_history,
        load_replay_fixtures,
        render_pr_comment,
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(dbt_manifest)

    def _split(blob: str) -> list[str]:
        found: list[str] = []
        for raw in blob.replace(",", "\n").splitlines():
            raw = raw.strip()
            if raw:
                found.append(raw)
        return found

    def _run_git(args: list[str]) -> str:
        try:
            return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""

    def _write_ci_status_artifacts(
        status: str,
        reason: str,
        message: str,
        required_fix: list[str],
    ) -> None:
        if status not in {"ANALYSIS_INCOMPLETE", "CONFIG_ERROR"}:
            raise click.ClickException(f"Internal SemZero status writer received invalid status: {status}")

        payload = {
            "receipt_kind": f"dbt_assumption_gate_ci_{status.lower()}_v1",
            "schema_version": "semzero.evidence.v1",
            "adapter": "dbt_assumption_gate",
            "domain": "data",
            "mode": mode,
            "verdict": status,
            "summary": {
                "finding_count": 0,
                "changed_resource_count": 0,
                "blast_radius_resource_count": 0,
                "analysis_status": {
                    "status": status,
                    "reason": reason,
                    "message": message,
                    "required_fix": required_fix,
                    "effective_base": effective_base,
                    "github_event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
                    "github_base_ref": os.environ.get("GITHUB_BASE_REF", ""),
                    "github_sha": os.environ.get("GITHUB_SHA", ""),
                },
            },
            "changed_files": files,
            "findings": [],
        }

        _save_json(payload, str(out / "receipt.json"))

        if status == "CONFIG_ERROR":
            title = "SemZero configuration prevented a safe review."
            meaning = (
                "SemZero did **not** complete a trustworthy dbt assumption review because required project/configuration "
                "inputs were missing or invalid."
            )
        else:
            title = "SemZero could not prove this PR is safe."
            meaning = (
                "SemZero did **not** find proof that this change is safe. It did not have enough reliable changed-file "
                "context to make a normal merge recommendation."
            )

        lines = [
            "<!-- semzero-assumption-gate -->",
            "## SemZero Assumption Gate",
            "",
            f"**{title}**",
            "",
            f"Verdict: `{status}` · Mode: `{mode}`",
            f"Reason: `{reason}`",
            "",
            message,
            "",
            "### What this means",
            "",
            meaning,
            "",
            "### Required fix",
            "",
        ]
        for item in required_fix:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("_Full diagnostic context is preserved in the JSON receipt artifact._")

        (out / "comment.md").write_text("\n".join(lines), encoding="utf-8")
        (out / "changed_files.txt").write_text("\n".join(files), encoding="utf-8")
        (out / "changed_files.status.json").write_text(
            json.dumps(payload["summary"]["analysis_status"], indent=2, sort_keys=True),
            encoding="utf-8",
        )

        click.echo("\n  SemZero Assumption CI")
        click.echo(f"  Verdict: {status}")
        click.echo(f"  Reason: {reason}")
        click.echo(f"  Artifacts → {out}\n")


    def _write_analysis_incomplete_artifacts(reason: str, message: str, required_fix: list[str]) -> None:
        _write_ci_status_artifacts(
            status="ANALYSIS_INCOMPLETE",
            reason=reason,
            message=message,
            required_fix=required_fix,
        )


    def _write_config_error_artifacts(reason: str, message: str, required_fix: list[str]) -> None:
        _write_ci_status_artifacts(
            status="CONFIG_ERROR",
            reason=reason,
            message=message,
            required_fix=required_fix,
        )


    files = _split(changed_files)
    effective_base = base_ref or (
        f"origin/{os.environ.get('GITHUB_BASE_REF')}"
        if os.environ.get("GITHUB_BASE_REF")
        else "origin/main"
    )

    if not manifest_path.exists():
        _write_config_error_artifacts(
            reason="dbt_manifest_missing",
            message=(
                f"dbt manifest was not found at `{dbt_manifest}`. SemZero cannot map changed files to dbt resources "
                "without a manifest."
            ),
            required_fix=[
                "Run `dbt compile` before SemZero, or pass --dbt-manifest pointing to a valid manifest.json.",
                "If using the GitHub Action, keep run-dbt-compile enabled or provide an existing target/manifest.json.",
                "Check dbt_compile.log in the SemZero artifact for the compile failure root cause.",
            ],
        )
        return

    if not files:
        name_blob = _run_git(["git", "diff", "--name-only", f"{effective_base}...HEAD"])
        files = _split(name_blob)

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    in_pull_request = event_name == "pull_request" or bool(os.environ.get("GITHUB_BASE_REF"))

    if not files and in_pull_request:
        _write_analysis_incomplete_artifacts(
            reason="changed_file_discovery_empty_in_pull_request",
            message=(
                "Changed-file discovery returned no files in pull_request context. "
                "SemZero cannot distinguish a truly empty PR from a shallow-checkout or base-ref discovery failure."
            ),
            required_fix=[
                "Use actions/checkout@v4 with fetch-depth: 0.",
                "Ensure the PR base ref is fetched before running SemZero.",
                "Check changed_files.debug.txt and changed_files.status.json in the SemZero artifact.",
            ],
        )
        return

    dbt_like = [
        item
        for item in files
        if item.endswith((".sql", ".yml", ".yaml"))
        and (
            item.startswith(("models/", "macros/", "snapshots/", "seeds/"))
            or item == "dbt_project.yml"
        )
    ]
    if not dbt_like:
        # Keep a zero-finding receipt-like artifact so CI has something auditable.
        payload = {
            "receipt_kind": "dbt_assumption_gate_ci_noop_v1",
            "schema_version": "semzero.evidence.v1",
            "adapter": "dbt_assumption_gate",
            "domain": "data",
            "mode": mode,
            "verdict": "ALLOW",
            "summary": {
                "finding_count": 0,
                "changed_resource_count": 0,
                "blast_radius_resource_count": 0,
                "analysis_status": {
                    "status": "COMPLETE",
                    "reason": "no_dbt_like_files_changed",
                    "message": "Changed files were discovered, but none matched the focused dbt Assumption Gate scope.",
                },
            },
            "changed_files": files,
            "findings": [],
            "note": "No changed dbt SQL/YAML files matched the focused Assumption Gate scope.",
        }
        _save_json(payload, str(out / "receipt.json"))
        comment = "## SemZero Assumption Gate\n\nVerdict: `ALLOW`\n\nNo changed dbt SQL/YAML files matched the focused Assumption Gate scope.\n"
        (out / "comment.md").write_text(comment, encoding="utf-8")
        (out / "changed_files.txt").write_text("\n".join(files), encoding="utf-8")
        click.echo("\n  SemZero Assumption CI")
        click.echo("  Verdict: ALLOW")
        click.echo("  Findings: 0")
        click.echo(f"  Artifacts → {out}\n")
        return

    diff_text = ""
    if changed_diff:
        diff_path = Path(changed_diff)
        diff_text = (
            diff_path.read_text(encoding="utf-8", errors="ignore")
            if diff_path.exists() and diff_path.is_file()
            else changed_diff
        )
    else:
        diff_text = _run_git(["git", "diff", f"{effective_base}...HEAD", "--", *dbt_like])

    def _read_changed_sql_pairs(paths: list[str]) -> list[tuple[str, str, str]]:
        root = Path(project_dir or ".")
        pairs: list[tuple[str, str, str]] = []
        for item in paths:
            if not item.endswith(".sql"):
                continue
            after_path = root / item
            try:
                after_sql = after_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                after_sql = ""
            before_sql = _run_git(["git", "show", f"{effective_base}:{item}"])
            if before_sql or after_sql:
                pairs.append((item, before_sql, after_sql))
        return pairs

    criticality_payload = load_business_criticality(criticality_registry or None)

    try:
        from semzero.repo_understanding.dbt_repo_snapshot import write_dbt_repo_snapshot

        snapshot = write_dbt_repo_snapshot(
            manifest_path,
            out / "repo_snapshot.json",
            repo=os.environ.get("GITHUB_REPOSITORY", "unknown"),
            repo_root=project_dir or ".",
            criticality_registry=criticality_payload,
        )
        click.echo(
            f"  Repo snapshot: {snapshot.get('summary', {}).get('indexed_resource_count', 0)} resource(s), "
            f"{snapshot.get('summary', {}).get('dependency_contract_count', 0)} inferred contract(s)"
        )
    except Exception as exc:
        snapshot_error = {
            "status": "SNAPSHOT_ERROR",
            "message": str(exc),
        }
        (out / "repo_snapshot.error.json").write_text(
            json.dumps(snapshot_error, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        click.echo(f"  Repo snapshot: failed ({exc})")

    effective_exceptions = exceptions_file or str(out / "assumption_exceptions.jsonl")
    gate = DbtAssumptionGate(
        manifest_path,
        table_sizes=load_table_sizes(table_sizes or None),
        cost_profiles=load_cost_profiles(cost_profiles or None),
        warehouse_history=load_warehouse_history(warehouse_history or None),
        replay_fixtures=load_replay_fixtures(replay_fixtures or None),
        criticality_registry=load_business_criticality(criticality_registry or None),
        exceptions=load_assumption_exceptions(
            effective_exceptions if Path(effective_exceptions).exists() else None
        ),
        catalog_path=dbt_catalog or None,
        run_results_path=dbt_run_results or None,
        project_dir=project_dir or None,
    )
    receipt = gate.run(dbt_like, mode=mode, changed_diff=diff_text)
    payload = receipt.to_dict()

    try:
        from semzero.repo_understanding.causality import attach_causality_to_receipt_payload

        repo_snapshot_path = out / "repo_snapshot.json"
        repo_snapshot_payload = None
        if repo_snapshot_path.exists():
            repo_snapshot_payload = json.loads(repo_snapshot_path.read_text(encoding="utf-8"))

        payload = attach_causality_to_receipt_payload(
            payload,
            repo_snapshot=repo_snapshot_payload,
        )
    except Exception as exc:
        payload.setdefault("summary", {})["causality_summary"] = {
            "kind": "semzero_causality_summary_v1",
            "status": "CAUSALITY_ERROR",
            "message": str(exc),
        }

    receipt_path = out / "receipt.json"
    comment_path = out / "comment.md"
    changed_path = out / "changed_files.txt"
    diff_path = out / "changed.diff"
    _save_json(payload, str(receipt_path))
    comment = render_pr_comment(receipt)
    comment_path.write_text(comment, encoding="utf-8")
    changed_path.write_text("\n".join(dbt_like), encoding="utf-8")
    diff_path.write_text(diff_text, encoding="utf-8")

    if write_shadow_ranking:
        try:
            from semzero.repo_understanding.hypothesis_ranking import (
                estimate_rewrite_stats,
                write_shadow_ranking_artifacts,
            )
            from semzero.repo_understanding.sql_semantic_diff import (
                extract_sql_semantic_events,
            )

            semantic_events = []
            rewrite_stats_payloads = []
            for path, before_sql, after_sql in _read_changed_sql_pairs(dbt_like):
                events = extract_sql_semantic_events(
                    before_sql,
                    after_sql,
                    model=Path(path).stem,
                )
                semantic_events.extend(events)
                rewrite_stats_payloads.append(
                    estimate_rewrite_stats(
                        before_sql,
                        after_sql,
                        joins_changed=sum(
                            1
                            for event in events
                            if event.event_type
                            in {
                                "join_added",
                                "join_removed",
                                "join_type_changed",
                                "join_key_changed",
                                "join_target_changed",
                                "join_predicate_weakened",
                            }
                        ),
                    ).to_dict()
                )

            aggregate_rewrite_stats: dict[str, Any] = {}
            if rewrite_stats_payloads:
                keys = set().union(*(item.keys() for item in rewrite_stats_payloads))
                aggregate_rewrite_stats = {
                    key: max(float(item.get(key) or 0.0) for item in rewrite_stats_payloads)
                    for key in keys
                }
                aggregate_rewrite_stats["joins_changed"] = int(
                    max(int(item.get("joins_changed") or 0) for item in rewrite_stats_payloads)
                )

            repo_snapshot_payload = None
            repo_snapshot_path = out / "repo_snapshot.json"
            if repo_snapshot_path.exists():
                repo_snapshot_payload = json.loads(
                    repo_snapshot_path.read_text(encoding="utf-8")
                )

            shadow = write_shadow_ranking_artifacts(
                out,
                payload,
                comment,
                semantic_events,
                repo_snapshot_payload,
                rewrite_stats=aggregate_rewrite_stats or None,
            )
            click.echo(
                "  Shadow ranking: "
                f"{shadow['shadow_hypothesis_receipt'].get('analysis_outcome')} "
                f"primary={shadow['shadow_hypothesis_receipt'].get('primary_family') or 'none'}"
            )
        except Exception as exc:
            shadow_error = {
                "kind": "semzero_shadow_hypothesis_error_v1",
                "shadow_only": True,
                "status": "SHADOW_RANKING_ERROR",
                "message": str(exc),
            }
            (out / "shadow_hypothesis_receipt.error.json").write_text(
                json.dumps(shadow_error, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            click.echo(f"  Shadow ranking: failed ({exc})")

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if write_github_summary and summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write("\n" + comment + "\n")

    click.echo("\n  SemZero Assumption CI")
    click.echo(f"  Verdict: {receipt.verdict}  |  Mode: {mode}")
    click.echo(f"  Changed dbt files: {len(dbt_like)}")
    click.echo(f"  Findings: {payload['summary']['finding_count']}")
    click.echo(f"  Receipt → {receipt_path}")
    click.echo(f"  PR comment → {comment_path}")
    click.echo(f"  Changed files → {changed_path}\n")

    if strict and receipt.verdict == "REQUIRE_REVIEW":
        raise click.ClickException("SemZero Assumption Gate requires review in --strict mode.")


@cli.command("assumption-feedback")
@click.option(
    "--receipt",
    required=True,
    help="Path or identifier of the assumption-gate receipt being reviewed.",
)
@click.option(
    "--finding-id",
    default="",
    help="Optional finding id; stable IDs from PR comments are preferred, legacy AG-...-001 IDs still work.",
)
@click.option(
    "--stable-finding-id",
    default="",
    help="Optional stable finding id from receipt/comment. Stored separately for calibration.",
)
@click.option(
    "--family",
    default="",
    help="Optional assumption family, useful for coarse feedback when a finding id is unavailable.",
)
@click.option(
    "--disposition",
    required=True,
    type=click.Choice(
        [
            "agree",
            "disagree",
            "false_positive",
            "false_negative",
            "needs_context",
            "fixed",
            "accepted_risk",
        ]
    ),
    help="Developer feedback disposition.",
)
@click.option("--reviewer", default="", help="Reviewer name/email/handle.")
@click.option("--comment", default="", help="Optional feedback note.")
@click.option("--pr", default="", help="Optional pull request number or URL.")
@click.option("--repository", default="", help="Optional repository name.")
@click.option(
    "--feedback-file",
    default="data/assumption_feedback.jsonl",
    show_default=True,
    help="JSONL feedback ledger path.",
)
def assumption_feedback_cmd(
    receipt,
    finding_id,
    stable_finding_id,
    family,
    disposition,
    reviewer,
    comment,
    pr,
    repository,
    feedback_file,
):
    """Record developer feedback for an Assumption Gate receipt/finding."""
    from .reliability.assumption_feedback import AssumptionFeedbackRecord, append_feedback

    row = append_feedback(
        AssumptionFeedbackRecord(
            receipt=receipt,
            finding_id=finding_id,
            stable_finding_id=stable_finding_id,
            family=family,
            disposition=disposition,
            reviewer=reviewer,
            comment=comment,
            pr=pr,
            repository=repository,
        ),
        feedback_file,
    )
    click.echo("\n  SemZero Assumption Feedback")
    click.echo(f"  Disposition: {row['disposition']}")
    if stable_finding_id:
        click.echo(f"  Stable finding: {stable_finding_id}")
    if finding_id:
        click.echo(f"  Finding: {finding_id}")
    click.echo(f"  Feedback ledger → {feedback_file}\n")


@cli.command("assumption-exception")
@click.option(
    "--scope",
    default="stable_id",
    show_default=True,
    type=click.Choice(["stable_id", "family", "source", "receipt", "global"]),
    help="Exception match scope.",
)
@click.option(
    "--value",
    default="",
    help="Stable ID/family/source/receipt value to match. Not required for scope=global.",
)
@click.option("--reason", required=True, help="Required reason for accepted risk or suppression.")
@click.option("--owner", default="", help="Owner responsible for revisiting the exception.")
@click.option(
    "--expires-at",
    default="",
    help="Optional ISO expiry timestamp/date. Expired exceptions are surfaced in dashboards.",
)
@click.option("--created-by", default="", help="Person creating the exception.")
@click.option("--ticket", default="", help="Optional ticket/change-request URL or ID.")
@click.option(
    "--exceptions-file",
    default="data/assumption_exceptions.jsonl",
    show_default=True,
    help="JSONL exception ledger path.",
)
def assumption_exception_cmd(
    scope, value, reason, owner, expires_at, created_by, ticket, exceptions_file
):
    """Record a reason-required, optional-expiry Assumption Gate exception.

    Exceptions are advisory and auditable. They annotate matching findings as
    accepted/suppressed but do not delete evidence or create hard blocks.
    """
    from .reliability.assumption_exceptions import AssumptionExceptionRecord, append_exception

    row = append_exception(
        AssumptionExceptionRecord(
            scope=scope,
            value=value,
            reason=reason,
            owner=owner,
            expires_at=expires_at,
            created_by=created_by,
            ticket=ticket,
        ),
        exceptions_file,
    )
    click.echo("\n  SemZero Assumption Exception")
    click.echo(f"  Scope: {row['scope']} = {row.get('value', '')}")
    click.echo(f"  Status: {row['status']}")
    if row.get("expires_at"):
        click.echo(f"  Expires: {row['expires_at']}")
    click.echo(f"  Exception ledger → {exceptions_file}\n")


@cli.command()
@click.option(
    "--receipt",
    "receipt_path",
    default="",
    help="Path to a SemZero receipt or bundle JSON. Defaults to auto-detect.",
)
@click.option(
    "--search-dir",
    default="data",
    show_default=True,
    help="Directory used to auto-detect current SemZero artifacts.",
)
@click.option(
    "--stale-after-hours",
    default=12.0,
    show_default=True,
    type=float,
    help="Mark receipts as stale after this many hours.",
)
@click.option(
    "--write-receipt",
    default="",
    show_default=False,
    help="Optional path to materialize a composite receipt when only separate reports exist.",
)
def check(receipt_path, search_dir, stale_after_hours, write_receipt):
    """Show the best current SemZero verdict without rerunning expensive validation by default."""
    from semzero.receipt_tools import load_or_autodetect_receipt, summarize_receipt

    receipt = load_or_autodetect_receipt(
        receipt_path or None, search_dir=search_dir, write_composite_to=write_receipt or None
    )
    if stale_after_hours != 12.0:
        receipt = summarize_receipt(
            receipt.payload, receipt.path, stale_after_hours=stale_after_hours
        )

    age = "unknown" if receipt.age_hours is None else f"{receipt.age_hours:.1f}h"
    click.echo(f"\n  Verdict: {receipt.verdict}")
    click.echo(f"  Receipt kind: {receipt.kind}")
    click.echo(f"  Evidence completeness: {receipt.evidence_completeness}")
    click.echo(f"  Confidence: {receipt.confidence}")
    click.echo(f"  Freshness: {receipt.freshness} (age={age})")
    click.echo(f"  Evidence source: {receipt.path}")
    root_cause = receipt.summary.get("root_cause")
    if root_cause:
        click.echo(f"  Reason: {root_cause}")
    if receipt.summary.get("queries_replayed") is not None:
        click.echo(
            f"  Replay: {receipt.summary.get('queries_replayed', 0)} query(s), {receipt.summary.get('queries_broken', 0)} broken"
        )
    if receipt.summary.get("mutations_that_broke") is not None:
        click.echo(
            f"  Chaos: {receipt.summary.get('mutations_that_broke', 0)} breaking mutation(s)"
        )
    next_steps = receipt.summary.get("recommended_action") or []
    if next_steps:
        click.echo("  Next step:")
        for item in next_steps[:2]:
            click.echo(f"    - {item}")
    click.echo()


@cli.command()
@click.option(
    "--receipt",
    "receipt_path",
    default="",
    help="Path to a SemZero receipt or bundle JSON. Defaults to auto-detect.",
)
@click.option("--search-dir", default="data", show_default=True)
@click.option(
    "--write-receipt",
    default="",
    show_default=False,
    help="Optional path to materialize a composite receipt when only separate reports exist.",
)
def explain(receipt_path, search_dir, write_receipt):
    """Explain why the current SemZero receipt reached its verdict."""
    from semzero.receipt_tools import load_or_autodetect_receipt

    receipt = load_or_autodetect_receipt(
        receipt_path or None, search_dir=search_dir, write_composite_to=write_receipt or None
    )
    click.echo(f"\n  Explaining: {receipt.path}")
    click.echo(
        f"  Verdict: {receipt.verdict}  |  Kind: {receipt.kind}  |  Confidence: {receipt.confidence}"
    )
    click.echo(f"  Freshness: {receipt.freshness}")
    root_cause = receipt.summary.get("root_cause")
    if root_cause:
        click.echo(f"\n  Primary reason:\n    {root_cause}")
    payload = receipt.payload
    gate = payload.get("gate_result", payload if receipt.kind == "gate_result" else {}) or {}
    blocked = gate.get("blocked_by") or []
    review = gate.get("review_reasons") or []
    if blocked:
        click.echo("\n  Blocking issues:")
        for item in blocked[:5]:
            click.echo(f"    - {item}")
    if review:
        click.echo("\n  Review reasons:")
        for item in review[:5]:
            click.echo(f"    - {item}")
    if receipt.artifact_paths:
        click.echo("\n  Linked artifacts:")
        for key, value in sorted(receipt.artifact_paths.items()):
            click.echo(f"    - {key}: {value}")
    click.echo()


@cli.command()
@click.option(
    "--left",
    "left_path",
    default="",
    help="Left receipt/bundle JSON. Defaults to auto-detect current receipt.",
)
@click.option(
    "--right", "right_path", required=True, help="Right receipt/bundle JSON to compare against."
)
@click.option("--search-dir", default="data", show_default=True)
def compare(left_path, right_path, search_dir):
    """Compare two SemZero receipts or report bundles to prove what changed."""
    from semzero.receipt_tools import compare_receipts, load_or_autodetect_receipt

    left = load_or_autodetect_receipt(left_path or None, search_dir=search_dir)
    right = load_or_autodetect_receipt(right_path, search_dir=search_dir)
    delta = compare_receipts(left, right)

    click.echo(f"\n  Left:  {delta['left_path']}  ({delta['left_verdict']})")
    click.echo(f"  Right: {delta['right_path']}  ({delta['right_verdict']})")
    click.echo(f"  Verdict changed: {'yes' if delta['verdict_changed'] else 'no'}")
    click.echo(f"  Confidence: {delta['left_confidence']} -> {delta['right_confidence']}")
    if delta["left_root_cause"] or delta["right_root_cause"]:
        click.echo(f"  Root cause: {delta['left_root_cause']} -> {delta['right_root_cause']}")
    if delta["changed_fields"]:
        click.echo("\n  Changed metrics:")
        for field in delta["changed_fields"]:
            entry = delta["deltas"][field]
            click.echo(f"    - {field}: {entry['left']} -> {entry['right']}")
    else:
        click.echo("\n  No tracked metric deltas were detected.")
    click.echo()


@cli.command()
@click.option(
    "--mode",
    type=click.Choice(["premerge", "validation"]),
    default="premerge",
    show_default=True,
    help="High-level recheck wrapper mode.",
)
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--drift", default="data/drift_report.json", show_default=True)
@click.option("--db-url", envvar="SEMZERO_DB_URL", default="")
@click.option("--migration", default="", help="Migration SQL file or inline SQL.")
@click.option(
    "--proof-path", "proof_paths", multiple=True, help="SQL/Python path to scan for AST proofing."
)
@click.option(
    "--run-chaos",
    is_flag=True,
    default=False,
    help="Enable targeted chaos when using premerge mode.",
)
@click.option(
    "--live-mode",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "clone", "metadata-only"]),
)
@click.option("--demo-pack-dir", default="", help="Validation pack directory for validation mode.")
@click.option(
    "--demo-scale",
    default="medium",
    show_default=True,
    type=click.Choice(["small", "medium", "large", "xlarge"]),
)
@click.option(
    "--demo-profile",
    default="standard",
    show_default=True,
    type=click.Choice(["standard", "messy", "finance", "chaos_labyrinth", "black_swan"]),
)
@click.option(
    "--output",
    default="data/recheck_bundle.json",
    show_default=True,
    help="Bundle/report output path.",
)
@click.pass_context
def recheck(
    ctx,
    mode,
    graph,
    drift,
    db_url,
    migration,
    proof_paths,
    run_chaos,
    live_mode,
    demo_pack_dir,
    demo_scale,
    demo_profile,
    output,
):
    """Run a fresh high-level SemZero validation wrapper while preserving the expert engine commands underneath."""
    if mode == "premerge":
        report_dir = str(Path(output).parent)
        return ctx.invoke(
            premerge,
            graph=graph,
            drift=drift,
            db_url=db_url,
            migration=migration,
            proof_paths=proof_paths,
            dbt_manifest=None,
            dbt_catalog=None,
            dbt_run_results=None,
            openlineage_paths=(),
            airflow_paths=(),
            dagster_paths=(),
            looker_paths=(),
            montecarlo_paths=(),
            rgcn_model="",
            run_chaos=run_chaos,
            live_mode=live_mode,
            output=output,
            shadow=False,
        )

    markdown_out = str(Path(output).with_suffix(".md"))
    html_out = str(Path(output).with_suffix(".html"))
    return ctx.invoke(
        validate_e2e,
        db_url=db_url,
        graph=graph,
        drift=drift,
        migration=migration,
        proof_paths=proof_paths,
        workload_query_files=(),
        demo_pack_dir=demo_pack_dir,
        demo_scale=demo_scale,
        demo_profile=demo_profile,
        demo_backend="sqlite",
        source_schema="public",
        scenarios=(),
        output=output,
        markdown_out=markdown_out,
        html_out=html_out,
    )


@cli.command()
@click.option(
    "--receipt",
    "receipt_path",
    default="",
    help="Path to a SemZero receipt or bundle JSON. Defaults to auto-detect.",
)
@click.option("--search-dir", default="data", show_default=True)
@click.option("--output", default="", show_default=False, help="Optional markdown output path")
def fix(receipt_path, search_dir, output):
    """Summarize the safest next fixes or rollback steps from the current receipt."""
    from semzero.receipt_tools import load_or_autodetect_receipt, render_receipt_markdown

    receipt = load_or_autodetect_receipt(receipt_path or None, search_dir=search_dir)
    summary = receipt.summary
    lines = [
        "# SemZero Fix Guidance",
        "",
        f"- Verdict: **{receipt.verdict}**",
        f"- Receipt: `{receipt.path}`",
        "",
    ]
    root = summary.get("root_cause")
    if root:
        lines += ["## Problem", "", f"- {root}", ""]
    next_actions = summary.get("recommended_action") or []
    if not next_actions:
        payload = receipt.payload
        gate = payload.get("gate_result", payload if receipt.kind == "gate_result" else {}) or {}
        next_actions = (
            gate.get("next_actions") or gate.get("blocked_by") or gate.get("review_reasons") or []
        )
    lines += ["## Recommended next steps", ""]
    if next_actions:
        lines.extend(f"- {item}" for item in next_actions[:8])
    else:
        lines.append(
            "- Re-run the narrowest safe validation layer after applying the directly impacted fixes."
        )
    if summary.get("queries_broken"):
        lines += [
            "",
            "## Rollback / replay guidance",
            "",
            f"- Re-run Wind Tunnel after patching the first {summary.get('queries_broken')} broken query path(s).",
        ]
    if summary.get("mutations_that_broke"):
        lines.append(
            f"- Address the stateful failure shape surfaced by {summary.get('mutations_that_broke')} breaking mutation(s) before merge."
        )
    rendered = "\n".join(lines).strip() + "\n"
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(rendered, encoding="utf-8")
        click.echo(f"  Fix guidance → {output}")
    click.echo(rendered)


# ── doctor ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--db-url", envvar="SEMZERO_DB_URL", required=True)
@click.option("--dialect", default="auto", show_default=True)
@click.option("--output", default="data/live_readiness.json", show_default=True)
@click.option("--markdown-out", default="data/live_readiness.md", show_default=True)
def doctor(db_url, dialect, output, markdown_out):
    """Check live-environment readiness and recommend the safest SemZero rollout path."""
    from semzero.utils.live_readiness import build_live_readiness_report

    report = build_live_readiness_report(db_url=db_url, dialect=dialect)
    report.save(output)
    Path(markdown_out).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_out).write_text(report.render_markdown(), encoding="utf-8")

    click.echo(f"\n  Dialect: {report.dialect}")
    click.echo(f"  Connectivity: {'OK' if report.connectivity_ok else 'FAILED'}")
    click.echo(f"  Clone support: {'yes' if report.clone_supported else 'no'}")
    click.echo(f"  Recommended live mode: {report.recommended_live_mode}")
    click.echo(f"  Visible tables: {report.table_count}")
    if report.warnings:
        click.echo("\n  Warnings:")
        for item in report.warnings:
            click.echo(f"    - {item}")
    click.echo(f"\n  JSON → {output}")
    click.echo(f"  Markdown → {markdown_out}\n")


# ── scan ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--db-url", envvar="SEMZERO_DB_URL", required=True)
@click.option("--graph-out", default="data/schema_graph.json", show_default=True)
@click.option("--html-out", default="data/scan_report.html", show_default=True)
@click.option("--store", default="data/graph_store.db", show_default=True)
@click.option("--label", default="live_scan", show_default=True)
@click.option("--no-stats", is_flag=True, default=False)
@click.option("--workers", default=8, show_default=True, type=int)
def scan(db_url, graph_out, html_out, store, label, no_stats, workers):
    """One-command live scan: crawl the schema and render a quick HTML report."""
    from semzero.crawler.builder import SchemaGraphBuilder
    from semzero.reporting.reporter import HTMLReporter

    builder = SchemaGraphBuilder(
        db_url,
        collect_stats=not no_stats,
        store_path=store,
        max_workers=workers,
    )
    graph = builder.build(label=label)
    builder.save(graph_out)
    HTMLReporter().generate(graph, None, None, None, output_path=html_out)
    click.echo(f"\n  Graph → {graph_out}")
    click.echo(f"  Report → {html_out}")
    click.echo(f"  Tables: {graph.get('meta', {}).get('table_count', 0)}")
    click.echo(f"  Nodes:  {graph.get('meta', {}).get('node_count', 0)}\n")


# ── crawl ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--db-url", envvar="SEMZERO_DB_URL", required=True)
@click.option("--output", default="data/schema_graph.json", show_default=True)
@click.option("--store", default="data/graph_store.db", show_default=True)
@click.option("--label", default="", help="Snapshot label")
@click.option("--no-stats", is_flag=True, default=False)
@click.option("--workers", default=8, show_default=True, type=int)
def crawl(db_url, output, store, label, no_stats, workers):
    """Crawl a database schema and save a versioned graph snapshot."""
    from semzero.crawler.builder import SchemaGraphBuilder

    click.echo(f"\n  Crawling {db_url[:60]}…\n")
    builder = SchemaGraphBuilder(
        db_url,
        collect_stats=not no_stats,
        store_path=store,
        max_workers=workers,
    )
    graph = builder.build(label=label)
    builder.save(output)
    m = graph["meta"]
    click.echo(f"  ✓ {m.get('table_count', 0)} tables, {m.get('node_count', 0)} nodes → {output}")
    click.echo(f"  Snapshot ID: {graph.get('_snapshot_id', '?')}\n")


# ── diff ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--before", required=True, help="Path to before graph JSON or snapshot ID")
@click.option("--after", required=True, help="Path to after graph JSON or snapshot ID")
@click.option("--output", default="data/drift_report.json", show_default=True)
@click.option("--store", default="data/graph_store.db", show_default=True)
def diff(before, after, output, store):
    """Diff two schema snapshots and produce a typed drift report."""
    from semzero.crawler.drift import SchemaDriftDetector
    from semzero.crawler.graph_store import GraphStore
    from semzero.reporting.reporter import TerminalReporter

    store_obj = GraphStore(store)

    def _load(ref: str) -> dict:
        if ref.isdigit():
            g = store_obj.get_snapshot(int(ref))
            if not g:
                click.echo(f"Snapshot {ref} not found.", err=True)
                sys.exit(1)
            return g
        return _load_json(ref)

    before_graph = _load(before)
    after_graph = _load(after)

    detector = SchemaDriftDetector()
    report = detector.diff(before_graph, after_graph, before_label=before, after_label=after)
    _save_json(report.to_dict(), output)
    TerminalReporter().print_drift_report(report.to_dict())
    click.echo(f"  Drift report → {output}\n")


# ── blast ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--node", required=True)
@click.option("--output", default="data/blast_report.json", show_default=True)
def blast(graph, node, output):
    """Compute blast radius for a changed node."""
    from semzero.analytics.impact import BlastRadiusAnalyzer
    from semzero.reporting.reporter import TerminalReporter

    graph_json = _load_json(graph)
    analyzer = BlastRadiusAnalyzer(graph_json)
    report = analyzer.analyze(changed_node_id=node)
    _save_json(report.to_dict(), output)
    TerminalReporter().print_blast_radius(report.to_dict())
    click.echo(f"  Blast report → {output}\n")


# ── match ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--source", required=True)
@click.option("--target", required=True)
@click.option("--output", default="data/match_report.json", show_default=True)
def match(source, target, output):
    """Match columns between two schema graphs (hybrid lexical + structural)."""
    from semzero.analytics.matcher import SchemaColumnMatcher

    src_graph = _load_json(source)
    tgt_graph = _load_json(target)
    matcher = SchemaColumnMatcher(src_graph, tgt_graph)
    match_rpt = matcher.match()
    result = match_rpt.to_dict()
    matches = match_rpt.auto_mapped + match_rpt.needs_review
    _save_json(result, output)
    click.echo(f"\n  {len(matches)} column matches found → {output}\n")
    for m in matches[:10]:
        click.echo(f"    {m.source_id} → {m.target_id}  ({m.confidence:.0%})")


# ── repair ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--drift", default="data/drift_report.json", show_default=True)
@click.option("--matches", default=None)
@click.option("--output", default="data/repair_plan.json", show_default=True)
@click.option("--sql-out", default="data/migration.sql", show_default=True)
@click.option("--open-pr", is_flag=True, default=False)
@click.option("--repo", envvar="SEMZERO_GITHUB_REPO", default=None)
@click.option("--token", envvar="SEMZERO_GITHUB_TOKEN", default=None)
@click.option("--reviewers", envvar="SEMZERO_REVIEWERS", default="")
@click.option("--slack", envvar="SEMZERO_SLACK_WEBHOOK", default=None)
def repair(drift, matches, output, sql_out, open_pr, repo, token, reviewers, slack):
    """Generate a repair plan and optional migration SQL for detected drift."""
    from semzero.crawler.drift import DriftEvent, ChangeType, Severity
    from semzero.orchestrator.repair import RepairEngine
    from semzero.reporting.reporter import TerminalReporter

    drift_report = _load_json(drift)
    match_data = _load_json(matches) if matches else None

    events = []
    for e in drift_report.get("events", []):
        events.append(
            DriftEvent(
                change_type=ChangeType(e["change_type"]),
                severity=Severity(e["severity"]),
                node_id=e["node_id"],
                before=e.get("before"),
                after=e.get("after"),
                detail=e.get("detail", ""),
            )
        )

    col_matches = []
    if match_data:
        from semzero.analytics.matcher import ColumnMatch

        for m in match_data.get("matches", []):
            col_matches.append(ColumnMatch(**m))

    match_map = {m.source_id: m.target_id for m in col_matches} if col_matches else {}
    engine = RepairEngine(match_map=match_map)
    plan = engine.build_plan(events)
    _save_json(plan.to_dict(), output)

    sql = plan.render_sql_script()
    Path(sql_out).parent.mkdir(parents=True, exist_ok=True)
    Path(sql_out).write_text(sql)

    TerminalReporter().print_repair_plan(plan.to_dict())
    click.echo(f"  Repair plan → {output}")
    click.echo(f"  Migration SQL → {sql_out}\n")

    if open_pr and repo and token:
        from semzero.integrations.github_pr import PRBot

        bot = PRBot(repo=repo, token=token, reviewers=reviewers.split(",") if reviewers else [])
        result = bot.open_pr(drift_report, plan.to_dict(), sql)
        if result.success:
            click.echo(f"  ✓ PR opened: {result.pr_url}")
            if slack:
                from semzero.integrations.slack import SlackAlerter

                SlackAlerter(webhook_url=slack).send_drift_alert(drift_report, result.pr_url)
        else:
            click.echo(f"  PR failed: {result.error}", err=True)


# ── report ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--drift", default=None)
@click.option("--blast", default=None)
@click.option("--repair", default=None)
@click.option(
    "--receipt",
    "receipt_path",
    default="",
    help="Optional receipt / bundle to render instead of the engine HTML report",
)
@click.option(
    "--search-dir",
    default="data",
    show_default=True,
    help="Directory used to auto-detect receipts when --receipt is omitted",
)
@click.option(
    "--format", "fmt", default="html", show_default=True, type=click.Choice(["html", "md", "json"])
)
@click.option("--output", default="data/semzero_report.html", show_default=True)
def report(graph, drift, blast, repair, receipt_path, search_dir, fmt, output):
    """Generate either an engine HTML report or a polished receipt render."""
    from semzero.reporting.reporter import HTMLReporter
    from semzero.receipt_tools import (
        load_or_autodetect_receipt,
        render_receipt_html,
        render_receipt_markdown,
        save_composite_receipt,
    )

    if receipt_path or Path(search_dir).exists():
        try:
            if fmt == "json" and not receipt_path:
                try:
                    composite_path = save_composite_receipt(search_dir=search_dir, output=output)
                    receipt = load_or_autodetect_receipt(composite_path, search_dir=search_dir)
                except FileNotFoundError:
                    receipt = load_or_autodetect_receipt(None, search_dir=search_dir)
            else:
                receipt = load_or_autodetect_receipt(receipt_path or None, search_dir=search_dir)
        except FileNotFoundError:
            receipt = None
        if receipt is not None:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if fmt == "json":
                out_path.write_text(
                    json.dumps(receipt.payload, indent=2, default=str), encoding="utf-8"
                )
            elif fmt == "md":
                out_path.write_text(render_receipt_markdown(receipt), encoding="utf-8")
            else:
                out_path.write_text(render_receipt_html(receipt), encoding="utf-8")
            click.echo(f"\n  Receipt report → {out_path}")
            click.echo(f"  Source:         {receipt.path}")
            click.echo(f"  Verdict:        {receipt.verdict}\n")
            return

    graph_json = _load_json(graph)
    drift_json = _load_json(drift) if drift else None
    blast_json = _load_json(blast) if blast else None
    repair_json = _load_json(repair) if repair else None

    path = HTMLReporter().generate(
        graph_json,
        drift_json,
        blast_json,
        repair_json,
        output_path=output,
    )
    click.echo(f"\n  Report → {path}")
    click.echo(f"  Open:   open {path}\n")


# ── trace (RCA) ────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--node", required=True)
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--store", default="data/graph_store.db", show_default=True)
@click.option("--since", default=None, help="Lookback window e.g. '24h', '7d'. Default: 72h")
@click.option("--output", default="data/rca_report.json", show_default=True)
def trace(node, graph, store, since, output):
    """Root Cause Analysis — trace why a node is broken."""
    from semzero.analytics.rca import RCAAgent

    graph_json = _load_json(graph)
    agent = RCAAgent(graph_json, store_path=store)
    # Parse the since string into hours for the lookback
    if since:
        import re as _re

        m = _re.match(r"(\d+)([hd])", since.lower())
        if m:
            val, unit = int(m.group(1)), m.group(2)
            hours = val if unit == "h" else val * 24
            agent.lookback_hours = hours
    rca_report = agent.investigate(broken_node_id=node)
    _save_json(rca_report.to_dict(), output)
    click.echo(rca_report.explain())
    click.echo(f"\n  RCA report → {output}\n")


# ── watch ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--db-url", envvar="SEMZERO_DB_URL", required=True)
@click.option(
    "--interval", default=3600, show_default=True, type=int, help="Crawl interval in seconds"
)
@click.option("--store", default="data/graph_store.db", show_default=True)
@click.option("--repo", envvar="SEMZERO_GITHUB_REPO", default=None)
@click.option("--token", envvar="SEMZERO_GITHUB_TOKEN", default=None)
@click.option("--reviewers", envvar="SEMZERO_REVIEWERS", default="")
@click.option("--slack", envvar="SEMZERO_SLACK_WEBHOOK", default=None)
@click.option("--channel", envvar="SEMZERO_SLACK_CHANNEL", default="#data-alerts")
@click.option("--no-stats", is_flag=True, default=False)
@click.option("--workers", default=8, type=int, show_default=True)
def watch(db_url, interval, store, repo, token, reviewers, slack, channel, no_stats, workers):
    """Run the autonomous schema watcher daemon."""
    from semzero.scheduler.watcher import SchemaWatcher

    watcher = SchemaWatcher(
        db_url=db_url,
        interval=interval,
        store_path=store,
        github_repo=repo or "",
        github_token=token or "",
        github_reviewers=reviewers.split(",") if reviewers else [],
        slack_webhook=slack or "",
        slack_channel=channel,
        collect_stats=not no_stats,
        max_workers=workers,
    )
    click.echo(f"\n  SemZero watcher started (interval={interval}s)")
    click.echo(f"  Ctrl+C to stop\n")
    watcher.start()


# ── history ────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--store", default="data/graph_store.db", show_default=True)
@click.option("--limit", default=20, type=int, show_default=True)
def history(store, limit):
    """List recent schema snapshots."""
    from semzero.crawler.graph_store import GraphStore

    snapshots = GraphStore(store).list_snapshots(limit=limit)
    if not snapshots:
        click.echo("\n  No snapshots found.\n")
        return

    click.echo(f"\n  {'ID':>4}  {'Label':<35}  {'Dialect':<12}  {'Nodes':>6}  {'Created'}")
    click.echo(f"  {'─' * 4}  {'─' * 35}  {'─' * 12}  {'─' * 6}  {'─' * 20}")
    for s in snapshots:
        click.echo(
            f"  {s['id']:>4}  {s['label']:<35}  {s['dialect']:<12}  "
            f"{s['node_count']:>6}  {s['created_at'][:19]}"
        )
    click.echo()


# ── chaos ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--db-url", envvar="SEMZERO_DB_URL", default=None)
@click.option(
    "--graph", default=None, help="Path to pre-crawled schema graph JSON (skips crawl step)"
)
@click.option("--dialect", default="auto")
@click.option("--mutations", default=50, type=int, show_default=True)
@click.option("--dbt-project", default=None, envvar="SEMZERO_DBT_PROJECT")
@click.option("--dbt-target", default="dev")
@click.option(
    "--query-file",
    "query_files",
    multiple=True,
    help="SQL workload file(s) to replay inside each chaos mutation",
)
@click.option(
    "--query-dir", "query_dirs", multiple=True, help="Directory containing workload .sql files"
)
@click.option(
    "--history-file", "history_files", multiple=True, help="JSON/JSONL/CSV query history export(s)"
)
@click.option(
    "--dbt-manifest",
    default=None,
    help="Path to dbt manifest.json for model-aware workload seeding",
)
@click.option(
    "--dbt-run-results", default=None, help="Path to dbt run_results.json to prioritize hot models"
)
@click.option(
    "--dbt-catalog", default=None, help="Path to dbt catalog.json for column-aware context"
)
@click.option(
    "--openlineage", "openlineage_paths", multiple=True, help="OpenLineage JSON/JSONL event file(s)"
)
@click.option("--airflow", "airflow_paths", multiple=True, help="Airflow DAG metadata export(s)")
@click.option(
    "--dagster-checks", "dagster_paths", multiple=True, help="Dagster asset-check export(s)"
)
@click.option(
    "--looker",
    "looker_paths",
    multiple=True,
    help="LookML file or directory for downstream blast radius",
)
@click.option(
    "--monte-carlo",
    "montecarlo_paths",
    multiple=True,
    help="Monte Carlo alert/monitor export(s) for observability context",
)
@click.option(
    "--rgcn-model",
    default="",
    help="Optional RGCN checkpoint for graph-native workload prioritization",
)
@click.option(
    "--stateful-recovery/--no-stateful-recovery",
    default=True,
    show_default=True,
    help="Verify whether workloads recover after bad data stops",
)
@click.option("--workload-max-queries", default=50, type=int, show_default=True)
@click.option(
    "--mutation-sample-pct",
    default=0.01,
    type=float,
    show_default=True,
    help="Representative sample size used for row-level chaos injections",
)
@click.option(
    "--null-flood-pct",
    default=0.15,
    type=float,
    show_default=True,
    help="Fraction of sampled rows set to NULL for null-flood mutations",
)
@click.option(
    "--temporal-skew-pct",
    default=0.05,
    type=float,
    show_default=True,
    help="Fraction of sampled rows shifted out of order for temporal chaos",
)
@click.option(
    "--temporal-skew-days",
    default=7,
    type=int,
    show_default=True,
    help="How far late-arriving temporal chaos shifts selected records",
)
@click.option(
    "--volume-spike-multiplier",
    default=10,
    type=int,
    show_default=True,
    help="Approximate amplification used for volume-spike chaos drills",
)
@click.option(
    "--rgcn-model", default="", help="Optional RGCN checkpoint for graph-native prioritization"
)
@click.option("--output", default="data/chaos_report.json")
@click.option("--html-out", default="data/chaos_report.html")
@click.option(
    "--live-mode",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "clone", "metadata-only"]),
)
@click.option(
    "--keep-clone",
    is_flag=True,
    default=False,
    help="Retain the cloned environment for manual debugging",
)
@click.option("--slack", envvar="SEMZERO_SLACK_WEBHOOK", default=None)
@click.option("--repo", envvar="SEMZERO_GITHUB_REPO", default=None)
@click.option("--token", envvar="SEMZERO_GITHUB_TOKEN", default=None)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--no-html", is_flag=True, default=False)
def chaos(
    db_url,
    graph,
    dialect,
    mutations,
    dbt_project,
    dbt_target,
    query_files,
    query_dirs,
    history_files,
    dbt_manifest,
    dbt_run_results,
    dbt_catalog,
    openlineage_paths,
    airflow_paths,
    dagster_paths,
    looker_paths,
    montecarlo_paths,
    stateful_recovery,
    workload_max_queries,
    mutation_sample_pct,
    null_flood_pct,
    temporal_skew_pct,
    temporal_skew_days,
    volume_spike_multiplier,
    rgcn_model,
    output,
    html_out,
    live_mode,
    keep_clone,
    slack,
    repo,
    token,
    dry_run,
    no_html,
):
    """Run Chaos Mode — proactive fragility analysis."""
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine
    from semzero.chaos.chaos_reporter import ChaosHTMLReporter

    graph_json = _load_json(graph) if graph else None

    if not db_url and not graph_json:
        click.echo("Error: provide --db-url or --graph", err=True)
        sys.exit(1)

    from semzero.utils.live_readiness import build_live_readiness_report, resolve_live_mode

    readiness = build_live_readiness_report(db_url or "", dialect=dialect)
    resolved_dry_run, live_mode_warnings = resolve_live_mode(
        live_mode,
        readiness.dialect,
        readiness.clone_supported,
    )
    if dry_run:
        resolved_dry_run = True

    config = ChaosConfig(
        db_url=db_url or "",
        dialect=dialect,
        mutation_count=mutations,
        dbt_project_path=dbt_project or "",
        dbt_target=dbt_target,
        run_dbt_tests=bool(dbt_project),
        workload_query_files=list(query_files),
        workload_query_directories=list(query_dirs),
        workload_history_files=list(history_files),
        dbt_manifest_path=dbt_manifest or "",
        dbt_run_results_path=dbt_run_results or "",
        dbt_catalog_path=dbt_catalog or "",
        openlineage_paths=list(openlineage_paths),
        airflow_paths=list(airflow_paths),
        dagster_paths=list(dagster_paths),
        looker_paths=list(looker_paths),
        montecarlo_paths=list(montecarlo_paths),
        graph_intelligence_enabled=True,
        rgcn_model_path=rgcn_model or "",
        stateful_recovery=stateful_recovery,
        workload_max_queries=workload_max_queries,
        mutation_sample_pct=mutation_sample_pct,
        null_flood_pct=null_flood_pct,
        temporal_skew_pct=temporal_skew_pct,
        temporal_skew_days=temporal_skew_days,
        volume_spike_multiplier=volume_spike_multiplier,
        slack_webhook=slack or "",
        github_repo=repo or "",
        github_token=token or "",
        dry_run=dry_run,
        generate_html=not no_html,
        data_dir=str(Path(output).parent),
    )

    click.echo(f"\n  Chaos Mode — {mutations} mutations, dry_run={dry_run}\n")
    engine = ChaosEngine(config)
    report = engine.run(graph_json=graph_json)
    report.save(output)

    if not no_html:
        ChaosHTMLReporter().generate(report, history=None, output_path=html_out)
        click.echo(f"  HTML → {html_out}\n  Open: open {html_out}\n")

    s = report.summary()
    click.echo(f"  Score: {s['fragility_score']}/100  Grade: {report.fragility_grade}")
    click.echo(f"  Mode:  {s['mode']}  Broke: {s['mutations_that_broke']}/{s['mutations_applied']}")
    click.echo(f"  DNA anti-pattern score: {s['anti_pattern_score']}/100\n")


# ── chaos-schedule ─────────────────────────────────────────────────────────────


@cli.command("chaos-schedule")
@click.option("--db-url", envvar="SEMZERO_DB_URL", required=True)
@click.option(
    "--schedule",
    default="weekly",
    show_default=True,
    type=click.Choice(["hourly", "daily", "weekly"]),
)
@click.option("--mutations", default=50, type=int)
@click.option("--dbt-project", default=None, envvar="SEMZERO_DBT_PROJECT")
@click.option("--slack", envvar="SEMZERO_SLACK_WEBHOOK", default=None)
@click.option("--repo", envvar="SEMZERO_GITHUB_REPO", default=None)
@click.option("--token", envvar="SEMZERO_GITHUB_TOKEN", default=None)
def chaos_schedule(db_url, schedule, mutations, dbt_project, slack, repo, token):
    """Run Chaos Mode on a recurring schedule. Posts Fragility Score to Slack."""
    from semzero.chaos.chaos_engine import ChaosConfig
    from semzero.chaos.chaos_scheduler import ChaosScheduler

    config = ChaosConfig(
        db_url=db_url,
        mutation_count=mutations,
        dbt_project_path=dbt_project or "",
        run_dbt_tests=bool(dbt_project),
        slack_webhook=slack or "",
        github_repo=repo or "",
        github_token=token or "",
    )
    ChaosScheduler(config=config, schedule=schedule).start()


# ── wind-tunnel ────────────────────────────────────────────────────────────────


@cli.command("wind-tunnel")
@click.option(
    "--db-url",
    envvar="SEMZERO_DB_URL",
    required=True,
    help="SQLAlchemy URL of the database to clone and replay against",
)
@click.option("--migration", required=True, help="Path to migration SQL file OR raw SQL string")
@click.option(
    "--graph",
    default=None,
    help="Path to schema graph JSON (enables FK-aware queries + semantic analysis)",
)
@click.option("--queries", default=100, type=int, show_default=True, help="Max queries to replay")
@click.option(
    "--timeout", default=15, type=int, show_default=True, help="Per-query timeout in seconds"
)
@click.option(
    "--query-file",
    "query_files",
    multiple=True,
    help="Path to a .sql file containing one or more workload queries",
)
@click.option(
    "--query-dir",
    "query_dirs",
    multiple=True,
    help="Directory of .sql workload files to include in replay",
)
@click.option(
    "--history-file",
    "history_files",
    multiple=True,
    help="JSON/JSONL/CSV workload history exports to include",
)
@click.option(
    "--dbt-manifest", default=None, help="Path to dbt manifest.json for model-aware query seeding"
)
@click.option(
    "--dbt-run-results", default=None, help="Path to dbt run_results.json to prioritize hot models"
)
@click.option(
    "--dbt-catalog", default=None, help="Path to dbt catalog.json for column-aware context"
)
@click.option(
    "--openlineage", "openlineage_paths", multiple=True, help="OpenLineage JSON/JSONL event file(s)"
)
@click.option("--airflow", "airflow_paths", multiple=True, help="Airflow DAG metadata export(s)")
@click.option(
    "--dagster-checks", "dagster_paths", multiple=True, help="Dagster asset-check export(s)"
)
@click.option(
    "--looker",
    "looker_paths",
    multiple=True,
    help="LookML file or directory for downstream blast radius",
)
@click.option(
    "--monte-carlo",
    "montecarlo_paths",
    multiple=True,
    help="Monte Carlo alert/monitor export(s) for observability context",
)
@click.option(
    "--regime-switching/--no-regime-switching",
    default=True,
    show_default=True,
    help="Generate regime-aware future workloads",
)
@click.option(
    "--focus-asset",
    "focus_assets",
    multiple=True,
    help="Prioritize replay for specific tables/columns like orders or orders.customer_id",
)
@click.option("--output", default="data/wind_tunnel_receipt.json", show_default=True)
@click.option(
    "--live-mode",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "clone", "metadata-only"]),
)
@click.option(
    "--keep-clone",
    is_flag=True,
    default=False,
    help="Retain the cloned environment for manual debugging",
)
@click.option(
    "--no-semantic",
    is_flag=True,
    default=False,
    help="Skip semantic risk analysis of migration SQL",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Skip real clone — use synthetic queries only (useful for CI without DB access)",
)
@click.option("--pr", default=None, type=int, help="GitHub PR number to post receipt to")
@click.option("--repo", envvar="SEMZERO_GITHUB_REPO", default=None)
@click.option("--token", envvar="SEMZERO_GITHUB_TOKEN", default=None)
def wind_tunnel(
    db_url,
    migration,
    graph,
    queries,
    timeout,
    query_files,
    query_dirs,
    history_files,
    dbt_manifest,
    dbt_run_results,
    dbt_catalog,
    openlineage_paths,
    airflow_paths,
    dagster_paths,
    looker_paths,
    montecarlo_paths,
    regime_switching,
    focus_assets,
    output,
    live_mode,
    keep_clone,
    no_semantic,
    dry_run,
    pr,
    repo,
    token,
):
    """
    Clone the database, apply a migration, replay queries, report breakage.

    \b
    Examples:
      semzero wind-tunnel --db-url sqlite:///test.db --migration migration.sql
      semzero wind-tunnel --db-url postgresql://user:pw@host/db --migration v2.sql --pr 42
    """
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

    migration_sql = _read_migration(migration)
    graph_json = _load_json(graph) if graph else None

    from semzero.utils.live_readiness import build_live_readiness_report, resolve_live_mode

    readiness = build_live_readiness_report(db_url)
    resolved_dry_run, live_mode_warnings = resolve_live_mode(
        live_mode,
        readiness.dialect,
        readiness.clone_supported,
    )
    if dry_run:
        resolved_dry_run = True

    config = WindTunnelConfig(
        db_url=db_url,
        max_queries=queries,
        query_timeout_s=timeout,
        query_files=list(query_files),
        query_directories=list(query_dirs),
        workload_history_files=list(history_files),
        dbt_manifest_path=dbt_manifest or "",
        dbt_run_results_path=dbt_run_results or "",
        dbt_catalog_path=dbt_catalog or "",
        openlineage_paths=list(openlineage_paths),
        airflow_paths=list(airflow_paths),
        dagster_paths=list(dagster_paths),
        looker_paths=list(looker_paths),
        montecarlo_paths=list(montecarlo_paths),
        regime_switching_enabled=regime_switching,
        focus_assets=list(focus_assets),
        run_semantic_analysis=not no_semantic,
        dry_run=resolved_dry_run,
        auto_destroy_clone=not keep_clone,
        data_dir=str(Path(output).parent),
        post_to_pr=bool(pr and token),
        github_token=token or "",
        github_repo=repo or "",
    )

    click.echo("\n  Launching Wind Tunnel…\n")
    tunnel = MigrationWindTunnel(config)
    receipt = tunnel.run(
        migration_sql=migration_sql,
        graph_json=graph_json,
        pr_number=pr,
    )
    receipt.save(output)

    verdict_emoji = {
        "SAFE": "✅",
        "SAFE_WITH_PATCHES": "⚠️",
        "BLOCKED": "🚫",
        "NO_QUERIES": "ℹ️",
        "ERROR": "❓",
    }
    v = receipt.verdict.value
    click.echo(f"\n  {verdict_emoji.get(v, '?')} Verdict:    {v}")
    click.echo(f"  Confidence:  {receipt.confidence_score}%")
    click.echo(f"  Replayed:    {receipt.queries_replayed} queries")
    click.echo(f"  Passed:      {receipt.queries_passed}")
    click.echo(f"  Broken:      {receipt.queries_broken}")
    click.echo(f"  Mismatch:    {receipt.queries_mismatch}")
    click.echo(f"  Duration:    {receipt.duration_s:.1f}s")
    if receipt.semantic_risks:
        click.echo(f"  Risks:       {len(receipt.semantic_risks)} semantic risk(s)")
        for r in receipt.semantic_risks[:3]:
            click.echo(f"    ⚠️  {r.risk_type} on `{r.column}`: {r.description[:70]}")
    click.echo(f"\n  Receipt → {output}")
    if receipt.broken_queries:
        click.echo("\n  🚫 Broken queries:")
        for q in receipt.broken_queries[:3]:
            preview = q.query_text[:70].replace("\n", " ")
            err = (q.clone_error or "")[:80]
            click.echo(f"     {preview}…")
            click.echo(f"     ↳ {err}")
    click.echo()


# ── gate ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--drift", default="data/drift_report.json", show_default=True, help="Path to drift report JSON"
)
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--output", default="data/gate_result.json", show_default=True)
@click.option(
    "--pr",
    default=None,
    type=int,
    help="GitHub PR number — posts verdict as comment + status check",
)
@click.option("--repo", envvar="SEMZERO_GITHUB_REPO", default=None)
@click.option("--token", envvar="SEMZERO_GITHUB_TOKEN", default=None)
@click.option(
    "--team",
    envvar="SEMZERO_DATA_TEAM",
    default=None,
    help="GitHub team slug to request review from on BLOCK/NEEDS_REVIEW",
)
@click.option(
    "--wind-tunnel",
    "run_wt",
    is_flag=True,
    default=False,
    help="Also run Wind Tunnel simulation and attach receipt",
)
@click.option(
    "--db-url",
    envvar="SEMZERO_DB_URL",
    default=None,
    help="DB URL for Wind Tunnel (required if --wind-tunnel)",
)
@click.option("--migration", default=None, help="Migration SQL file or string (for Wind Tunnel)")
@click.option(
    "--wind-live-mode",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "clone", "metadata-only"]),
    help="How Change Gate should execute Wind Tunnel when enabled",
)
@click.option(
    "--keep-wind-clone",
    is_flag=True,
    default=False,
    help="Keep the Wind Tunnel clone when Gate launches a simulation",
)
@click.option("--strict", is_flag=True, default=False, help="Also block on NEEDS_REVIEW changes")
@click.option(
    "--no-block", is_flag=True, default=False, help="Evaluate but never exit non-zero (report only)"
)
@click.option(
    "--proof-path",
    "proof_paths",
    multiple=True,
    help="SQL/Python file or directory to scan for AST-first proofing (repeatable)",
)
@click.option("--dbt-manifest", default=None)
@click.option("--dbt-catalog", default=None)
@click.option("--dbt-run-results", default=None)
@click.option("--openlineage", "openlineage_paths", multiple=True)
@click.option("--airflow", "airflow_paths", multiple=True)
@click.option("--dagster-checks", "dagster_paths", multiple=True)
@click.option("--looker", "looker_paths", multiple=True)
@click.option("--monte-carlo", "montecarlo_paths", multiple=True)
@click.option(
    "--rgcn-model", default="", help="Optional RGCN checkpoint for graph-native risk ranking"
)
@click.option(
    "--chaos-report",
    default=None,
    help="Optional chaos report JSON to include in the merge comment preview/post",
)
@click.option(
    "--comment-out",
    default="data/merge_comment.md",
    show_default=True,
    help="Write the rendered merge comment markdown here",
)
def gate(
    drift,
    graph,
    output,
    pr,
    repo,
    token,
    team,
    run_wt,
    db_url,
    migration,
    wind_live_mode,
    keep_wind_clone,
    strict,
    no_block,
    proof_paths,
    dbt_manifest,
    dbt_catalog,
    dbt_run_results,
    openlineage_paths,
    airflow_paths,
    dagster_paths,
    looker_paths,
    montecarlo_paths,
    rgcn_model,
    chaos_report,
    comment_out,
):
    """
    Pre-merge Change Gate — evaluate a drift report and post PR verdict.

    \b
    Exits 1 if verdict is BLOCK (use --no-block to disable).

    Examples:
      semzero gate --drift data/drift_report.json --pr 42
      semzero gate --drift data/drift_report.json --wind-tunnel --db-url $SEMZERO_DB_URL
    """
    from semzero.integrations.change_gate import ChangeGate, GateConfig, Verdict

    graph_json = _load_json(graph)
    drift_report = _load_json(drift)

    resolved_proof_paths = list(proof_paths) or _default_proof_paths()

    config = GateConfig(
        github_token=token or "",
        github_repo=repo or "",
        data_owner_team=team or "",
        strict_mode=strict,
        run_wind_tunnel=run_wt and bool(db_url),
        db_url=db_url or "",
        wind_tunnel_max_queries=100,
        wind_tunnel_live_mode=wind_live_mode,
        wind_tunnel_keep_clone=keep_wind_clone,
        data_dir=str(Path(output).parent),
        proof_enabled=True,
        proof_source_paths=resolved_proof_paths,
        dbt_manifest_path=dbt_manifest or "",
        dbt_catalog_path=dbt_catalog or "",
        dbt_run_results_path=dbt_run_results or "",
        openlineage_paths=list(openlineage_paths),
        airflow_paths=list(airflow_paths),
        dagster_paths=list(dagster_paths),
        looker_paths=list(looker_paths),
        montecarlo_paths=list(montecarlo_paths),
        calibration_store_path=str(Path(output).parent / "calibration_history.jsonl"),
        graph_intelligence_enabled=True,
        rgcn_model_path=rgcn_model or "",
    )

    gate_obj = ChangeGate(graph_json, config)
    result = gate_obj.evaluate(drift_report, pr_number=pr)
    if chaos_report:
        result.chaos_report = _load_json(chaos_report)

    # Wind Tunnel
    if run_wt:
        if not db_url:
            click.echo("  ⚠️  --wind-tunnel requires --db-url", err=True)
        else:
            migration_sql = _read_migration(migration) if migration else ""
            if not migration_sql:
                click.echo("  ℹ️  No --migration provided — Wind Tunnel will use synthetic queries")
            result = gate_obj.run_wind_tunnel(
                result,
                migration_sql=migration_sql,
                drift_report=drift_report,
                graph_json=graph_json,
            )

    result.save(output)

    comment_body = gate_obj._build_pr_comment(result)
    Path(comment_out).parent.mkdir(parents=True, exist_ok=True)
    Path(comment_out).write_text(comment_body, encoding="utf-8")

    if pr and token and repo:
        gate_obj.post_to_pr(result)

    # Terminal output
    v = result.verdict.value
    emoji = {"SAFE": "✅", "NEEDS_REVIEW": "⚠️", "BLOCK": "🚫"}.get(v, "❓")
    click.echo(f"\n  {emoji} Verdict: {v}")
    click.echo(f"  Blast radius: {result.total_blast_radius} downstream nodes")
    click.echo(f"  Assessments: {len(result.assessments)} changes evaluated")

    if result.blocked_by:
        click.echo("\n  Blocking issues:")
        for b in result.blocked_by:
            click.echo(f"    🚫 {b}")

    if result.review_reasons:
        click.echo("\n  Review required:")
        for r in result.review_reasons:
            click.echo(f"    ⚠️  {r}")

    if result.simulation_summary:
        click.echo(f"\n  Wind Tunnel simulation attached to result.")

    click.echo(f"\n  Gate result → {output}\n")

    if result.verdict == Verdict.BLOCK and not no_block:
        sys.exit(1)


# ── premerge ───────────────────────────────────────────────────────────────────


@cli.command("premerge")
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--drift", default="data/drift_report.json", show_default=True)
@click.option("--db-url", envvar="SEMZERO_DB_URL", default="")
@click.option("--migration", default="", help="Migration SQL file or inline SQL for scoped replay")
@click.option(
    "--proof-path", "proof_paths", multiple=True, help="SQL/Python path to scan for AST proofing"
)
@click.option("--dbt-manifest", default=None)
@click.option("--dbt-catalog", default=None)
@click.option("--dbt-run-results", default=None)
@click.option("--openlineage", "openlineage_paths", multiple=True)
@click.option("--airflow", "airflow_paths", multiple=True)
@click.option("--dagster-checks", "dagster_paths", multiple=True)
@click.option("--looker", "looker_paths", multiple=True)
@click.option("--monte-carlo", "montecarlo_paths", multiple=True)
@click.option(
    "--rgcn-model",
    default="",
    help="Optional RGCN checkpoint for graph-native prioritization across Gate/Wind/Chaos",
)
@click.option(
    "--repo",
    envvar="SEMZERO_GITHUB_REPO",
    default="",
    help="Repository identifier for shadow/team trend dashboards",
)
@click.option(
    "--team",
    envvar="SEMZERO_DATA_TEAM",
    default="",
    help="Team identifier for shadow rollout dashboards",
)
@click.option(
    "--run-chaos",
    is_flag=True,
    default=False,
    help="Also run targeted Chaos when Gate recommends it",
)
@click.option(
    "--live-mode",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "clone", "metadata-only"]),
)
@click.option("--output", default="data/premerge_bundle.json", show_default=True)
@click.option(
    "--shadow",
    "shadow",
    is_flag=True,
    default=False,
    help="Collect evidence without enabling merge-block enforcement in the bundle/report.",
)
@click.option(
    "--shadow-mode",
    "shadow",
    is_flag=True,
    help="Alias for --shadow for CI and shadow deployments.",
)
def premerge(
    graph,
    drift,
    db_url,
    migration,
    proof_paths,
    dbt_manifest,
    dbt_catalog,
    dbt_run_results,
    openlineage_paths,
    airflow_paths,
    dagster_paths,
    looker_paths,
    montecarlo_paths,
    rgcn_model,
    repo,
    team,
    run_chaos,
    live_mode,
    output,
    shadow,
):
    """One-command premerge workflow: Gate first, then scoped Wind Tunnel and Chaos only when justified."""
    from semzero.reliability.premerge import PremergeWorkflow, PremergeWorkflowConfig

    graph_json = _load_json(graph)
    drift_report = _load_json(drift)
    migration_sql = _read_migration(migration) if migration else ""

    workflow = PremergeWorkflow(
        graph_json,
        PremergeWorkflowConfig(
            db_url=db_url or "",
            data_dir=str(Path(output).parent),
            proof_paths=list(proof_paths) or _default_proof_paths(),
            dbt_manifest_path=dbt_manifest or "",
            dbt_catalog_path=dbt_catalog or "",
            dbt_run_results_path=dbt_run_results or "",
            openlineage_paths=list(openlineage_paths),
            airflow_paths=list(airflow_paths),
            dagster_paths=list(dagster_paths),
            looker_paths=list(looker_paths),
            montecarlo_paths=list(montecarlo_paths),
            graph_intelligence_enabled=True,
            rgcn_model_path=rgcn_model or "",
            github_repo=repo or "",
            data_owner_team=team or "",
            shadow_mode=shadow,
            run_wind_tunnel=bool(db_url),
            run_chaos=run_chaos and bool(db_url),
            wind_live_mode=live_mode,
            chaos_live_mode=live_mode,
            chaos_mutation_count=8,
        ),
    )
    bundle = workflow.run(drift_report=drift_report, migration_sql=migration_sql)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(bundle.to_dict(), indent=2, default=str), encoding="utf-8")

    gate = bundle.gate_result
    click.echo(f"\n  Premerge verdict: {gate.get('verdict', 'UNKNOWN')}")
    click.echo(f"  Reliability score: {gate.get('reliability_score', 'n/a')}")
    click.echo(f"  On-call risk: {gate.get('oncall_risk', 'UNKNOWN')}")
    click.echo(f"  Bundle → {output}")
    report_path = bundle.artifact_paths.get("report")
    if report_path:
        click.echo(f"  Markdown report → {report_path}\n")


# ── validate-e2e ───────────────────────────────────────────────────────────────


@cli.command("validate-e2e")
@click.option("--db-url", envvar="SEMZERO_DB_URL", default="")
@click.option(
    "--graph", default="", help="Existing schema graph JSON. Omit when using --demo-pack-dir."
)
@click.option(
    "--drift", default="", help="Existing drift report JSON. Omit when using --demo-pack-dir."
)
@click.option(
    "--migration", default="", help="Migration SQL file or inline SQL used for end-to-end replay."
)
@click.option(
    "--proof-path",
    "proof_paths",
    multiple=True,
    help="SQL/app paths to include in validation proofing.",
)
@click.option(
    "--workload-query-file",
    "workload_query_files",
    multiple=True,
    help="Representative workload SQL file(s).",
)
@click.option(
    "--demo-pack-dir",
    default="",
    help="Create and use a built-in validation pack in this directory.",
)
@click.option(
    "--demo-scale",
    default="medium",
    show_default=True,
    type=click.Choice(["small", "medium", "large", "xlarge"]),
)
@click.option(
    "--demo-profile",
    default="standard",
    show_default=True,
    type=click.Choice(["standard", "messy", "finance", "chaos_labyrinth", "black_swan"]),
)
@click.option(
    "--demo-backend", default="sqlite", show_default=True, type=click.Choice(["sqlite", "postgres"])
)
@click.option(
    "--source-schema",
    default="public",
    show_default=True,
    help="Source schema to seed/use for live Postgres validation packs.",
)
@click.option(
    "--scenario",
    "scenarios",
    multiple=True,
    type=click.Choice(
        [
            "silent_truncation",
            "rename_breakage",
            "nullability_hardening",
            "numeric_precision_narrowing",
            "domain_enum_drift",
            "temporal_timezone_mismatch",
            "distribution_drift",
            "blank_string_fanout",
            "incremental_ghost",
            "ast_cross_modal_truth",
            "pregate_gate_stop",
        ]
    ),
)
@click.option("--output", default="data/validation_report.json", show_default=True)
@click.option("--markdown-out", default="data/validation_report.md", show_default=True)
@click.option("--html-out", default="data/validation_report.html", show_default=True)
def validate_e2e(
    db_url,
    graph,
    drift,
    migration,
    proof_paths,
    workload_query_files,
    demo_pack_dir,
    demo_scale,
    demo_profile,
    demo_backend,
    source_schema,
    scenarios,
    output,
    markdown_out,
    html_out,
):
    """Run end-to-end technical validation against a live/demo SQL database and compare predicted vs actual behavior."""
    from semzero.reliability.validation import ValidationConfig, ValidationHarness

    config = ValidationConfig(
        db_url=db_url or "",
        graph_path=graph or "",
        drift_path=drift or "",
        migration_path=migration or "",
        data_dir=str(Path(output).parent),
        proof_paths=list(proof_paths),
        workload_query_files=list(workload_query_files),
        run_chaos=True,
        demo_pack_dir=demo_pack_dir or "",
        demo_scale=demo_scale,
        demo_profile=demo_profile,
        demo_backend=demo_backend,
        source_schema=source_schema,
        scenarios=list(scenarios)
        or [
            "silent_truncation",
            "rename_breakage",
            "nullability_hardening",
            "numeric_precision_narrowing",
            "domain_enum_drift",
            "temporal_timezone_mismatch",
            "distribution_drift",
            "blank_string_fanout",
            "incremental_ghost",
            "ast_cross_modal_truth",
        ],
    )
    report = ValidationHarness(config).run()
    report.save(output, markdown_out, html_out)
    summary = report.summary
    click.echo(f"\n  Validation report → {output}")
    click.echo(f"  Markdown → {markdown_out}")
    click.echo(f"  HTML → {html_out}")
    click.echo(f"  Gate verdict: {summary.get('gate_verdict', 'UNKNOWN')}")
    click.echo(f"  Reliability score: {summary.get('reliability_score', 'n/a')}")
    click.echo(f"  Queries replayed: {summary.get('queries_replayed', 0)}")
    click.echo(f"  Broken queries: {summary.get('queries_broken', 0)}")
    click.echo(f"  Broken mutations: {summary.get('mutations_that_broke', 0)}")
    click.echo(
        f"  Prediction alignment: {summary.get('aligned_predictions', 0)}/{summary.get('scenarios_with_ground_truth', 0)}\n"
    )


# ── ops-report ──────────────────────────────────────────────────────────────────


@cli.command("shadow")
@click.option("--graph", default="data/schema_graph.json", show_default=True)
@click.option("--drift", default="data/drift_report.json", show_default=True)
@click.option("--db-url", envvar="SEMZERO_DB_URL", default="")
@click.option("--migration", default="", help="Migration SQL file or inline SQL for scoped replay")
@click.option(
    "--proof-path", "proof_paths", multiple=True, help="SQL/Python path to scan for AST proofing"
)
@click.option("--dbt-manifest", default=None)
@click.option("--dbt-catalog", default=None)
@click.option("--dbt-run-results", default=None)
@click.option("--openlineage", "openlineage_paths", multiple=True)
@click.option("--airflow", "airflow_paths", multiple=True)
@click.option("--dagster-checks", "dagster_paths", multiple=True)
@click.option("--looker", "looker_paths", multiple=True)
@click.option("--monte-carlo", "montecarlo_paths", multiple=True)
@click.option(
    "--rgcn-model",
    default="",
    help="Optional RGCN checkpoint for graph-native prioritization across Gate/Wind/Chaos",
)
@click.option(
    "--repo",
    envvar="SEMZERO_GITHUB_REPO",
    default="",
    help="Repository identifier for trend dashboards",
)
@click.option(
    "--team", envvar="SEMZERO_DATA_TEAM", default="", help="Team identifier for trend dashboards"
)
@click.option(
    "--run-chaos",
    is_flag=True,
    default=False,
    help="Also run targeted Chaos when Gate recommends it",
)
@click.option(
    "--live-mode",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "clone", "metadata-only"]),
)
@click.option("--output", default="data/shadow_premerge_bundle.json", show_default=True)
def shadow_cmd(
    graph,
    drift,
    db_url,
    migration,
    proof_paths,
    dbt_manifest,
    dbt_catalog,
    dbt_run_results,
    openlineage_paths,
    airflow_paths,
    dagster_paths,
    looker_paths,
    montecarlo_paths,
    rgcn_model,
    repo,
    team,
    run_chaos,
    live_mode,
    output,
):
    """Run the full SemZero premerge workflow in shadow mode with non-blocking enforcement."""
    ctx = click.get_current_context()
    return ctx.invoke(
        premerge,
        graph=graph,
        drift=drift,
        db_url=db_url,
        migration=migration,
        proof_paths=proof_paths,
        dbt_manifest=dbt_manifest,
        dbt_catalog=dbt_catalog,
        dbt_run_results=dbt_run_results,
        openlineage_paths=openlineage_paths,
        airflow_paths=airflow_paths,
        dagster_paths=dagster_paths,
        looker_paths=looker_paths,
        montecarlo_paths=montecarlo_paths,
        rgcn_model=rgcn_model,
        repo=repo,
        team=team,
        run_chaos=run_chaos,
        live_mode=live_mode,
        output=output,
        shadow=True,
    )


@cli.command("assumption-dashboard")
@click.option(
    "--receipt-dir",
    default="data",
    show_default=True,
    help="Directory containing assumption-gate receipt JSON files.",
)
@click.option("--output", default="", show_default=False, help="JSON dashboard output path.")
@click.option(
    "--markdown-output", default="", show_default=False, help="Markdown dashboard output path."
)
@click.option(
    "--feedback-file",
    default="",
    show_default=False,
    help="Optional JSONL developer feedback ledger. Defaults to receipt-dir/assumption_feedback.jsonl.",
)
@click.option(
    "--exceptions-file",
    default="",
    show_default=False,
    help="Optional JSONL exception ledger. Defaults to receipt-dir/assumption_exceptions.jsonl.",
)
def assumption_dashboard_cmd(receipt_dir, output, markdown_output, feedback_file, exceptions_file):
    """Aggregate dbt Assumption Gate receipts into an assumption-first shadow dashboard."""
    from .reliability.assumption_dashboard import AssumptionDashboard

    dashboard = AssumptionDashboard(
        receipt_dir=receipt_dir, feedback_file=feedback_file, exceptions_file=exceptions_file
    )
    output = output or str(Path(receipt_dir) / "assumption_dashboard.json")
    markdown_output = markdown_output or str(Path(receipt_dir) / "assumption_dashboard.md")
    payload = dashboard.save_json(output)
    dashboard.save_markdown(markdown_output)
    click.echo("\n  SemZero Assumption Dashboard")
    click.echo(f"  Runs scanned: {payload.get('run_count', 0)}")
    click.echo(f"  Assumption findings: {payload.get('assumption_finding_count', 0)}")
    click.echo(f"  Would require review: {payload.get('would_require_review_count', 0)}")
    feedback = payload.get("feedback") or {}
    click.echo(f"  Developer feedback records: {feedback.get('feedback_count', 0)}")
    if feedback.get("developer_agreement_rate") is not None:
        click.echo(
            f"  Developer agreement rate: {round(feedback['developer_agreement_rate'] * 100, 1)}%"
        )
    roi = payload.get("roi") or {}
    cost = roi.get("estimated_cost_exposure_usd_per_run")
    if cost is not None:
        monthly = roi.get("estimated_cost_exposure_usd_per_month")
        suffix = f" | ${monthly}/month" if monthly is not None else ""
        click.echo(f"  Rough cost exposure surfaced: ${cost}/run{suffix}")
    avoided = roi.get("estimated_avoided_cost_usd_per_run")
    if avoided is not None:
        avoided_monthly = roi.get("estimated_avoided_cost_usd_per_month")
        suffix = f" | ${avoided_monthly}/month" if avoided_monthly is not None else ""
        click.echo(f"  Rough avoided cost from fixed findings: ${avoided}/run{suffix}")
    click.echo(f"  Fixed findings: {roi.get('fixed_finding_count', 0)}")
    click.echo(f"  Accepted-risk findings: {roi.get('accepted_risk_finding_count', 0)}")
    exceptions = payload.get("exceptions") or {}
    if exceptions.get("exception_count"):
        click.echo(f"  Active exception records: {exceptions.get('active_exception_count', 0)}")
        click.echo(f"  Expired exception records: {exceptions.get('expired_exception_count', 0)}")
    policy = payload.get("policy_recommendations") or {}
    if policy.get("summary"):
        click.echo(f"  Policy calibration: {policy.get('summary')}")
    if policy.get("require_review_candidates"):
        click.echo(
            f"  Require-review candidates: {', '.join(policy.get('require_review_candidates') or [])}"
        )
    if policy.get("lower_severity_or_suppress_candidates"):
        click.echo(
            f"  Suppress/lower-severity candidates: {', '.join(policy.get('lower_severity_or_suppress_candidates') or [])}"
        )
    readiness = payload.get("calibration_readiness") or {}
    if readiness.get("state"):
        click.echo(f"  Calibration readiness: {readiness.get('state')} — {readiness.get('reason')}")
    click.echo(f"  Stable recurring findings: {payload.get('stable_finding_count', 0)}")
    click.echo(f"  JSON → {output}")
    click.echo(f"  Markdown → {markdown_output}\n")


@cli.command("assumption-lineage")
@click.option(
    "--receipt-dir",
    default="data",
    show_default=True,
    help="Directory containing assumption-gate receipt JSON files.",
)
@click.option("--output", default="", show_default=False, help="JSON lineage output path.")
@click.option(
    "--markdown-output", default="", show_default=False, help="Markdown lineage output path."
)
@click.option(
    "--feedback-file",
    default="",
    show_default=False,
    help="Optional JSONL developer feedback ledger. Defaults to receipt-dir/assumption_feedback.jsonl.",
)
@click.option(
    "--exceptions-file",
    default="",
    show_default=False,
    help="Optional JSONL exception ledger. Defaults to receipt-dir/assumption_exceptions.jsonl.",
)
def assumption_lineage_cmd(receipt_dir, output, markdown_output, feedback_file, exceptions_file):
    """Build an Assumption Lineage Lite graph from receipts, feedback, and exceptions."""
    from .reliability.assumption_lineage import AssumptionLineageBuilder

    builder = AssumptionLineageBuilder(
        receipt_dir=receipt_dir, feedback_file=feedback_file, exceptions_file=exceptions_file
    )
    output = output or str(Path(receipt_dir) / "assumption_lineage.json")
    markdown_output = markdown_output or str(Path(receipt_dir) / "assumption_lineage.md")
    payload = builder.save_json(output)
    builder.save_markdown(markdown_output)
    click.echo("\n  SemZero Assumption Lineage Lite")
    click.echo(f"  Receipts scanned: {payload.get('receipt_count', 0)}")
    click.echo(f"  Assumption nodes: {payload.get('assumption_node_count', 0)}")
    click.echo(f"  Graph nodes: {payload.get('node_count', 0)}")
    click.echo(f"  Graph edges: {payload.get('edge_count', 0)}")
    replay = payload.get("replay_counts") or {}
    click.echo(f"  Replay-validated drift nodes: {replay.get('replay_validated_drift', 0)}")
    click.echo(f"  Inferred-only nodes: {replay.get('inferred_only', 0)}")
    click.echo(f"  JSON → {output}")
    click.echo(f"  Markdown → {markdown_output}\n")


@cli.command("assumption-decay")
@click.option(
    "--receipt-dir",
    default="data",
    show_default=True,
    help="Directory containing assumption-gate receipt JSON files.",
)
@click.option("--output", default="", show_default=False, help="JSON decay report output path.")
@click.option(
    "--markdown-output", default="", show_default=False, help="Markdown decay report output path."
)
@click.option(
    "--feedback-file",
    default="",
    show_default=False,
    help="Optional JSONL developer feedback ledger. Defaults to receipt-dir/assumption_feedback.jsonl.",
)
@click.option(
    "--exceptions-file",
    default="",
    show_default=False,
    help="Optional JSONL exception ledger. Defaults to receipt-dir/assumption_exceptions.jsonl.",
)
@click.option(
    "--recurring-threshold",
    default=2,
    show_default=True,
    type=int,
    help="Occurrences needed before a stable assumption is considered recurring.",
)
@click.option(
    "--review-due-days",
    default=14,
    show_default=True,
    type=int,
    help="Receipt age in days after which evidence is review-due.",
)
@click.option(
    "--stale-days",
    default=30,
    show_default=True,
    type=int,
    help="Receipt age in days after which evidence is stale.",
)
def assumption_decay_cmd(
    receipt_dir,
    output,
    markdown_output,
    feedback_file,
    exceptions_file,
    recurring_threshold,
    review_due_days,
    stale_days,
):
    """Track assumption decay / fragility from receipts, feedback, replay, and exceptions."""
    from .reliability.assumption_decay import AssumptionDecayConfig, AssumptionDecayTracker

    config = AssumptionDecayConfig(
        receipt_dir=receipt_dir,
        feedback_file=feedback_file or None,
        exceptions_file=exceptions_file or None,
        recurring_threshold=recurring_threshold,
        review_due_days=review_due_days,
        stale_days=stale_days,
    )
    tracker = AssumptionDecayTracker(config)
    output = output or str(Path(receipt_dir) / "assumption_decay.json")
    markdown_output = markdown_output or str(Path(receipt_dir) / "assumption_decay.md")
    payload = tracker.save_json(output)
    tracker.save_markdown(markdown_output)
    click.echo("\n  SemZero Assumption Decay Tracking Lite")
    click.echo(f"  Receipts scanned: {payload.get('receipt_count', 0)}")
    click.echo(f"  Stable assumptions: {payload.get('stable_assumption_count', 0)}")
    states = payload.get("state_counts") or {}
    if states:
        click.echo("  Decay states: " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    signals = payload.get("signal_counts") or {}
    if signals:
        click.echo("  Signals: " + ", ".join(f"{k}={v}" for k, v in sorted(signals.items())))
    click.echo(f"  Review queue: {len(payload.get('review_queue') or [])}")
    click.echo(f"  JSON → {output}")
    click.echo(f"  Markdown → {markdown_output}\n")


@cli.command("assumption-memory")
@click.option(
    "--receipt-dir",
    default="data",
    show_default=True,
    help="Directory containing assumption-gate receipt JSON files.",
)
@click.option(
    "--output", default="", show_default=False, help="JSON drift-memory report output path."
)
@click.option(
    "--markdown-output",
    default="",
    show_default=False,
    help="Markdown drift-memory report output path.",
)
@click.option(
    "--feedback-file",
    default="",
    show_default=False,
    help="Optional JSONL developer feedback ledger. Defaults to receipt-dir/assumption_feedback.jsonl.",
)
@click.option(
    "--exceptions-file",
    default="",
    show_default=False,
    help="Optional JSONL exception ledger. Defaults to receipt-dir/assumption_exceptions.jsonl.",
)
def assumption_memory_cmd(receipt_dir, output, markdown_output, feedback_file, exceptions_file):
    """Build organization/team/model-level Assumption Drift Memory Lite from existing evidence."""
    from .reliability.assumption_memory import AssumptionMemoryBuilder, AssumptionMemoryConfig

    config = AssumptionMemoryConfig(
        receipt_dir=receipt_dir,
        feedback_file=feedback_file or None,
        exceptions_file=exceptions_file or None,
    )
    builder = AssumptionMemoryBuilder(config)
    output = output or str(Path(receipt_dir) / "assumption_memory.json")
    markdown_output = markdown_output or str(Path(receipt_dir) / "assumption_memory.md")
    payload = builder.save_json(output)
    builder.save_markdown(markdown_output)
    org = payload.get("organization_memory") or {}
    click.echo("\n  SemZero Assumption Drift Memory Lite")
    click.echo(f"  Receipts scanned: {payload.get('receipt_count', 0)}")
    click.echo(f"  Findings: {payload.get('finding_count', 0)}")
    click.echo(
        f"  Organization memory: {org.get('memory_pattern', 'watch')} · score={org.get('memory_score', 0)}"
    )
    watch = payload.get("memory_watchlist") or []
    if watch:
        click.echo(f"  Watchlist: {len(watch)} item(s)")
        for row in watch[:5]:
            click.echo(
                f"    - {row.get('kind')}:{row.get('name')} · {row.get('memory_pattern')} · score={row.get('memory_score')}"
            )
    click.echo(f"  JSON → {output}")
    click.echo(f"  Markdown → {markdown_output}\n")


@cli.command("assumption-precision-eval")
@click.option(
    "--receipt-dir",
    default="data",
    show_default=True,
    help="Directory containing assumption-gate receipt JSON files.",
)
@click.option("--output", default="", show_default=False, help="JSON precision report output path.")
@click.option(
    "--markdown-output",
    default="",
    show_default=False,
    help="Markdown precision report output path.",
)
@click.option(
    "--feedback-file",
    default="",
    show_default=False,
    help="Optional JSONL developer feedback ledger. Defaults to receipt-dir/assumption_feedback.jsonl.",
)
@click.option(
    "--exceptions-file",
    default="",
    show_default=False,
    help="Optional JSONL exception ledger. Defaults to receipt-dir/assumption_exceptions.jsonl.",
)
def assumption_precision_eval_cmd(
    receipt_dir, output, markdown_output, feedback_file, exceptions_file
):
    """Evaluate useful/noisy/over-broad assumption findings before policy promotion."""
    from .reliability.assumption_precision import AssumptionPrecisionEvaluator, PrecisionConfig

    config = PrecisionConfig(
        receipt_dir=receipt_dir,
        feedback_file=feedback_file or None,
        exceptions_file=exceptions_file or None,
    )
    evaluator = AssumptionPrecisionEvaluator(config)
    output = output or str(Path(receipt_dir) / "assumption_precision_eval.json")
    markdown_output = markdown_output or str(Path(receipt_dir) / "assumption_precision_eval.md")
    payload = evaluator.save_json(output)
    evaluator.save_markdown(markdown_output)
    click.echo("\n  SemZero Assumption Precision Evaluation")
    click.echo(f"  Receipts scanned: {payload.get('receipt_count', 0)}")
    click.echo(f"  Findings evaluated: {payload.get('finding_count', 0)}")
    click.echo(
        f"  Feedback coverage: {round(float(payload.get('feedback_coverage_rate', 0) or 0) * 100, 1)}%"
    )
    click.echo(
        f"  Replay Lite coverage: {round(float(payload.get('replay_coverage_rate', 0) or 0) * 100, 1)}%"
    )
    click.echo(f"  Enforcement-risky findings: {payload.get('enforcement_risky_count', 0)}")
    click.echo(f"  JSON → {output}")
    click.echo(f"  Markdown → {markdown_output}\n")


@cli.command("assumption-dogfood-report")
@click.option(
    "--dogfood-dir",
    default="examples/dogfood_dbt_assumption_gate",
    show_default=True,
    help="Dogfood fixture directory containing receipts/dashboard outputs.",
)
@click.option("--output", default="", show_default=False, help="JSON demo report output path.")
@click.option(
    "--markdown-output", default="", show_default=False, help="Markdown demo report output path."
)
def assumption_dogfood_report_cmd(dogfood_dir, output, markdown_output):
    """Build a product-demo report from dogfood Assumption Gate outputs."""
    from .reliability.dogfood_report import DogfoodReportBuilder

    root = Path(dogfood_dir)
    builder = DogfoodReportBuilder(root)
    output = output or str(root / "dogfood_demo_report.json")
    markdown_output = markdown_output or str(root / "dogfood_demo_report.md")
    payload = builder.save_json(output)
    builder.save_markdown(markdown_output)
    click.echo("\n  SemZero Dogfood Demo Report")
    click.echo(f"  Scenarios: {payload.get('scenario_count', 0)}")
    click.echo(f"  Passed: {payload.get('scenario_pass_count', 0)}")
    click.echo(f"  Failed: {payload.get('scenario_fail_count', 0)}")
    click.echo(f"  Families covered: {', '.join(payload.get('families_covered') or [])}")
    click.echo(f"  JSON → {output}")
    click.echo(f"  Markdown → {markdown_output}\n")


@cli.command("shadow-dashboard")
@click.option("--data-dir", default="data", show_default=True)
@click.option("--output", default="", show_default=False)
@click.option("--html-output", default="", show_default=False)
def shadow_dashboard_cmd(data_dir, output, html_output):
    """Build a shadow-mode proof dashboard showing would-have-blocked and would-have-saved value."""
    from .reliability.shadow_mode import ShadowDashboard

    dashboard = ShadowDashboard(
        shadow_runs_path=str(Path(data_dir) / "shadow_runs.jsonl"),
        feedback_path=str(Path(data_dir) / "shadow_feedback.jsonl"),
        override_path=str(Path(data_dir) / "override_ledger.jsonl"),
        incident_path=str(Path(data_dir) / "incident_ledger.jsonl"),
    )
    output = output or str(Path(data_dir) / "shadow_dashboard.json")
    html_output = html_output or str(Path(data_dir) / "shadow_dashboard.html")
    payload = dashboard.build()
    dashboard.save_json(output)
    dashboard.save_html(html_output)
    click.echo(f"\n  Shadow dashboard → {output}")
    click.echo(f"  HTML → {html_output}")
    click.echo(f"  Runs: {payload.get('run_count', 0)}")
    click.echo(f"  Would-have-blocked: {payload.get('would_have_blocked', 0)}")
    click.echo(
        f"  Estimated savings surfaced: ${float(payload.get('estimated_savings_usd_total', 0.0) or 0.0):,.0f}\n"
    )


@cli.command("shadow-feedback")
@click.option("--receipt-id", required=True, help="Receipt or run id associated with the feedback.")
@click.option("--target", required=True, help="PR / repo / target identifier.")
@click.option("--actor", required=True, help="Who is recording the feedback.")
@click.option(
    "--outcome",
    required=True,
    type=click.Choice(["confirmed", "useful", "noisy", "false_positive", "fixed", "expected"]),
)
@click.option("--note", default="", show_default=False, help="Optional explanation or context.")
@click.option("--repo", default="", help="Repository identifier for trend calibration")
@click.option("--team", default="", help="Team identifier for trend calibration")
@click.option("--risk-category", default="", help="Risk category this feedback applies to")
@click.option("--data-dir", default="data", show_default=True)
def shadow_feedback_cmd(
    receipt_id, target, actor, outcome, note, repo, team, risk_category, data_dir
):
    """Record developer feedback for shadow-mode calibration and trust tuning."""
    from .integrations.feedback_ledger import ShadowFeedbackLedger
    from .reliability.shadow_mode import ShadowDashboard

    ledger = ShadowFeedbackLedger(path=str(Path(data_dir) / "shadow_feedback.jsonl"))
    row = ledger.record(
        receipt_id=receipt_id,
        target=target,
        actor=actor,
        outcome=outcome,
        note=note,
        repo=repo,
        team=team,
        risk_category=risk_category,
    )
    dashboard = ShadowDashboard(
        shadow_runs_path=str(Path(data_dir) / "shadow_runs.jsonl"),
        feedback_path=str(Path(data_dir) / "shadow_feedback.jsonl"),
        override_path=str(Path(data_dir) / "override_ledger.jsonl"),
        incident_path=str(Path(data_dir) / "incident_ledger.jsonl"),
    )
    payload = dashboard.build()
    dashboard.save_json(str(Path(data_dir) / "shadow_dashboard.json"))
    dashboard.save_html(str(Path(data_dir) / "shadow_dashboard.html"))
    click.echo(f"\n  Shadow feedback recorded for {row['target']} ({row['outcome']})")
    click.echo(
        f"  Precision proxy: {float((payload.get('feedback_summary', {}) or {}).get('precision_proxy', 0.0) or 0.0):.0%}\n"
    )


@cli.command("shadow-trends")
@click.option("--data-dir", default="data", show_default=True)
@click.option(
    "--scope", default="repo", show_default=True, type=click.Choice(["repo", "team", "global"])
)
@click.option("--limit", default=10, show_default=True, type=int)
def shadow_trends_cmd(data_dir, scope, limit):
    """Show repo/team shadow trend history and confidence-tier enforcement recommendations."""
    from .reliability.shadow_mode import ShadowDashboard

    dashboard = ShadowDashboard(
        shadow_runs_path=str(Path(data_dir) / "shadow_runs.jsonl"),
        feedback_path=str(Path(data_dir) / "shadow_feedback.jsonl"),
        override_path=str(Path(data_dir) / "override_ledger.jsonl"),
        incident_path=str(Path(data_dir) / "incident_ledger.jsonl"),
    )
    payload = dashboard.build()
    if scope == "global":
        rec = payload.get("enforcement_recommendation", {})
        click.echo(f"\n  Global shadow runs: {payload.get('run_count', 0)}")
        click.echo(f"  Would-have-blocked: {payload.get('would_have_blocked', 0)}")
        click.echo(
            f"  Estimated savings: ${float(payload.get('estimated_savings_usd_total', 0.0) or 0.0):,.0f}"
        )
        click.echo(
            f"  Recommended tier: {rec.get('tier', 'TIER_0_SHADOW_ONLY')} — {rec.get('description', '')}\n"
        )
        return
    rows = payload.get("repo_trends" if scope == "repo" else "team_trends", [])[:limit]
    click.echo(f"\n  SemZero shadow {scope} trends")
    click.echo("  scope | runs | would-block | savings | recommended-tier")
    click.echo("  " + "-" * 74)
    for row in rows:
        key = row.get(scope, f"unknown_{scope}")
        rec = row.get("enforcement_recommendation", {})
        click.echo(
            f"  {key} | {row.get('run_count', 0)} | {row.get('would_have_blocked', 0)} | ${float(row.get('estimated_savings_usd_total', 0.0) or 0.0):,.0f} | {rec.get('tier', 'TIER_0_SHADOW_ONLY')}"
        )
    click.echo()


@cli.command("streaming-shadow")
@click.option("--before", "before_path", required=True, help="Before streaming/topic schema JSON")
@click.option("--after", "after_path", required=True, help="After streaming/topic schema JSON")
@click.option("--contracts", "contracts_path", default="", help="Consumer contract JSON")
@click.option(
    "--repo",
    envvar="SEMZERO_GITHUB_REPO",
    default="",
    help="Repository identifier for shadow trends",
)
@click.option(
    "--team", envvar="SEMZERO_DATA_TEAM", default="", help="Team identifier for shadow trends"
)
@click.option("--data-dir", default="data", show_default=True)
@click.option("--output", default="", help="Streaming gate JSON output. Defaults under --data-dir.")
@click.option(
    "--html-output", default="", help="Streaming HTML report output. Defaults under --data-dir."
)
def streaming_shadow_cmd(
    before_path, after_path, contracts_path, repo, team, data_dir, output, html_output
):
    """Run Kafka/streaming schema and consumer-contract checks in non-blocking shadow mode."""
    from .integrations.streaming_gate import (
        StreamingGate,
        load_streaming_json,
        save_streaming_report,
    )
    from .reliability.shadow_mode import ShadowDashboard, ShadowRunLedger

    before = load_streaming_json(before_path)
    after = load_streaming_json(after_path)
    contracts = load_streaming_json(contracts_path) if contracts_path else {}
    result = StreamingGate(before, after, contracts).evaluate(
        repo=repo, team=team, shadow_mode=True
    )

    data_root = Path(data_dir)
    output = output or str(data_root / "streaming_gate_result.json")
    html_output = html_output or str(data_root / "streaming_gate_report.html")
    save_streaming_report(result, output, html_output)

    ShadowRunLedger(path=str(data_root / "shadow_runs.jsonl")).record(
        result,
        artifact_paths={"streaming_gate": output, "streaming_report_html": html_output},
        team=team or result.get("team", ""),
        repo=repo or result.get("repo", ""),
    )
    dashboard = ShadowDashboard(
        shadow_runs_path=str(data_root / "shadow_runs.jsonl"),
        feedback_path=str(data_root / "shadow_feedback.jsonl"),
        override_path=str(data_root / "override_ledger.jsonl"),
        incident_path=str(data_root / "incident_ledger.jsonl"),
    )
    dashboard.save_json(str(data_root / "shadow_dashboard.json"))
    dashboard.save_html(str(data_root / "shadow_dashboard.html"))

    click.echo(f"\n  Streaming shadow verdict: {result.get('verdict', 'UNKNOWN')}")
    click.echo(f"  Findings: {result.get('streaming_summary', {}).get('finding_count', 0)}")
    click.echo(f"  JSON → {output}")
    click.echo(f"  HTML → {html_output}")
    click.echo(f"  Shadow dashboard → {data_root / 'shadow_dashboard.html'}\n")


@cli.command("ops-report")
@click.option("--gate", "gate_path", default=None, help="Path to gate_result.json")
@click.option("--wind-tunnel", "wind_path", default=None, help="Path to Wind Tunnel receipt JSON")
@click.option("--chaos", "chaos_path", default=None, help="Path to chaos report JSON")
@click.option("--markdown-out", default="data/unified_ops_report.md", show_default=True)
def ops_report(gate_path, wind_path, chaos_path, markdown_out):
    """Render a unified markdown report from Gate, Wind Tunnel, and Chaos outputs."""
    from semzero.reporting.live_report import UnifiedOpsReport

    report = UnifiedOpsReport(
        gate_result=_load_json(gate_path) if gate_path else None,
        wind_tunnel_receipt=_load_json(wind_path) if wind_path else None,
        chaos_report=_load_json(chaos_path) if chaos_path else None,
    )
    report.save_markdown(markdown_out)
    click.echo(f"\n  Unified report → {markdown_out}\n")



@cli.command("memory-init")
@click.option("--db", "db_path", default="data/semzero_memory.sqlite", show_default=True)
def memory_init_cmd(db_path):
    """Initialize the local SemZero memory/flywheel SQLite database."""
    from semzero.repo_understanding.repo_memory import SemZeroMemoryDB

    SemZeroMemoryDB(db_path).init()
    click.echo(f"SemZero memory DB initialized: {db_path}")


@cli.command("memory-ingest")
@click.option("--db", "db_path", default="data/semzero_memory.sqlite", show_default=True)
@click.option("--receipt", default="", help="Path to SemZero receipt.json.")
@click.option("--repo-snapshot", default="", help="Path to SemZero repo_snapshot.json.")
@click.option("--repo", default="", help="Repository identifier, e.g. owner/repo.")
@click.option("--pr-number", default="", help="Pull request number, if known.")
@click.option("--commit-sha", default="", help="Commit SHA, if known.")
@click.option("--action-sha", default="", help="SemZero Action SHA, if known.")
def memory_ingest_cmd(db_path, receipt, repo_snapshot, repo, pr_number, commit_sha, action_sha):
    """Ingest receipt/repo snapshot artifacts into the local SemZero memory DB."""
    from semzero.repo_understanding.repo_memory import SemZeroMemoryDB

    if not receipt and not repo_snapshot:
        raise click.ClickException("Provide --receipt and/or --repo-snapshot.")

    db = SemZeroMemoryDB(db_path)
    outputs = []

    if repo_snapshot:
        outputs.append(db.ingest_snapshot(repo_snapshot, repo=repo))

    if receipt:
        outputs.append(
            db.ingest_receipt(
                receipt,
                repo=repo,
                pr_number=pr_number,
                commit_sha=commit_sha,
                action_sha=action_sha,
            )
        )

    click.echo(json.dumps(outputs, indent=2, sort_keys=True))


@cli.command("memory-calibrate")
@click.option("--db", "db_path", default="data/semzero_memory.sqlite", show_default=True)
@click.option("--stable-id", required=True, help="Finding stable ID.")
@click.option(
    "--response",
    required=True,
    type=click.Choice(["agree", "fixed", "false_positive", "accepted_risk"]),
    help="Reviewer calibration response.",
)
@click.option("--repo", default="", help="Repository identifier.")
@click.option("--actor", default="", help="Reviewer/user who calibrated the finding.")
@click.option("--reason", default="", help="Optional reason.")
@click.option("--run-id", default="", help="Optional run ID to disambiguate repeated stable IDs.")
def memory_calibrate_cmd(db_path, stable_id, response, repo, actor, reason, run_id):
    """Record reviewer calibration against a SemZero finding."""
    from semzero.repo_understanding.repo_memory import SemZeroMemoryDB

    payload = SemZeroMemoryDB(db_path).record_calibration(
        stable_id=stable_id,
        response=response,
        repo=repo,
        actor=actor,
        reason=reason,
        run_id=run_id,
    )
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


@cli.command("memory-summary")
@click.option("--db", "db_path", default="data/semzero_memory.sqlite", show_default=True)
def memory_summary_cmd(db_path):
    """Summarize the local SemZero memory/flywheel database."""
    from semzero.repo_understanding.repo_memory import SemZeroMemoryDB

    click.echo(json.dumps(SemZeroMemoryDB(db_path).summary(), indent=2, sort_keys=True))


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
