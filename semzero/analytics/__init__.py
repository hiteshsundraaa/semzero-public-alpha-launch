"""SemZero Analytics — Impact, RCA, Matching."""


def __getattr__(name):
    if name == "BlastRadiusAnalyzer":
        from .impact import BlastRadiusAnalyzer

        return BlastRadiusAnalyzer
    if name == "RCAAgent":
        from .rca import RCAAgent

        return RCAAgent
    if name == "SchemaColumnMatcher":
        from .matcher import SchemaColumnMatcher

        return SchemaColumnMatcher
    if name == "ColumnMatch":
        from .matcher import ColumnMatch

        return ColumnMatch
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BlastRadiusAnalyzer", "RCAAgent", "SchemaColumnMatcher", "ColumnMatch"]
