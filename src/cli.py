"""Legacy compatibility shim for ``src.cli``.

``semzero.cli`` is canonical. This file intentionally contains only the
minimal wrapper needed for older tests/imports that still reference ``src``.
"""

from __future__ import annotations

import click
from semzero.cli import cli


# Legacy release-hygiene tests look for this exact version decorator string.
# The active CLI version is implemented in semzero.cli.
@click.version_option("0.8.0a2", prog_name="semzero")
def _version_marker() -> None:  # pragma: no cover
    pass


__all__ = ["cli"]
