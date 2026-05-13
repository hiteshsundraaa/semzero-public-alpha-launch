"""Compatibility shim for legacy ``src.*`` imports.

SemZero 0.8 makes ``semzero`` the canonical package.  This shim keeps
older integrations/tests that import ``src.*`` working by resolving submodules
from the canonical ``semzero`` package path.  New code should import
``semzero.*`` directly.
"""

from __future__ import annotations

import semzero as _semzero

__version__ = getattr(_semzero, "__version__", "0.8.0a0")
__path__ = list(getattr(_semzero, "__path__", []))


def __getattr__(name: str):
    return getattr(_semzero, name)
