from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SEMZERO_VERSION = "0.8.0a2"
SEMZERO_RELEASE_CHANNEL = "0.8.0-alpha"
SEMZERO_RELEASE_LINEAGE = [
    "0.00",
    "0.3.16",
    "0.3.17",
    "0.3.18",
    "0.5.2",
    "0.7.1",
    "0.7.2",
    "0.7.3",
    SEMZERO_VERSION,
]
SEMZERO_PHASES_COMPLETED = [
    "phase1_live_execution",
    "phase1_pr_native_ci",
    "phase1_dbt_jinja_ast",
    "phase1_release_hygiene",
    "phase1_ast_perf_hardening",
    "phase1_high_level_command_surface",
    "tier1_daily_use_and_composite_receipts",
    "tier2_row_diff_and_replay_fidelity",
    "tier3_feedback_ledgers_and_fix_guidance",
    "phase2_sql_dbt_compiler_lineage_core",
    "phase2_exact_provenance_receipts",
    "phase3_shadow_dashboard_and_feedback_calibration",
    "phase3_repo_team_trends_and_enforcement_recommendations",
    "phase3_streaming_shadow_schema_contracts",
    "v1_dbt_assumption_gate_wedge",
    "0.8.0-alpha_product_core_cleanup",
    "0.8.0-alpha_public_readiness_trust_layer",
]


@dataclass(frozen=True)
class ReleaseInfo:
    version: str = SEMZERO_VERSION
    channel: str = SEMZERO_RELEASE_CHANNEL
    lineage: tuple[str, ...] = tuple(SEMZERO_RELEASE_LINEAGE)
    phases_completed: tuple[str, ...] = tuple(SEMZERO_PHASES_COMPLETED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "channel": self.channel,
            "lineage": list(self.lineage),
            "phases_completed": list(self.phases_completed),
        }


__version__ = SEMZERO_VERSION
release_info = ReleaseInfo()
