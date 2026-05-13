from __future__ import annotations

import ast
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .compiler_lineage import SQLCompilerLineage


@dataclass(slots=True)
class PythonLineageColumn:
    output_name: str
    exact_sources: set[str] = field(default_factory=set)
    inferred_sources: set[str] = field(default_factory=set)
    provenance: str = "inferred"

    def exact_pairs(self) -> list[str]:
        return [f"{self.output_name}<-{src}" for src in sorted(self.exact_sources)]


@dataclass(slots=True)
class DataFrameState:
    name: str
    source_tables: set[str] = field(default_factory=set)
    columns: dict[str, PythonLineageColumn] = field(default_factory=dict)
    filters: set[str] = field(default_factory=set)
    joins: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PythonLineageResult:
    dataframes: dict[str, DataFrameState] = field(default_factory=dict)
    columns: dict[str, PythonLineageColumn] = field(default_factory=dict)
    filters: set[str] = field(default_factory=set)
    joins: list[str] = field(default_factory=list)

    @property
    def exact_pairs(self) -> list[str]:
        pairs: list[str] = []
        for col in self.columns.values():
            pairs.extend(col.exact_pairs())
        return sorted(set(pairs))

    @property
    def provenance_classes(self) -> dict[str, str]:
        return {name: col.provenance for name, col in self.columns.items()}


