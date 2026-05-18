from __future__ import annotations

from semzero.repo_understanding.causality import _snapshot_dependency_signal


def _base_snapshot(downstream: bool = True, contracts: list[dict] | None = None) -> dict:
    return {
        "models": {
            "model.example.int_payment_summary": {
                "unique_id": "model.example.int_payment_summary",
                "name": "int_payment_summary",
                "sensitivity": {
                    "label": "REVENUE_CRITICAL",
                    "source": "inferred_pattern",
                },
                "contracts": contracts or [],
                "downstream": [
                    {
                        "unique_id": "model.example.mart_order_payments",
                        "name": "mart_order_payments",
                        "resource_type": "model",
                        "sensitivity": "REVENUE_CRITICAL",
                        "distance": 1,
                    }
                ]
                if downstream
                else [],
            }
        }
    }


def test_join_cardinality_does_not_use_generic_downstream_as_dependency_signal() -> None:
    finding = {
        "stable_id": "AG-JOIN",
        "family": "join_cardinality",
        "changed_resources": ["model.example.int_payment_summary"],
        "assumption_diff": {
            "has_explicit_before_after_diff": True,
            "pattern_type": "join_grain_or_fanout",
        },
        "blast_radius": [],
        "replay_fidelity": {"score": 0.52, "level": "low_static_fidelity"},
    }

    present, label, confidence, path, source, distance = _snapshot_dependency_signal(
        finding,
        repo_snapshot=_base_snapshot(downstream=True, contracts=[]),
        changed_resources=["model.example.int_payment_summary"],
    )

    assert present is False
    assert label == "UNKNOWN"
    assert confidence == 0.0
    assert path == []
    assert source == "repo_snapshot_no_property_specific_dependency_signal"
    assert distance == 999


def test_enum_domain_closure_can_use_generic_downstream_dependency_signal() -> None:
    finding = {
        "stable_id": "AG-ENUM",
        "family": "enum_domain_closure",
        "changed_resources": ["model.example.int_payment_summary"],
        "assumption_diff": {
            "has_explicit_before_after_diff": True,
            "pattern_type": "enum_domain_closure",
        },
        "blast_radius": [],
        "replay_fidelity": {"score": 0.58, "level": "medium_static_fidelity"},
    }

    present, label, confidence, path, source, distance = _snapshot_dependency_signal(
        finding,
        repo_snapshot=_base_snapshot(downstream=True, contracts=[]),
        changed_resources=["model.example.int_payment_summary"],
    )

    assert present is True
    assert label == "REVENUE_CRITICAL"
    assert confidence == 0.80
    assert path == [
        "model.example.int_payment_summary",
        "model.example.mart_order_payments",
    ]
    assert source == "repo_snapshot_downstream"
    assert distance == 1


def test_join_cardinality_can_use_property_specific_contract_signal() -> None:
    finding = {
        "stable_id": "AG-JOIN",
        "family": "join_cardinality",
        "changed_resources": ["model.example.int_payment_summary"],
        "assumption_diff": {
            "has_explicit_before_after_diff": True,
            "pattern_type": "join_grain_or_fanout",
        },
        "blast_radius": [],
        "replay_fidelity": {"score": 0.52, "level": "low_static_fidelity"},
    }

    present, label, confidence, path, source, distance = _snapshot_dependency_signal(
        finding,
        repo_snapshot=_base_snapshot(
            downstream=True,
            contracts=[
                {
                    "dependency_type": "grain",
                    "dependent_property": "customer_id unique",
                    "column": "customer_id",
                    "confidence": 0.75,
                    "upstream_model": "model.example.int_payment_summary",
                    "downstream_model": "model.example.mart_order_payments",
                }
            ],
        ),
        changed_resources=["model.example.int_payment_summary"],
    )

    assert present is True
    assert label == "REVENUE_CRITICAL"
    assert confidence == 0.60
    assert path == [
        "model.example.int_payment_summary",
        "model.example.mart_order_payments",
    ]
    assert source == "repo_snapshot_property_specific_contract"
    assert distance == 1
