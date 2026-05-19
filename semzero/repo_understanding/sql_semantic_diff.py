from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


SEMANTIC_DIFF_EVENT_KIND = "semzero_sql_semantic_diff_event_v1"

FIDELITY_SQLGLOT_AST = 0.95
FIDELITY_RAW_SQL_PARSE = 0.85
FIDELITY_CLAUSE_FALLBACK = 0.70
FIDELITY_RAW_TRIGGER = 0.40

FAMILY_EVENT_WEIGHTS: dict[str, dict[str, float]] = {
    "schema_contract_break": {
        "selected_column_removed": 1.0,
        "selected_column_renamed": 0.9,
        "selected_alias_changed": 0.75,
        "selected_expression_changed": 0.65,
        "type_cast_changed": 0.7,
    },
    "grain_contract_drift": {
        "group_by_key_added": 1.0,
        "group_by_key_removed": 1.0,
        "group_by_key_replaced": 1.0,
        "distinct_added": 0.7,
        "distinct_removed": 0.8,
        "row_number_partition_changed": 0.9,
    },
    "join_relationship_drift": {
        "join_key_changed": 1.0,
        "join_target_changed": 0.9,
        "join_type_changed": 0.75,
        "join_predicate_weakened": 0.85,
        "join_added": 0.55,
        "join_removed": 0.65,
    },
    "join_cardinality": {
        "join_key_changed": 0.9,
        "join_target_changed": 0.8,
        "join_type_changed": 0.7,
        "join_predicate_weakened": 0.85,
        "group_by_key_added": 0.72,
        "group_by_key_removed": 0.72,
        "distinct_removed": 0.65,
    },
    "metric_semantics_drift": {
        "aggregate_function_changed": 1.0,
        "aggregate_argument_changed": 1.0,
        "distinctness_changed": 0.85,
        "arithmetic_expression_changed": 0.8,
        "metric_numerator_changed": 0.9,
        "metric_denominator_changed": 0.9,
    },
    "filter_population_drift": {
        "where_predicate_added": 0.75,
        "where_predicate_removed": 0.9,
        "where_predicate_changed": 1.0,
        "date_window_changed": 0.9,
        "soft_delete_filter_removed": 0.9,
        "test_user_filter_removed": 0.85,
        "status_population_changed": 0.85,
    },
    "enum_domain_closure": {
        "case_else_changed": 1.0,
        "case_branch_added": 0.8,
        "case_branch_removed": 0.9,
        "in_list_added_value": 0.8,
        "in_list_removed_value": 0.9,
    },
    "null_default_fallback": {
        "coalesce_added": 0.85,
        "coalesce_removed": 0.9,
        "null_filter_changed": 0.8,
    },
    "temporal_bucket": {
        "date_column_changed": 0.85,
        "date_trunc_grain_changed": 1.0,
        "window_size_changed": 0.8,
        "timezone_conversion_changed": 1.0,
        "inclusive_bound_changed": 0.75,
    },
}

SQL_KEYWORDS = {
    "as",
    "and",
    "or",
    "not",
    "null",
    "true",
    "false",
    "case",
    "when",
    "then",
    "else",
    "end",
    "select",
    "from",
    "where",
    "group",
    "by",
    "having",
    "order",
    "limit",
    "join",
    "left",
    "right",
    "full",
    "inner",
    "outer",
    "on",
    "in",
    "is",
    "distinct",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "date",
    "date_trunc",
    "timestamp_trunc",
    "cast",
    "convert_timezone",
    "timezone",
    "interval",
}

IDENTIFIER_RE = re.compile(r"\b[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?\b")
AGG_RE = re.compile(r"\b(count|sum|avg|min|max|median)\s*\((.*?)\)", re.I | re.S)
CASE_TOKEN_RE = re.compile(r"\b(case|when|then|else|end)\b", re.I)
COALESCE_RE = re.compile(r"\b(coalesce|ifnull|nvl)\s*\((.*?)\)", re.I | re.S)
IN_LIST_RE = re.compile(
    r"\b([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)\s+in\s*\((.*?)\)", re.I | re.S
)
DATE_CALL_RE = re.compile(
    r"\b(date|to_date)\s*\(\s*([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)", re.I
)
DATE_TRUNC_RE = re.compile(
    r"\b(?:date_trunc|timestamp_trunc)\s*\(\s*['\"]?(\w+)['\"]?\s*,\s*([^)]+)\)",
    re.I | re.S,
)
TIMEZONE_RE = re.compile(r"\b(convert_timezone|timezone|at\s+time\s+zone)\b", re.I)
INTERVAL_RE = re.compile(r"\binterval\s+['\"]?(\d+)\s+(\w+)['\"]?", re.I)
BOUND_RE = re.compile(r"([a-zA-Z_][\w\.]*)\s*(<=|<|>=|>)\s*([^)\s]+)", re.I)