class PythonCompilerLineage(ast.NodeVisitor):
    """Impacted-zone compiler-style lineage for dataframe-heavy Python.

    Deliberately scoped to the operations that dominate analytics code:
    read_sql/read_gbq, merge/join, rename, assign, query, groupby/agg.
    """

    def __init__(self) -> None:
        self.alias_to_df: dict[str, DataFrameState] = {}
        self.current_result = PythonLineageResult()

    @classmethod
    @lru_cache(maxsize=512)
    def compile(cls, source_text: str) -> PythonLineageResult:
        inst = cls()
        tree = ast.parse(source_text)
        inst.visit(tree)
        if inst.alias_to_df:
            last = list(inst.alias_to_df.values())[-1]
            inst.current_result.columns = dict(last.columns)
            inst.current_result.filters.update(last.filters)
            inst.current_result.joins.extend(last.joins)
        return inst.current_result

    def visit_Assign(self, node: ast.Assign) -> Any:
        state = self._state_from_expr(node.value)
        if state is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    cloned = self._clone_state(state, target.id)
                    self.alias_to_df[target.id] = cloned
                    self.current_result.dataframes[target.id] = cloned
        self.generic_visit(node)
        return node

    def _state_from_expr(self, node: ast.AST) -> DataFrameState | None:
        if isinstance(node, ast.Name) and node.id in self.alias_to_df:
            return self.alias_to_df[node.id]
        if isinstance(node, ast.Call):
            func_name = self._call_name(node.func)
            if func_name in {"read_sql", "read_gbq"} and node.args:
                sql = self._literal(node.args[0])
                if isinstance(sql, str):
                    compiled = SQLCompilerLineage.compile(sql)
                    state = DataFrameState(name="query")
                    for table in compiled.tables:
                        state.source_tables.add(table)
                    for output_name, col in compiled.columns.items():
                        state.columns[output_name] = PythonLineageColumn(
                            output_name=output_name,
                            exact_sources=set(col.exact_sources),
                            inferred_sources=set(col.inferred_sources),
                            provenance=col.provenance,
                        )
                    state.filters.update(compiled.filters)
                    return state
            if func_name in {"rename", "assign", "query", "merge", "join", "groupby", "agg"}:
                return self._apply_dataframe_op(node, func_name)
        return None

    def _apply_dataframe_op(self, node: ast.Call, func_name: str) -> DataFrameState | None:
        base = self._base_state(node)
        if base is None:
            return None
        state = self._clone_state(base, base.name)
        if func_name == "rename":
            mapping = next((kw.value for kw in node.keywords if kw.arg == "columns"), None)
            if isinstance(mapping, ast.Dict):
                for key, value in zip(mapping.keys, mapping.values):
                    old = self._literal(key)
                    new = self._literal(value)
                    if isinstance(old, str) and isinstance(new, str):
                        old_low, new_low = old.lower(), new.lower()
                        prev = state.columns.get(
                            old_low,
                            PythonLineageColumn(output_name=old_low, inferred_sources={old_low}),
                        )
                        state.columns[new_low] = PythonLineageColumn(
                            output_name=new_low,
                            exact_sources=set(prev.exact_sources or {old_low}),
                            inferred_sources=set(prev.inferred_sources),
                            provenance="exact" if prev.exact_sources else "exact+inferred",
                        )
            return state
        if func_name == "assign":
            for kw in node.keywords:
                if not kw.arg:
                    continue
                new_col = kw.arg.lower()
                refs = self._extract_column_refs(kw.value)
                exact: set[str] = set()
                inferred: set[str] = set()
                for ref in refs:
                    prev = state.columns.get(ref)
                    if prev:
                        exact.update(prev.exact_sources or {ref})
                        inferred.update(prev.inferred_sources)
                    else:
                        inferred.add(ref)
                provenance = (
                    "exact"
                    if exact and not inferred
                    else "exact+inferred"
                    if exact
                    else "inferred"
                    if inferred
                    else "constant"
                )
                state.columns[new_col] = PythonLineageColumn(
                    new_col, exact_sources=exact, inferred_sources=inferred, provenance=provenance
                )
            return state
        if func_name == "query":
            query_text = (
                self._literal(node.args[0])
                if node.args
                else next(
                    (self._literal(kw.value) for kw in node.keywords if kw.arg == "expr"), None
                )
            )
            if isinstance(query_text, str):
                state.filters.update(self._extract_filter_columns(query_text))
            return state
        if func_name in {"merge", "join"}:
            other = None
            if node.args:
                other = self._lookup_df(node.args[0])
            if other is None:
                other = next(
                    (
                        self._lookup_df(kw.value)
                        for kw in node.keywords
                        if kw.arg in {"right", "other"}
                    ),
                    None,
                )
            if other is not None:
                for name, col in other.columns.items():
                    if name not in state.columns:
                        state.columns[name] = PythonLineageColumn(
                            name,
                            exact_sources=set(col.exact_sources),
                            inferred_sources=set(col.inferred_sources),
                            provenance=col.provenance,
                        )
                state.source_tables.update(other.source_tables)
                join_cols: list[str] = []
                for kw in node.keywords:
                    if kw.arg in {"on", "left_on", "right_on"}:
                        join_cols.extend(self._literal_list(kw.value))
                for col in join_cols:
                    state.filters.add(col)
                state.joins.append("join:" + "->".join(sorted({base.name, other.name})))
            return state
        if func_name == "groupby":
            cols: list[str] = []
            if node.args:
                cols.extend(self._literal_list(node.args[0]))
            for kw in node.keywords:
                if kw.arg == "by":
                    cols.extend(self._literal_list(kw.value))
            state.filters.update(c.lower() for c in cols)
            return state
        if func_name == "agg":
            if node.args and isinstance(node.args[0], (ast.Dict,)):
                for key, value in zip(node.args[0].keys, node.args[0].values):
                    out = self._literal(key)
                    if not isinstance(out, str):
                        continue
                    out_low = out.lower()
                    prev = state.columns.get(
                        out_low, PythonLineageColumn(out_low, inferred_sources={out_low})
                    )
                    state.columns[out_low] = PythonLineageColumn(
                        out_low,
                        exact_sources=set(prev.exact_sources or {out_low}),
                        inferred_sources=set(prev.inferred_sources),
                        provenance="exact" if prev.exact_sources else "exact+inferred",
                    )
            return state
        return state

    def _base_state(self, node: ast.Call) -> DataFrameState | None:
        if isinstance(node.func, ast.Attribute):
            return self._lookup_df(node.func.value)
        return None

    def _lookup_df(self, node: ast.AST) -> DataFrameState | None:
        if isinstance(node, ast.Name):
            return self.alias_to_df.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return self.alias_to_df.get(node.value.id)
        if isinstance(node, ast.Call):
            return self._state_from_expr(node)
        return None

    @staticmethod
    def _clone_state(state: DataFrameState, name: str) -> DataFrameState:
        return DataFrameState(
            name=name,
            source_tables=set(state.source_tables),
            columns={
                k: PythonLineageColumn(
                    v.output_name,
                    exact_sources=set(v.exact_sources),
                    inferred_sources=set(v.inferred_sources),
                    provenance=v.provenance,
                )
                for k, v in state.columns.items()
            },
            filters=set(state.filters),
            joins=list(state.joins),
        )

    def _extract_column_refs(self, node: ast.AST) -> set[str]:
        refs: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript):
                refs.update(self._literal_list(child.slice))
            elif (
                isinstance(child, ast.Call) and self._call_name(child.func) == "col" and child.args
            ):
                lit = self._literal(child.args[0])
                if isinstance(lit, str):
                    refs.add(lit.lower())
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value.isidentifier():
                    refs.add(child.value.lower())
        return refs

    @staticmethod
    def _extract_filter_columns(expr: str) -> set[str]:
        import re

        cols = set()
        for token in re.findall(r"([A-Za-z_][\w$]*)\s*(?:==|=|!=|>=|<=|>|<|\bin\b|\bisin\b)", expr):
            cols.add(token.lower())
        return cols

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return node.id
        return ""

    def _literal(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    parts.append(str(self._literal(value.value) or "{}"))
            return "".join(parts)
        if isinstance(node, ast.List):
            return [self._literal(elt) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return [self._literal(elt) for elt in node.elts]
        return None

    def _literal_list(self, node: ast.AST) -> list[str]:
        lit = self._literal(node)
        if isinstance(lit, str):
            return [lit.lower()]
        if isinstance(lit, list):
            return [str(item).lower() for item in lit if isinstance(item, str)]
        return []
