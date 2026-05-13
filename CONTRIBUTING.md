# Contributing to SemZero

Thanks for considering a contribution.

## Development setup

```bash
git clone https://github.com/hirreshsundra3/semzero.git
cd semzero
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Quality checks

Before opening a PR, run:

```bash
ruff check .
ruff format --check .
mypy semzero semzero_lab
pytest
```

## Product focus

SemZero's public product surface is the **dbt PR Assumption Gate**.

Please keep new user-facing features focused on:

- dbt PR assumption detection
- assumption receipts
- Replay Lite validation
- blast-radius reporting
- GitHub PR review workflow
- feedback, exceptions, and calibration that reduce noise

Broader platform experiments should live behind experimental/lab documentation until they strengthen the dbt PR Assumption Gate directly.
