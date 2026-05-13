"""Legacy module entrypoint shim.

Run ``python -m semzero`` for the canonical entrypoint. This keeps
``python -m src`` style legacy checks from breaking while avoiding a duplicate
implementation tree.
"""

from semzero.cli import cli

if __name__ == "__main__":
    cli()
