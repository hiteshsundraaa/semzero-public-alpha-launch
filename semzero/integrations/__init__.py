"""SemZero Integrations — GitHub, Slack, Change Gate."""


def __getattr__(name):
    if name in (
        "ChangeGate",
        "GateConfig",
        "GateResult",
        "Verdict",
        "CompatibilityType",
        "CompatibilityOracle",
    ):
        from .change_gate import (
            ChangeGate,
            GateConfig,
            GateResult,
            Verdict,
            CompatibilityType,
            CompatibilityOracle,
        )

        return locals()[name]
    if name == "PRBot":
        from .github_pr import PRBot

        return PRBot
    if name == "SlackAlerter":
        from .slack import SlackAlerter

        return SlackAlerter
    if name == "ASTChangeProver":
        from .ast_proofing import ASTChangeProver

        return ASTChangeProver
    if name == "MergeCommentRenderer":
        from .pr_comments import MergeCommentRenderer

        return MergeCommentRenderer
    if name in {"GraphIntelligenceEngine", "GraphIntelligenceReport", "GraphNodeSignal"}:
        from .graph_intelligence import (
            GraphIntelligenceEngine,
            GraphIntelligenceReport,
            GraphNodeSignal,
        )

        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ChangeGate",
    "GateConfig",
    "GateResult",
    "Verdict",
    "CompatibilityType",
    "ASTChangeProver",
    "MergeCommentRenderer",
    "GraphIntelligenceEngine",
]