@dataclass(frozen=True, slots=True)
class SemanticDiffEvent:
    event_type: str
    family_hint: str
    model: str = ""
    before: Any = None
    after: Any = None
    changed_columns: tuple[str, ...] = ()
    clause: str = ""
    cte: str | None = None
    confidence: float = 0.0
    fidelity: float = 0.0
    source: str = ""
    raw_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": SEMANTIC_DIFF_EVENT_KIND,
            "event_type": self.event_type,
            "family_hint": self.family_hint,
            "model": self.model,
            "before": self.before,
            "after": self.after,
            "changed_columns": list(self.changed_columns),
            "location": {"clause": self.clause, "cte": self.cte},
            "confidence": self.confidence,
            "fidelity": self.fidelity,
            "source": self.source,
            "raw_excerpt": self.raw_excerpt,
        }


@dataclass(frozen=True, slots=True)
class _SelectItem:
    expr: str
    alias: str
    raw: str
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _JoinItem:
    join_type: str
    target: str
    predicate: str
    raw: str
    columns: tuple[str, ...]


def extract_sql_semantic_events(
    before_sql: str,
    after_sql: str,
    *,
    dialect: str | None = None,
    model: str = "",
) -> list[SemanticDiffEvent]:
    """Extract normalized semantic events from before/after SQL.

    sqlglot is used opportunistically when available. If parsing fails or the
    package is absent, the function returns clause-aware fallback events with a
    lower fidelity score instead of pretending the evidence is AST-grade.
    """
    before_sql = str(before_sql or "")
    after_sql = str(after_sql or "")
    if not before_sql.strip() and not after_sql.strip():
        return []

    parsed = _normalize_with_sqlglot(before_sql, after_sql, dialect=dialect)
    if parsed:
        before_norm, after_norm = parsed
        return _extract_clause_events(
            before_norm,
            after_norm,
            model=model,
            source="sqlglot_ast_diff",
            fidelity=FIDELITY_SQLGLOT_AST,
            base_confidence=0.95,
        )

    return extract_clause_fallback_events(before_sql, after_sql, model=model)


def extract_clause_fallback_events(
    before_sql: str,
    after_sql: str,
    *,
    model: str = "",
) -> list[SemanticDiffEvent]:
    return _extract_clause_events(
        before_sql,
        after_sql,
        model=model,
        source="clause_text_diff",
        fidelity=FIDELITY_CLAUSE_FALLBACK,
        base_confidence=0.88,
    )


def score_family_change_specificity(
    events: list[SemanticDiffEvent],
    family: str,
    *,
    event_weights: dict[str, dict[str, float]] | None = None,
) -> float:
    weights = (event_weights or FAMILY_EVENT_WEIGHTS).get(family, {})
    best = 0.0
    for event in events:
        weight = weights.get(event.event_type, 0.0)
        if not weight:
            continue
        best = max(best, event.fidelity * event.confidence * weight)
    return round(best, 4)


def _normalize_with_sqlglot(
    before_sql: str, after_sql: str, *, dialect: str | None = None
) -> tuple[str, str] | None:
    try:
        import sqlglot  # type: ignore
    except Exception:
        return None
    try:
        before_tree = sqlglot.parse_one(before_sql, read=dialect) if dialect else sqlglot.parse_one(before_sql)
        after_tree = sqlglot.parse_one(after_sql, read=dialect) if dialect else sqlglot.parse_one(after_sql)
        return before_tree.sql(dialect=dialect), after_tree.sql(dialect=dialect)
    except Exception:
        return None


