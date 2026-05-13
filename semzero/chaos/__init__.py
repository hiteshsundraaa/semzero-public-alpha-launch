"""SemZero Chaos Mode — Proactive Pipeline Resilience."""


def __getattr__(name):
    if name in ("ChaosEngine", "ChaosConfig", "ChaosReport"):
        import importlib

        m = importlib.import_module(".chaos_engine", package=__name__)
        return getattr(m, name)
    if name in (
        "MigrationWindTunnel",
        "WindTunnelConfig",
        "SimulationReceipt",
        "TunnelVerdict",
        "QueryStatus",
        "SemanticAnalyser",
        "CloneManager",
        "QueryExtractor",
        "QueryReplayer",
        "MigrationApplicator",
        "PatchGenerator",
    ):
        import importlib

        m = importlib.import_module(".wind_tunnel", package=__name__)
        return getattr(m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ChaosEngine",
    "ChaosConfig",
    "ChaosReport",
    "MigrationWindTunnel",
    "WindTunnelConfig",
    "SimulationReceipt",
    "TunnelVerdict",
    "QueryStatus",
]
