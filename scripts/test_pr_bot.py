"""
test_pr_bot.py — End-to-end test for the SemZero PR bot.

Before running:
  1. Set your GitHub token:
       export SEMZERO_GITHUB_TOKEN=ghp_xxxxxxxxxxxx

  2. Set your repo:
       export SEMZERO_GITHUB_REPO=your-org/your-repo

  3. Make sure you have run test_drift.py and test_repair.py first
     so data/drift_report.json, data/repair_plan.json,
     and data/migration.sql all exist.
"""

import json
import os
from pathlib import Path
from semzero.integrations.github_pr import PRBot

# ── Load existing outputs ─────────────────────────────────────────────────────
drift_report = json.loads(Path("data/drift_report.json").read_text())
repair_plan = json.loads(Path("data/repair_plan.json").read_text())
migration_sql = Path("data/migration.sql").read_text()

# ── Config ────────────────────────────────────────────────────────────────────
REPO = os.environ.get("SEMZERO_GITHUB_REPO", "your-org/your-repo")
REVIEWERS = []  # Add GitHub usernames: ["hiteshsundra"]
BASE = "main"

print(f"\n  Repo:      {REPO}")
print(f"  Base:      {BASE}")
print(f"  Changes:   {drift_report['summary']['total_changes']}")
print(f"  Actions:   {repair_plan['summary']['total_actions']}")
print()

# ── Run PR bot ────────────────────────────────────────────────────────────────
bot = PRBot(repo=REPO, base=BASE, reviewers=REVIEWERS)

result = bot.open_pr(
    drift_report=drift_report,
    repair_plan=repair_plan,
    migration_sql=migration_sql,
)

print(result)

if result.success:
    print(f"\n  Branch:    {result.branch}")
    print(f"  PR URL:    {result.pr_url}")
    print(f"  Draft:     {result.is_draft}")
    print()
    print("  Files committed:")
    print("    semzero/migration.sql")
    print("    semzero/repair_summary.json")
    print("    semzero/dbt_patches/*.yml  (if any)")
else:
    print(f"\n  Error: {result.error}")