def _extract_clause_events(
    before_sql: str,
    after_sql: str,
    *,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    before = _normalize_sql(before_sql)
    after = _normalize_sql(after_sql)
    if before == after:
        return []

    events: list[SemanticDiffEvent] = []
    events.extend(_selected_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    events.extend(_grain_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    events.extend(_join_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    events.extend(_metric_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    events.extend(_filter_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    events.extend(_enum_null_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    events.extend(_temporal_events(before_sql, after_sql, model, source, fidelity, base_confidence))
    return _dedupe_events(events)


def _selected_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    before_items = _select_items(before_sql)
    after_items = _select_items(after_sql)
    before_by_alias = {item.alias: item for item in before_items if item.alias}
    after_by_alias = {item.alias: item for item in after_items if item.alias}
    before_exprs = {item.expr: item for item in before_items}
    after_exprs = {item.expr: item for item in after_items}
    events: list[SemanticDiffEvent] = []

    for alias in sorted(set(before_by_alias) & set(after_by_alias)):
        old = before_by_alias[alias]
        new = after_by_alias[alias]
        if old.expr != new.expr:
            event_type = "type_cast_changed" if _cast_changed(old.expr, new.expr) else "selected_expression_changed"
            events.append(
                _event(
                    event_type,
                    "schema_contract_break",
                    model,
                    old.raw,
                    new.raw,
                    _changed_columns(old.raw + " " + new.raw),
                    "SELECT",
                    base_confidence,
                    fidelity,
                    source,
                    new.raw,
                )
            )

    for expr in sorted(set(before_exprs) & set(after_exprs)):
        old = before_exprs[expr]
        new = after_exprs[expr]
        if old.alias and new.alias and old.alias != new.alias:
            events.append(
                _event(
                    "selected_alias_changed",
                    "schema_contract_break",
                    model,
                    old.alias,
                    new.alias,
                    old.columns or new.columns,
                    "SELECT",
                    base_confidence,
                    fidelity,
                    source,
                    new.raw,
                )
            )

    removed = [item for item in before_items if item.expr not in after_exprs and item.alias not in after_by_alias]
    added = [item for item in after_items if item.expr not in before_exprs and item.alias not in before_by_alias]
    for item in removed:
        events.append(
            _event(
                "selected_column_removed",
                "schema_contract_break",
                model,
                item.raw,
                None,
                item.columns,
                "SELECT",
                base_confidence,
                fidelity,
                source,
                item.raw,
            )
        )
    for item in added:
        events.append(
            _event(
                "selected_column_added",
                "schema_contract_break",
                model,
                None,
                item.raw,
                item.columns,
                "SELECT",
                base_confidence * 0.9,
                fidelity,
                source,
                item.raw,
            )
        )
    return events


def _grain_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    before_group = _group_keys(before_sql)
    after_group = _group_keys(after_sql)
    events: list[SemanticDiffEvent] = []
    before_set = set(before_group)
    after_set = set(after_group)
    added = [item for item in after_group if item not in before_set]
    removed = [item for item in before_group if item not in after_set]
    if added:
        events.append(
            _event(
                "group_by_key_added",
                "grain_contract_drift",
                model,
                before_group,
                after_group,
                _changed_columns(" ".join(added)),
                "GROUP_BY",
                base_confidence,
                fidelity,
                source,
                "GROUP BY " + ", ".join(after_group),
            )
        )
    if removed:
        events.append(
            _event(
                "group_by_key_removed",
                "grain_contract_drift",
                model,
                before_group,
                after_group,
                _changed_columns(" ".join(removed)),
                "GROUP_BY",
                base_confidence,
                fidelity,
                source,
                "GROUP BY " + ", ".join(after_group),
            )
        )
    if before_group and after_group and not added and not removed and before_group != after_group:
        events.append(
            _event(
                "group_by_key_replaced",
                "grain_contract_drift",
                model,
                before_group,
                after_group,
                _changed_columns(" ".join(before_group + after_group)),
                "GROUP_BY",
                base_confidence,
                fidelity,
                source,
                "GROUP BY " + ", ".join(after_group),
            )
        )
    before_distinct = _has_select_distinct(before_sql)
    after_distinct = _has_select_distinct(after_sql)
    if before_distinct and not after_distinct:
        events.append(
            _event(
                "distinct_removed",
                "grain_contract_drift",
                model,
                True,
                False,
                (),
                "SELECT",
                base_confidence,
                fidelity,
                source,
                _select_clause(after_sql) or after_sql[:160],
            )
        )
    elif after_distinct and not before_distinct:
        events.append(
            _event(
                "distinct_added",
                "grain_contract_drift",
                model,
                False,
                True,
                (),
                "SELECT",
                base_confidence * 0.9,
                fidelity,
                source,
                _select_clause(after_sql) or after_sql[:160],
            )
        )
    return events


def _join_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    before_joins = _joins(before_sql)
    after_joins = _joins(after_sql)
    events: list[SemanticDiffEvent] = []
    before_by_target = {join.target: join for join in before_joins}
    after_by_target = {join.target: join for join in after_joins}

    for target in sorted(set(before_by_target) & set(after_by_target)):
        old = before_by_target[target]
        new = after_by_target[target]
        if old.join_type != new.join_type:
            events.append(
                _event(
                    "join_type_changed",
                    "join_relationship_drift",
                    model,
                    old.join_type,
                    new.join_type,
                    old.columns + new.columns,
                    "JOIN",
                    base_confidence,
                    fidelity,
                    source,
                    new.raw,
                )
            )
        if old.predicate != new.predicate:
            events.append(
                _event(
                    "join_key_changed",
                    "join_relationship_drift",
                    model,
                    old.predicate,
                    new.predicate,
                    _changed_columns(old.predicate + " " + new.predicate),
                    "JOIN",
                    base_confidence,
                    fidelity,
                    source,
                    new.raw,
                )
            )
            if _predicate_weakened(old.predicate, new.predicate):
                events.append(
                    _event(
                        "join_predicate_weakened",
                        "join_relationship_drift",
                        model,
                        old.predicate,
                        new.predicate,
                        _changed_columns(old.predicate + " " + new.predicate),
                        "JOIN",
                        base_confidence * 0.95,
                        fidelity,
                        source,
                        new.raw,
                    )
                )

    before_targets = set(before_by_target)
    after_targets = set(after_by_target)
    for join in after_joins:
        if join.target not in before_targets:
            events.append(
                _event(
                    "join_added",
                    "join_relationship_drift",
                    model,
                    None,
                    join.raw,
                    join.columns,
                    "JOIN",
                    base_confidence * 0.85,
                    fidelity,
                    source,
                    join.raw,
                )
            )
    for join in before_joins:
        if join.target not in after_targets:
            events.append(
                _event(
                    "join_removed",
                    "join_relationship_drift",
                    model,
                    join.raw,
                    None,
                    join.columns,
                    "JOIN",
                    base_confidence * 0.85,
                    fidelity,
                    source,
                    join.raw,
                )
            )

    for index, old in enumerate(before_joins):
        if index >= len(after_joins):
            continue
        new = after_joins[index]
        if old.target != new.target and old.target in before_targets and new.target in after_targets:
            events.append(
                _event(
                    "join_target_changed",
                    "join_relationship_drift",
                    model,
                    old.target,
                    new.target,
                    old.columns + new.columns,
                    "JOIN",
                    base_confidence,
                    fidelity,
                    source,
                    new.raw,
                )
            )
    return events


def _metric_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    before_metrics = _metric_items(before_sql)
    after_metrics = _metric_items(after_sql)
    events: list[SemanticDiffEvent] = []
    for alias in sorted(set(before_metrics) & set(after_metrics)):
        old = before_metrics[alias]
        new = after_metrics[alias]
        if old["function"] != new["function"]:
            events.append(
                _event(
                    "aggregate_function_changed",
                    "metric_semantics_drift",
                    model,
                    old["raw"],
                    new["raw"],
                    _changed_columns(old["raw"] + " " + new["raw"]),
                    "SELECT",
                    base_confidence,
                    fidelity,
                    source,
                    new["raw"],
                )
            )
        if old["argument"] != new["argument"]:
            events.append(
                _event(
                    "aggregate_argument_changed",
                    "metric_semantics_drift",
                    model,
                    old["raw"],
                    new["raw"],
                    _changed_columns(old["argument"] + " " + new["argument"]),
                    "SELECT",
                    base_confidence,
                    fidelity,
                    source,
                    new["raw"],
                )
            )
        if old["distinct"] != new["distinct"]:
            events.append(
                _event(
                    "distinctness_changed",
                    "metric_semantics_drift",
                    model,
                    old["raw"],
                    new["raw"],
                    _changed_columns(old["argument"] + " " + new["argument"]),
                    "SELECT",
                    base_confidence,
                    fidelity,
                    source,
                    new["raw"],
                )
            )

    before_by_alias = {item.alias: item for item in _select_items(before_sql) if item.alias}
    after_by_alias = {item.alias: item for item in _select_items(after_sql) if item.alias}
    for alias in sorted(set(before_by_alias) & set(after_by_alias)):
        old = before_by_alias[alias]
        new = after_by_alias[alias]
        if old.expr != new.expr and _has_arithmetic(old.expr + " " + new.expr):
            events.append(
                _event(
                    "arithmetic_expression_changed",
                    "metric_semantics_drift",
                    model,
                    old.raw,
                    new.raw,
                    _changed_columns(old.raw + " " + new.raw),
                    "SELECT",
                    base_confidence * 0.9,
                    fidelity,
                    source,
                    new.raw,
                )
            )
    return events


def _filter_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    before_where = _normalize_sql(_where_clause(before_sql))
    after_where = _normalize_sql(_where_clause(after_sql))
    events: list[SemanticDiffEvent] = []
    if before_where == after_where:
        return events
    if before_where and after_where:
        events.append(
            _event(
                "where_predicate_changed",
                "filter_population_drift",
                model,
                before_where,
                after_where,
                _changed_columns(before_where + " " + after_where),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                after_where,
            )
        )
    elif after_where:
        events.append(
            _event(
                "where_predicate_added",
                "filter_population_drift",
                model,
                None,
                after_where,
                _changed_columns(after_where),
                "WHERE",
                base_confidence * 0.85,
                fidelity,
                source,
                after_where,
            )
        )
    elif before_where:
        events.append(
            _event(
                "where_predicate_removed",
                "filter_population_drift",
                model,
                before_where,
                None,
                _changed_columns(before_where),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                before_where,
            )
        )
    if _date_window_changed(before_where, after_where):
        events.append(
            _event(
                "date_window_changed",
                "filter_population_drift",
                model,
                before_where,
                after_where,
                _changed_columns(before_where + " " + after_where),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                after_where,
            )
        )
    if _status_population_changed(before_where, after_where):
        events.append(
            _event(
                "status_population_changed",
                "filter_population_drift",
                model,
                before_where,
                after_where,
                _changed_columns(before_where + " " + after_where),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                after_where,
            )
        )
    if before_where and not after_where and "deleted" in before_where:
        events.append(
            _event(
                "soft_delete_filter_removed",
                "filter_population_drift",
                model,
                before_where,
                None,
                _changed_columns(before_where),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                before_where,
            )
        )
    if before_where and not after_where and re.search(r"\b(test|is_test|dummy)\b", before_where):
        events.append(
            _event(
                "test_user_filter_removed",
                "filter_population_drift",
                model,
                before_where,
                None,
                _changed_columns(before_where),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                before_where,
            )
        )
    return events


def _enum_null_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    events: list[SemanticDiffEvent] = []
    before_cases = _case_summaries(before_sql)
    after_cases = _case_summaries(after_sql)
    for index, old in enumerate(before_cases):
        if index >= len(after_cases):
            continue
        new = after_cases[index]
        if old["else"] != new["else"]:
            events.append(
                _event(
                    "case_else_changed",
                    "enum_domain_closure",
                    model,
                    old["else"],
                    new["else"],
                    _changed_columns(old["raw"] + " " + new["raw"]),
                    "CASE",
                    base_confidence,
                    fidelity,
                    source,
                    new["raw"],
                )
            )
        old_branches = set(old["branches"])
        new_branches = set(new["branches"])
        for branch in sorted(new_branches - old_branches):
            events.append(
                _event(
                    "case_branch_added",
                    "enum_domain_closure",
                    model,
                    None,
                    branch,
                    _changed_columns(branch),
                    "CASE",
                    base_confidence * 0.9,
                    fidelity,
                    source,
                    new["raw"],
                )
            )
        for branch in sorted(old_branches - new_branches):
            events.append(
                _event(
                    "case_branch_removed",
                    "enum_domain_closure",
                    model,
                    branch,
                    None,
                    _changed_columns(branch),
                    "CASE",
                    base_confidence,
                    fidelity,
                    source,
                    old["raw"],
                )
            )

    before_lists = _in_lists(before_sql)
    after_lists = _in_lists(after_sql)
    for column in sorted(set(before_lists) & set(after_lists)):
        old_values = before_lists[column]
        new_values = after_lists[column]
        for value in sorted(new_values - old_values):
            events.append(
                _event(
                    "in_list_added_value",
                    "enum_domain_closure",
                    model,
                    sorted(old_values),
                    sorted(new_values),
                    (column,),
                    "WHERE",
                    base_confidence,
                    fidelity,
                    source,
                    f"{column} IN ({', '.join(sorted(new_values))})",
                )
            )
        for value in sorted(old_values - new_values):
            events.append(
                _event(
                    "in_list_removed_value",
                    "enum_domain_closure",
                    model,
                    sorted(old_values),
                    sorted(new_values),
                    (column,),
                    "WHERE",
                    base_confidence,
                    fidelity,
                    source,
                    f"{column} IN ({', '.join(sorted(new_values))})",
                )
            )

    before_coalesce = _coalesce_calls(before_sql)
    after_coalesce = _coalesce_calls(after_sql)
    for call in sorted(after_coalesce - before_coalesce):
        events.append(
            _event(
                "coalesce_added",
                "null_default_fallback",
                model,
                None,
                call,
                _changed_columns(call),
                "SELECT",
                base_confidence,
                fidelity,
                source,
                call,
            )
        )
    for call in sorted(before_coalesce - after_coalesce):
        events.append(
            _event(
                "coalesce_removed",
                "null_default_fallback",
                model,
                call,
                None,
                _changed_columns(call),
                "SELECT",
                base_confidence,
                fidelity,
                source,
                call,
            )
        )
    before_null = _null_filters(before_sql)
    after_null = _null_filters(after_sql)
    if before_null != after_null and (before_null or after_null):
        events.append(
            _event(
                "null_filter_changed",
                "null_default_fallback",
                model,
                sorted(before_null),
                sorted(after_null),
                _changed_columns(" ".join(before_null | after_null)),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                _where_clause(after_sql) or _where_clause(before_sql),
            )
        )
    return events


def _temporal_events(
    before_sql: str,
    after_sql: str,
    model: str,
    source: str,
    fidelity: float,
    base_confidence: float,
) -> list[SemanticDiffEvent]:
    events: list[SemanticDiffEvent] = []
    before_tz = bool(TIMEZONE_RE.search(before_sql))
    after_tz = bool(TIMEZONE_RE.search(after_sql))
    if before_tz != after_tz or (
        before_tz and after_tz and _timezone_excerpt(before_sql) != _timezone_excerpt(after_sql)
    ):
        events.append(
            _event(
                "timezone_conversion_changed",
                "temporal_bucket",
                model,
                _timezone_excerpt(before_sql),
                _timezone_excerpt(after_sql),
                _changed_columns(before_sql + " " + after_sql),
                "SELECT",
                base_confidence,
                fidelity,
                source,
                _timezone_excerpt(after_sql) or after_sql[:160],
            )
        )

    before_trunc = _date_trunc_calls(before_sql)
    after_trunc = _date_trunc_calls(after_sql)
    for key in sorted(set(before_trunc) & set(after_trunc)):
        if before_trunc[key] != after_trunc[key]:
            events.append(
                _event(
                    "date_trunc_grain_changed",
                    "temporal_bucket",
                    model,
                    before_trunc[key],
                    after_trunc[key],
                    _changed_columns(key),
                    "SELECT",
                    base_confidence,
                    fidelity,
                    source,
                    f"DATE_TRUNC({after_trunc[key]}, {key})",
                )
            )

    before_dates = _date_columns(before_sql)
    after_dates = _date_columns(after_sql)
    if before_dates != after_dates and (before_dates or after_dates):
        events.append(
            _event(
                "date_column_changed",
                "temporal_bucket",
                model,
                sorted(before_dates),
                sorted(after_dates),
                tuple(sorted(before_dates ^ after_dates)),
                "SELECT",
                base_confidence,
                fidelity,
                source,
                ", ".join(sorted(after_dates)) or ", ".join(sorted(before_dates)),
            )
        )

    before_window = _window_size(before_sql)
    after_window = _window_size(after_sql)
    if before_window and after_window and before_window != after_window:
        events.append(
            _event(
                "window_size_changed",
                "temporal_bucket",
                model,
                before_window,
                after_window,
                _changed_columns(before_sql + " " + after_sql),
                "WHERE",
                base_confidence,
                fidelity,
                source,
                str(after_window),
            )
        )

    before_bounds = _bounds(before_sql)
    after_bounds = _bounds(after_sql)
    if before_bounds and after_bounds and before_bounds != after_bounds:
        events.append(
            _event(
                "inclusive_bound_changed",
                "temporal_bucket",
                model,
                sorted(before_bounds),
                sorted(after_bounds),
                _changed_columns(" ".join(before_bounds | after_bounds)),
                "WHERE",
                base_confidence * 0.85,
                fidelity,
                source,
                _where_clause(after_sql) or after_sql[:160],
            )
        )
    return events


def _event(
    event_type: str,
    family_hint: str,
    model: str,
    before: Any,
    after: Any,
    changed_columns: tuple[str, ...],
    clause: str,
    confidence: float,
    fidelity: float,
    source: str,
    raw_excerpt: str,
) -> SemanticDiffEvent:
    return SemanticDiffEvent(
        event_type=event_type,
        family_hint=family_hint,
        model=model,
        before=before,
        after=after,
        changed_columns=_unique_tuple(changed_columns),
        clause=clause,
        confidence=_bounded(confidence),
        fidelity=_bounded(fidelity),
        source=source,
        raw_excerpt=_normalize_sql(str(raw_excerpt or ""))[:500],
    )


def _normalize_sql(sql: str) -> str:
    text = _strip_comments(str(sql or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--.*?$", " ", sql, flags=re.M)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)


def _select_clause(sql: str) -> str:
    return _extract_clause(sql, r"\bselect\b", [r"\bfrom\b"])


def _where_clause(sql: str) -> str:
    return _extract_clause(
        sql,
        r"\bwhere\b",
        [r"\bgroup\s+by\b", r"\bhaving\b", r"\border\s+by\b", r"\blimit\b", r"\bqualify\b"],
    )


def _group_clause(sql: str) -> str:
    return _extract_clause(sql, r"\bgroup\s+by\b", [r"\bhaving\b", r"\border\s+by\b", r"\blimit\b"])


def _extract_clause(sql: str, start_pattern: str, end_patterns: list[str]) -> str:
    text = _strip_comments(sql)
    start = re.search(start_pattern, text, flags=re.I)
    if not start:
        return ""
    end = len(text)
    remainder = text[start.end() :]
    for pattern in end_patterns:
        match = re.search(pattern, remainder, flags=re.I)
        if match:
            end = min(end, start.end() + match.start())
    return text[start.end() : end].strip()


def _split_top_level_csv(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(depth - 1, 0)
            current.append(char)
        elif char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(char)
        index += 1
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _select_items(sql: str) -> list[_SelectItem]:
    clause = _select_clause(sql)
    if not clause:
        return []
    clause = re.sub(r"^\s*distinct\s+", "", clause, flags=re.I)
    out: list[_SelectItem] = []
    for raw in _split_top_level_csv(clause):
        expr, alias = _split_alias(raw)
        expr_norm = _normalize_sql(expr)
        alias_norm = _normalize_identifier(alias or expr)
        out.append(
            _SelectItem(
                expr=expr_norm,
                alias=alias_norm,
                raw=_normalize_raw(raw),
                columns=_changed_columns(expr),
            )
        )
    return out


def _split_alias(raw: str) -> tuple[str, str]:
    text = raw.strip()
    match = re.search(r"\s+as\s+([`\"']?[a-zA-Z_][\w]*[`\"']?)\s*$", text, flags=re.I)
    if match:
        return text[: match.start()].strip(), _strip_quotes(match.group(1))
    tail = re.search(r"\s+([`\"']?[a-zA-Z_][\w]*[`\"']?)\s*$", text)
    if tail and not text[: tail.start()].strip().endswith(")"):
        prefix = text[: tail.start()].strip()
        if prefix and not re.fullmatch(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?", prefix):
            return prefix, _strip_quotes(tail.group(1))
    simple = re.fullmatch(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?", text)
    return text, _strip_quotes(text.split(".")[-1]) if simple else ""


def _group_keys(sql: str) -> list[str]:
    clause = _group_clause(sql)
    return [_normalize_sql(item) for item in _split_top_level_csv(clause)] if clause else []


def _has_select_distinct(sql: str) -> bool:
    return bool(re.search(r"\bselect\s+distinct\b", _strip_comments(sql), flags=re.I))


def _joins(sql: str) -> list[_JoinItem]:
    text = _strip_comments(sql)
    pattern = re.compile(
        r"\b(?:(left|right|full|inner|cross)\s+(?:outer\s+)?)?join\s+"
        r"([a-zA-Z_][\w\.]*|\{\{.*?\}\})"
        r"(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?\s+\bon\b\s+"
        r"(.*?)(?=\b(?:left|right|full|inner|cross)?\s*(?:outer\s+)?join\b|"
        r"\bwhere\b|\bgroup\s+by\b|\bhaving\b|\border\s+by\b|\blimit\b|$)",
        flags=re.I | re.S,
    )
    out: list[_JoinItem] = []
    for match in pattern.finditer(text):
        join_type = _normalize_sql(match.group(1) or "inner")
        target = _normalize_identifier(match.group(2))
        predicate = _normalize_sql(match.group(4))
        raw = _normalize_raw(match.group(0))
        out.append(
            _JoinItem(
                join_type=join_type,
                target=target,
                predicate=predicate,
                raw=raw,
                columns=_changed_columns(match.group(4)),
            )
        )
    return out


def _metric_items(sql: str) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for item in _select_items(sql):
        match = AGG_RE.search(item.raw)
        if not match:
            continue
        argument = _normalize_sql(match.group(2))
        alias = item.alias or item.expr
        metrics[alias] = {
            "function": match.group(1).lower(),
            "argument": argument,
            "distinct": argument.startswith("distinct "),
            "raw": item.raw,
        }
    return metrics


def _case_summaries(sql: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for block in _case_blocks(_strip_comments(sql)):
        body = _case_body(block)
        branches = _top_level_case_branches(body)
        summaries.append(
            {
                "raw": _normalize_raw(block),
                "branches": tuple(branches),
                "else": _top_level_case_else(body),
            }
        )
    return summaries


def _case_blocks(sql: str) -> list[str]:
    stack: list[int] = []
    blocks: list[tuple[int, str]] = []
    for match in CASE_TOKEN_RE.finditer(sql):
        token = match.group(1).lower()
        if token == "case":
            stack.append(match.start())
        elif token == "end" and stack:
            start = stack.pop()
            blocks.append((start, sql[start : match.end()]))
    return [block for _, block in sorted(blocks, key=lambda item: item[0])]


def _case_body(block: str) -> str:
    text = block.strip()
    if text.lower().startswith("case"):
        text = text[4:]
    if text.lower().endswith("end"):
        text = text[:-3]
    return text.strip()


def _top_level_case_else(body: str) -> str:
    depth = 0
    else_start: int | None = None
    for match in CASE_TOKEN_RE.finditer(body):
        token = match.group(1).lower()
        if token == "case":
            depth += 1
        elif token == "end":
            depth = max(depth - 1, 0)
        elif token == "else" and depth == 0:
            else_start = match.end()
    return _normalize_literal(body[else_start:]) if else_start is not None else ""


def _top_level_case_branches(body: str) -> list[str]:
    branches: list[str] = []
    depth = 0
    branch_start: int | None = None
    for match in CASE_TOKEN_RE.finditer(body):
        token = match.group(1).lower()
        if token == "case":
            depth += 1
        elif token == "end":
            depth = max(depth - 1, 0)
        elif token == "when" and depth == 0:
            if branch_start is not None:
                branch = _normalize_sql(body[branch_start : match.start()])
                if branch:
                    branches.append(branch)
            branch_start = match.start()
        elif token == "else" and depth == 0:
            if branch_start is not None:
                branch = _normalize_sql(body[branch_start : match.start()])
                if branch:
                    branches.append(branch)
            branch_start = None
    if branch_start is not None:
        branch = _normalize_sql(body[branch_start:])
        if branch:
            branches.append(branch)
    return branches


def _in_lists(sql: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for match in IN_LIST_RE.finditer(_strip_comments(sql)):
        column = _normalize_identifier(match.group(1))
        values = {_normalize_literal(item) for item in _split_top_level_csv(match.group(2))}
        out[column] = values
    return out


def _coalesce_calls(sql: str) -> set[str]:
    return {_normalize_raw(match.group(0)) for match in COALESCE_RE.finditer(_strip_comments(sql))}


def _null_filters(sql: str) -> set[str]:
    where = _where_clause(sql)
    if not where:
        return set()
    return {
        _normalize_raw(match.group(0))
        for match in re.finditer(r"\b[a-zA-Z_][\w\.]*\s+is\s+(?:not\s+)?null\b", where, re.I)
    }


def _date_trunc_calls(sql: str) -> dict[str, str]:
    calls: dict[str, str] = {}
    for match in DATE_TRUNC_RE.finditer(_strip_comments(sql)):
        grain = _normalize_literal(match.group(1))
        column = _normalize_sql(match.group(2))
        calls[column] = grain
    return calls


def _date_columns(sql: str) -> set[str]:
    return {_normalize_identifier(match.group(2)) for match in DATE_CALL_RE.finditer(_strip_comments(sql))}


def _timezone_excerpt(sql: str) -> str:
    text = _strip_comments(sql)
    match = TIMEZONE_RE.search(text)
    if not match:
        return ""
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 140)
    return _normalize_raw(text[start:end])


def _window_size(sql: str) -> tuple[str, str] | None:
    match = INTERVAL_RE.search(_strip_comments(sql))
    if not match:
        return None
    return match.group(1), match.group(2).lower()


def _bounds(sql: str) -> set[str]:
    where = _where_clause(sql) or sql
    return {_normalize_raw(match.group(0)) for match in BOUND_RE.finditer(where)}


def _date_window_changed(before_where: str, after_where: str) -> bool:
    return bool(
        before_where
        and after_where
        and before_where != after_where
        and (
            "date" in before_where
            or "date" in after_where
            or "interval" in before_where
            or "interval" in after_where
            or "_at" in before_where
            or "_at" in after_where
            or "_ts" in before_where
            or "_ts" in after_where
        )
    )


def _status_population_changed(before_where: str, after_where: str) -> bool:
    text = before_where + " " + after_where
    return bool(
        before_where
        and after_where
        and before_where != after_where
        and any(term in text for term in ("status", "state", "type", "category", "kind", "code"))
    )


def _predicate_weakened(before_predicate: str, after_predicate: str) -> bool:
    before = _normalize_sql(before_predicate)
    after = _normalize_sql(after_predicate)
    return bool((" or " in after and " or " not in before) or "!=" in after or " not in " in after)


def _cast_changed(before_expr: str, after_expr: str) -> bool:
    return bool("cast(" in before_expr or "cast(" in after_expr or "::" in before_expr or "::" in after_expr)


def _has_arithmetic(expr: str) -> bool:
    return bool(re.search(r"[\w\)]\s*[-+*/]\s*[\w\(]", expr))


def _changed_columns(text: str) -> tuple[str, ...]:
    columns: list[str] = []
    text_without_literals = re.sub(r"'[^']*'|\"[^\"]*\"", " ", str(text or ""))
    for raw in IDENTIFIER_RE.findall(text_without_literals):
        token = _normalize_identifier(raw)
        short = token.split(".")[-1]
        if short in SQL_KEYWORDS or short.isdigit():
            continue
        if short not in columns:
            columns.append(short)
    return tuple(columns)


def _normalize_identifier(value: str) -> str:
    return _strip_quotes(_normalize_sql(value))


def _normalize_literal(value: str) -> str:
    return _strip_quotes(_normalize_sql(value).strip(","))


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in {"'", '"', "`"} and text[-1] == text[0]:
        return text[1:-1]
    return text


def _normalize_raw(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _unique_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, round(float(value), 4)))


def _dedupe_events(events: list[SemanticDiffEvent]) -> list[SemanticDiffEvent]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[SemanticDiffEvent] = []
    for event in events:
        key = (
            event.event_type,
            event.family_hint,
            event.clause,
            _normalize_sql(event.raw_excerpt),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out
