from __future__ import annotations

from semzero.repo_understanding.sql_semantic_diff import (
    SEMANTIC_DIFF_EVENT_KIND,
    extract_clause_fallback_events,
    extract_sql_semantic_events,
    score_family_change_specificity,
)


def _by_type(events):
    return {event.event_type: event for event in events}


def test_group_by_key_added_emits_grain_event_with_receipt_shape():
    before = "select customer_id, count(*) as orders from orders group by customer_id"
    after = """
    select customer_id, payment_status, count(*) as orders
    from orders
    group by customer_id, payment_status
    """

    events = extract_clause_fallback_events(before, after, model="int_payment_summary")
    group_event = _by_type(events)["group_by_key_added"]
    payload = group_event.to_dict()

    assert payload["kind"] == SEMANTIC_DIFF_EVENT_KIND
    assert payload["event_type"] == "group_by_key_added"
    assert payload["family_hint"] == "grain_contract_drift"
    assert payload["model"] == "int_payment_summary"
    assert payload["location"]["clause"] == "GROUP_BY"
    assert payload["source"] == "clause_text_diff"
    assert payload["fidelity"] == 0.70
    assert "payment_status" in payload["changed_columns"]


def test_enum_only_change_scores_enum_not_join_or_grain():
    before = """
    select
      case when payment_status = 'completed' then 'paid' else 'pending' end
        as final_payment_status
    from payments
    """
    after = """
    select
      case when payment_status = 'completed' then 'paid' else 'unresolved' end
        as final_payment_status
    from payments
    """

    events = extract_clause_fallback_events(before, after)

    assert _by_type(events)["case_else_changed"].family_hint == "enum_domain_closure"
    assert score_family_change_specificity(events, "enum_domain_closure") > 0.60
    assert score_family_change_specificity(events, "join_relationship_drift") == 0.0
    assert score_family_change_specificity(events, "grain_contract_drift") == 0.0


def test_nested_case_else_change_scores_enum_for_repo3_payment_summary_shape():
    before = """
    WITH payment_summary AS (
        SELECT
            customer_id,
            COUNT(payment_id) AS total_payments,
            SUM(payment_value) AS total_paid,
            CASE
                WHEN SUM(CASE WHEN payment_status = 'completed' THEN 1 ELSE 0 END) > 0
                THEN 'paid'
                ELSE 'pending'
            END AS final_payment_status
        FROM {{ ref('stg_payments') }}
        GROUP BY customer_id
    )
    SELECT * FROM payment_summary
    """
    after = before.replace("ELSE 'pending'", "ELSE 'unresolved'")

    events = extract_clause_fallback_events(before, after, model="int_payment_summary")
    case_event = _by_type(events)["case_else_changed"]

    assert case_event.family_hint == "enum_domain_closure"
    assert case_event.before == "pending"
    assert case_event.after == "unresolved"
    assert "payment_status" in case_event.changed_columns
    assert score_family_change_specificity(events, "enum_domain_closure") > 0.60
    assert score_family_change_specificity(events, "join_relationship_drift") == 0.0


def test_join_key_change_is_property_specific():
    before = """
    select o.order_id, p.payment_id
    from orders o
    join payments p on o.customer_id = p.customer_id
    """
    after = """
    select o.order_id, p.payment_id
    from orders o
    join payments p on o.order_id = p.customer_id
    """

    events = extract_clause_fallback_events(before, after)
    join_event = _by_type(events)["join_key_changed"]

    assert join_event.family_hint == "join_relationship_drift"
    assert "order_id" in join_event.changed_columns
    assert "customer_id" in join_event.changed_columns
    assert score_family_change_specificity(events, "join_relationship_drift") > 0.60
    assert score_family_change_specificity(events, "enum_domain_closure") == 0.0


def test_metric_and_filter_changes_emit_separate_events():
    before = """
    select customer_id, sum(payment_value) as total_paid
    from payments
    where payment_status = 'completed'
    group by customer_id
    """
    after = """
    select customer_id, sum(payment_value - refund_amount) as total_paid
    from payments
    where payment_status != 'failed'
    group by customer_id
    """

    events = extract_clause_fallback_events(before, after)
    event_types = {event.event_type for event in events}

    assert "aggregate_argument_changed" in event_types
    assert "arithmetic_expression_changed" in event_types
    assert "where_predicate_changed" in event_types
    assert "status_population_changed" in event_types
    assert score_family_change_specificity(events, "metric_semantics_drift") > 0.60
    assert score_family_change_specificity(events, "filter_population_drift") > 0.60


def test_temporal_and_null_events_are_extracted():
    before = """
    select date(event_ts) as event_day, amount
    from payments
    where event_ts >= current_date - interval '7 day'
    """
    after = """
    select date(convert_timezone('UTC', 'America/New_York', event_ts)) as event_day,
           coalesce(amount, 0) as amount
    from payments
    where event_ts > current_date - interval '14 day'
    """

    events = extract_clause_fallback_events(before, after)
    event_types = {event.event_type for event in events}

    assert "timezone_conversion_changed" in event_types
    assert "coalesce_added" in event_types
    assert "window_size_changed" in event_types
    assert "inclusive_bound_changed" in event_types
    assert score_family_change_specificity(events, "temporal_bucket") > 0.60
    assert score_family_change_specificity(events, "null_default_fallback") > 0.50


def test_extract_sql_semantic_events_degrades_honestly_when_parser_is_unavailable_or_fails():
    before = "select customer_id, count(*) as orders from orders group by customer_id"
    after = """
    select customer_id, payment_status, count(*) as orders
    from orders
    group by customer_id, payment_status
    """

    events = extract_sql_semantic_events(before, after)

    assert any(event.event_type == "group_by_key_added" for event in events)
    assert all(event.fidelity in {0.70, 0.95} for event in events)
    assert all(event.source in {"clause_text_diff", "sqlglot_ast_diff"} for event in events)
