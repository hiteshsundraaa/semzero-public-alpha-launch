"""
test_change_gate.py — Test the Pre-merge Change Gate locally.

Simulates a PR containing breaking migration changes and shows
what the gate comment would look like.

Run from project root:
  python scripts/test_change_gate.py
"""

import json
from pathlib import Path
from semzero.integrations.change_gate import ChangeGate, GateConfig

print("\n  SemZero Change Gate Test")
print("  " + "─" * 44)

graph_path = Path("data/schema_graph.json")
if not graph_path.exists():
    print("\n  ✗ No schema_graph.json — run: python scripts/test_crawl.py\n")
    exit(1)

graph_json = json.loads(graph_path.read_text())
print(f"\n  Graph: {graph_json['meta']['table_count']} tables")

# Simulate a drift report with mixed change types
# This is what the watcher would produce after detecting a real migration
simulated_drift = {
    "detected_at": "2026-03-17T12:00:00Z",
    "summary": {
        "total_changes": 5,
        "by_severity": {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 1, "LOW": 1},
        "is_clean": False,
    },
    "events": [
        # DESTRUCTIVE — should BLOCK
        {
            "change_type": "COLUMN_REMOVED",
            "severity": "CRITICAL",
            "node_id": "orders.user_id",
            "before": {"dtype": "INTEGER", "nullable": False, "cardinality": 0.9},
            "after": None,
            "detail": "Column orders.user_id was dropped.",
        },
        # RENAME — should NEEDS_REVIEW
        {
            "change_type": "COLUMN_RENAMED",
            "severity": "HIGH",
            "node_id": "users.email",
            "before": {"dtype": "VARCHAR", "nullable": True, "cardinality": 0.95},
            "after": {"dtype": "VARCHAR", "nullable": True, "cardinality": 0.95},
            "detail": "Column users.email may have been renamed to users.email_address.",
        },
        # TYPE NARROWING — should BLOCK
        {
            "change_type": "TYPE_CHANGED",
            "severity": "HIGH",
            "node_id": "products.price",
            "before": {"dtype": "FLOAT", "nullable": True, "cardinality": 0.7},
            "after": {"dtype": "INTEGER", "nullable": True, "cardinality": 0.6},
            "detail": "Type: FLOAT → INTEGER on products.price.",
        },
        # NULLABLE HARDENING — should NEEDS_REVIEW
        {
            "change_type": "NULLABLE_CHANGED",
            "severity": "MEDIUM",
            "node_id": "orders.status",
            "before": {"dtype": "VARCHAR", "nullable": True},
            "after": {"dtype": "VARCHAR", "nullable": False},
            "detail": "Nullability: nullable=True → False on orders.status.",
        },
        # ADDITIVE SAFE — should pass
        {
            "change_type": "COLUMN_ADDED",
            "severity": "LOW",
            "node_id": "users.last_login_at",
            "before": None,
            "after": {"dtype": "TIMESTAMP", "nullable": True},
            "detail": "Column users.last_login_at added.",
        },
    ],
}

# Configure gate (dry run — no real GitHub API calls)
config = GateConfig(
    github_token="",  # Leave empty for local test
    github_repo="hiteshsundraaa/Semzero",
    block_on_destructive=True,
    block_on_narrowing=True,
    auto_patch_consumers=True,
    strict_mode=False,
)

gate = ChangeGate(graph_json, config)
result = gate.evaluate(simulated_drift, pr_number=99)
result.save("data/gate_result.json")

# Print verdict
v_col = {
    "SAFE": "\033[92m",
    "NEEDS_REVIEW": "\033[93m",
    "BLOCK": "\033[91m",
}
reset = "\033[0m"
bold = "\033[1m"

col = v_col.get(result.verdict.value, "")
print(f"\n  {bold}Verdict: {col}{result.verdict.value}{reset}")
print(f"  Gate ID: {result.gate_id}")
print(f"  Blast radius: {result.total_blast_radius} downstream nodes")
print()

# Assessments
for a in result.assessments:
    emoji = {
        "ADDITIVE_SAFE": "✅",
        "ADDITIVE_BREAKING": "⚠️",
        "RENAME_HIGH_CONFIDENCE": "🔄",
        "RENAME_LOW_CONFIDENCE": "⚠️",
        "DESTRUCTIVE_DELETE": "🚫",
        "TYPE_WIDENING": "✅",
        "TYPE_NARROWING": "🚫",
        "NULLABLE_HARDENING": "⚠️",
        "SEMANTIC_BREAKING": "🚫",
    }.get(a.compatibility.value, "❓")
    print(f"  {emoji} {a.node_id:<30} {a.compatibility.value:<25} blast={a.blast_radius}")

print()

# Show blocking reasons
if result.blocked_by:
    print(f"  \033[91mBlocking reasons:\033[0m")
    for b in result.blocked_by:
        print(f"    - {b}")

# Show rollout strategy for first blocking issue
blocking = result.blocking_assessments
if blocking:
    print(f"\n  Rollout strategy for `{blocking[0].node_id}`:")
    for step in blocking[0].rollout_strategy:
        print(f"    {step}")

# Print the full PR comment
print(f"\n  {'─' * 44}")
print(f"  PR Comment Preview (what SWEs would see):")
print(f"  {'─' * 44}\n")

comment = gate._build_comment(result)
lines = comment.split("\n")[:80]
for line in lines:
    print(f"  {line}")
if len(comment.split("\n")) > 80:
    print(f"  ... ({len(comment.split(chr(10))) - 80} more lines)")

print(f"\n  Gate result saved → data/gate_result.json\n")
