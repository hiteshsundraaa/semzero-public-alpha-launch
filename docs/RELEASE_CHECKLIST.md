# Release Checklist

## Local verification

```bash
rm -rf dist build *.egg-info
python -m pip install --upgrade pip
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy semzero semzero_lab
pytest
python scripts/run_killer_demo.py
python -m build
twine check dist/*
```

## Fresh install verification

```bash
python -m venv /tmp/semzero-release-test
source /tmp/semzero-release-test/bin/activate
pip install dist/*.whl
semzero --help
semzero init-assumption-ci --output-dir /tmp/semzero-demo
```

## GitHub verification

- Tests workflow passes.
- Quality workflow passes.
- Release-check workflow passes.
- README quickstart works.
- Killer demo works.
- Version numbers match.
- No secrets or generated local artifacts are committed.
- Changelog is updated.
