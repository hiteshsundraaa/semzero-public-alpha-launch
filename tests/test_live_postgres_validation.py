from pathlib import Path


def test_build_live_postgres_validation_pack_requires_postgres_url(tmp_path):
    from semzero.reliability.validation import build_live_postgres_validation_pack

    try:
        build_live_postgres_validation_pack(tmp_path / "demo", db_url="sqlite:////tmp/demo.db")
    except ValueError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-Postgres URL")


def test_validate_e2e_cli_exposes_live_pack_options():
    from semzero.cli import cli

    validate = cli.commands["validate-e2e"]
    option_names = {param.name for param in validate.params}
    assert "demo_backend" in option_names
    assert "demo_profile" in option_names
    assert "source_schema" in option_names
