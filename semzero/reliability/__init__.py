"""SemZero reliability package.

Heavy workflow/validation modules are loaded lazily so lightweight commands such as
shadow dashboards and streaming checks do not import database/runtime dependencies.
"""

from __future__ import annotations

__all__ = [
    "PremergeBundle",
    "PremergeWorkflow",
    "PremergeWorkflowConfig",
    "DemoValidationPack",
    "ValidationConfig",
    "ValidationHarness",
    "ValidationReport",
    "build_demo_validation_pack",
]


def __getattr__(name):
    if name in {"PremergeBundle", "PremergeWorkflow", "PremergeWorkflowConfig"}:
        from .premerge import PremergeBundle, PremergeWorkflow, PremergeWorkflowConfig

        return locals()[name]
    if name in {
        "DemoValidationPack",
        "ValidationConfig",
        "ValidationHarness",
        "ValidationReport",
        "build_demo_validation_pack",
    }:
        from .validation import (
            DemoValidationPack,
            ValidationConfig,
            ValidationHarness,
            ValidationReport,
            build_demo_validation_pack,
        )

        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
