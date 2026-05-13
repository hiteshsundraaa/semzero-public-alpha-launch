"""SemZero Crawler — Schema graph builder and drift detector."""

# Lazy imports — connectors.py and builder.py depend on sqlalchemy.
# Import drift/graph_store directly since they are pure Python.
from .drift import SchemaDriftDetector, DriftReport, DriftEvent
from .graph_store import GraphStore


def __getattr__(name):
    if name == "SchemaGraphBuilder":
        from .builder import SchemaGraphBuilder

        return SchemaGraphBuilder
    if name == "DatabaseConnector":
        from .connectors import DatabaseConnector

        return DatabaseConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SchemaDriftDetector",
    "DriftReport",
    "DriftEvent",
    "GraphStore",
    "SchemaGraphBuilder",
    "DatabaseConnector",
]
