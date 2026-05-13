from __future__ import annotations

from pathlib import Path

from semzero.reliability.assumption_replay_lite import run_replay_lite


def test_replay_lite_marks_local_fixture_evidence_without_credentials() -> None:
    result = run_replay_lite(
        {
            "families": {
                "temporal_bucket": {
                    "after_timezone_offset_hours": -5,
                    "events": [
                        {"event_ts": "2026-01-02T01:00:00Z"},
                        {"event_ts": "2026-01-02T12:00:00Z"},
                    ],
                }
            }
        },
        "temporal_bucket",
    )
    assert result["replay_ran"] is True
    assert result["evidence_source"] == "local_fixture_or_sample"
    assert result["requires_live_database"] is False
    assert result["requires_credentials"] is False
    assert "Using supplied local sample evidence" in result["summary"]
    assert "does not connect to a live warehouse" in result["honesty_note"]


def test_replay_lite_not_run_still_states_no_credentials() -> None:
    result = run_replay_lite({}, "temporal_bucket")
    assert result["replay_ran"] is False
    assert result["evidence_source"] == "none"
    assert result["requires_live_database"] is False
    assert result["requires_credentials"] is False


def test_credibility_docs_exist_and_define_boundaries() -> None:
    root = Path.cwd()
    replay = (root / "docs" / "REPLAY_LITE.md").read_text(encoding="utf-8")
    fp = (root / "docs" / "FALSE_POSITIVE_STRATEGY.md").read_text(encoding="utf-8")
    comp = (root / "docs" / "COMPETITIVE_POSITIONING.md").read_text(encoding="utf-8")
    naming = (root / "docs" / "NAMING_AND_TAGLINE.md").read_text(encoding="utf-8")

    assert "does not connect to a live warehouse" in replay
    assert "Warehouse auth required: no" in replay
    assert "No generic SQL-risk warnings" in fp
    assert "Data diffing" in comp and "Assumption classification" in comp
    assert "PR review for hidden assumptions in dbt changes" in naming
