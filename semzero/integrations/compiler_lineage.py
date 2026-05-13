from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][\w$]*")
_SQL_KEYWORDS = {
    "select",
    "from",
    "join",
    "where",
    "group",
    "order",
    "by",
    "on",
    "with",
    "and",
    "or",
    "as",
    "case",
    "when",
    "then",
    "else",
    "end",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "distinct",
    "cast",
    "coalesce",
    "over",
    "partition",
    "having",
    "limit",
    "union",
    "all",
    "left",
    "right",
    "inner",
    "outer",
    "cross",
    "qualify",
    "merge",
    "into",
    "using",
    "lateral",
    "flatten",
    "unnest",
    "explode",
}
_DBT_CONFIG_PATTERN = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.I | re.S)
_DBT_REF_PATTERN = re.compile(r"\{\{\s*ref\(['\"]([A-Za-z_][\w\.]*)['\"]\)\s*\}\}", re.I)
_DBT_SOURCE_PATTERN = re.compile(
    r"\{\{\s*source\(['\"]([A-Za-z_][\w]*)['\"]\s*,\s*['\"]([A-Za-z_][\w]*)['\"]\)\s*\}\}", re.I
)
_DBT_VARIABLE_PATTERN = re.compile(
    r"\{\{\s*(?:var|env_var)\(['\"]([A-Za-z_][\w\.]*)['\"].*?\)\s*\}\}", re.I | re.S
)
_DBT_THIS_PATTERN = re.compile(r"\{\{\s*this\s*\}\}", re.I)
_DBT_MACRO_DEF_PATTERN = re.compile(
    r"\{%\s*macro\s+([A-Za-z_][\w]*)\s*\((.*?)\)\s*%\}(.*?)\{%\s*endmacro\s*%\}", re.I | re.S
)
_DBT_MACRO_CALL_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][\w]*)\s*\((.*?)\)\s*\}\}", re.I | re.S)
_BUILTIN_DBT_MACROS = {"ref", "source", "config", "var", "env_var", "this", "is_incremental"}


@dataclass(slots=True)
class LineageColumn:
    output_name: str
    expression: str
    exact_sources: set[str] = field(default_factory=set)
    inferred_sources: set[str] = field(default_factory=set)
    source_tables: set[str] = field(default_factory=set)
    provenance: str = "heuristic"

    def exact_pairs(self) -> list[str]:
        return [f"{self.output_name}<-{src}" for src in sorted(self.exact_sources)]

    def inferred_pairs(self) -> list[str]:
        return [
            f"{self.output_name}~{src}"
            for src in sorted(self.inferred_sources - self.exact_sources)
        ]


@dataclass(slots=True)
class QueryLineage:
    columns: dict[str, LineageColumn] = field(default_factory=dict)
    ctes: dict[str, "QueryLineage"] = field(default_factory=dict)
    filters: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)
    select_star: bool = False
    macro_calls: set[str] = field(default_factory=set)
    macro_defs: set[str] = field(default_factory=set)

    @property
    def exact_pairs(self) -> list[str]:
        pairs: list[str] = []
        for col in self.columns.values():
            pairs.extend(col.exact_pairs())
        return sorted(set(pairs))

    @property
    def inferred_pairs(self) -> list[str]:
        pairs: list[str] = []
        for col in self.columns.values():
            pairs.extend(col.inferred_pairs())
        return sorted(set(pairs))

    @property
    def provenance_classes(self) -> dict[str, str]:
        return {name: col.provenance for name, col in self.columns.items()}


class SQLCompilerLineage:
    """Fast compiler-style lineage for SQL/dbt surfaces.

    This is not a universal SQL compiler, but it builds exact column derivation
    graphs for the SELECT/CTE patterns that dominate analytics + dbt workloads.
    """

    @classmethod
    @lru_cache(maxsize=1024)
    def compile(cls, sql_text: str) -> QueryLineage:
        return cls._compile(sql_text, {})

    @classmethod
    def _compile(cls, sql_text: str, outer_ctes: dict[str, QueryLineage]) -> QueryLineage:
        sql = cls._strip_comments(sql_text)
        sql = cls._normalise_dbt_sql(sql)
        sql = cls._inline_macro_defs(sql)
        ctes, remainder = cls._extract_ctes(sql)
        lineage = QueryLineage()
        available_ctes: dict[str, QueryLineage] = dict(outer_ctes)
        for cte_name, cte_sql in ctes.items():
            compiled_cte = cls._compile(cte_sql, available_ctes)
            lineage.ctes[cte_name] = compiled_cte
            available_ctes[cte_name] = compiled_cte
            lineage.filters.update(compiled_cte.filters)
        alias_map = cls._extract_from_join_aliases(remainder)
        lineage.tables.update({t for t in alias_map.values() if t})
        lineage.filters.update(cls._extract_filter_columns(remainder))
        select_clause = cls._extract_top_select_clause(remainder)
        if not select_clause:
            return lineage
        select_items = cls._split_sql_list(select_clause)
        lineage.select_star = any(
            item.strip() == "*" or item.strip().endswith(".*") for item in select_items
        )
        for idx, expr in enumerate(select_items):
            output_name = cls._extract_select_alias(expr) or f"_expr_{idx}"
            refs = cls._extract_identifiers_from_expr(expr, alias_map, set(available_ctes))
            col = LineageColumn(output_name=output_name.lower(), expression=expr.strip())
            if expr.strip() == "*":
                col.provenance = "wildcard"
                for table in alias_map.values():
                    if table:
                        col.inferred_sources.add(f"{table}.*")
                        col.source_tables.add(table)
            elif expr.strip().endswith(".*"):
                table_alias = expr.strip()[:-2].strip().lower()
                table = alias_map.get(table_alias, table_alias)
                col.provenance = "wildcard"
                col.inferred_sources.add(f"{table}.*")
                col.source_tables.add(table)
            else:
                for table_name, raw_col in refs:
                    source_table = alias_map.get(table_name, table_name) if table_name else ""
                    source_col = raw_col.lower()
                    if source_table and source_table in available_ctes:
                        resolved = available_ctes[source_table].columns.get(source_col)
                        if resolved:
                            col.exact_sources.update(
                                resolved.exact_sources or resolved.inferred_sources
                            )
                            col.inferred_sources.update(resolved.inferred_sources)
                            col.source_tables.update(resolved.source_tables)
                        else:
                            col.inferred_sources.add(f"{source_table}.{source_col}")
                            col.source_tables.add(source_table)
                    elif source_table:
                        col.exact_sources.add(f"{source_table}.{source_col}")
                        col.source_tables.add(source_table)
                    else:
                        col.inferred_sources.add(source_col)
                if col.exact_sources and not col.inferred_sources:
                    col.provenance = "exact"
                elif col.exact_sources and col.inferred_sources:
                    col.provenance = "exact+inferred"
                elif col.inferred_sources:
                    col.provenance = "inferred"
                else:
                    col.provenance = "constant"
            lineage.columns[col.output_name] = col
        lineage.macro_defs.update(cls._extract_macro_defs(sql_text))
        lineage.macro_calls.update(cls._extract_macro_calls(sql_text))
        return lineage

    @staticmethod
    def _strip_comments(sql: str) -> str:
        sql = re.sub(r"--.*?$", "", sql, flags=re.M)
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
        return sql

    @classmethod
    def _normalise_dbt_sql(cls, text: str) -> str:
        text = _DBT_CONFIG_PATTERN.sub(" ", text)
        text = _DBT_REF_PATTERN.sub(lambda m: m.group(1), text)
        text = _DBT_SOURCE_PATTERN.sub(lambda m: f"{m.group(1)}.{m.group(2)}", text)
        text = _DBT_VARIABLE_PATTERN.sub(lambda m: m.group(1), text)
        text = _DBT_THIS_PATTERN.sub(" this ", text)
        text = re.sub(
            r"\{%\s*(if|elif|else|endif|for|endfor|set)\b.*?%\}", " ", text, flags=re.I | re.S
        )
        return text

    @classmethod
    def _inline_macro_defs(cls, text: str) -> str:
        rendered = text
        for macro_name, params, body in _DBT_MACRO_DEF_PATTERN.findall(text):
            full = f"{{% macro {macro_name}({params}) %}}{body}{{% endmacro %}}"
            rendered = rendered.replace(full, body)
        for macro_name, args in _DBT_MACRO_CALL_PATTERN.findall(rendered):
            if macro_name.lower() in _BUILTIN_DBT_MACROS:
                continue
            rendered = rendered.replace(f"{{{{ {macro_name}({args}) }}}}", args)
        return rendered

    @staticmethod
    def _extract_macro_defs(text: str) -> set[str]:
        return {
            f"macro:{name.lower()}" for name, _params, _body in _DBT_MACRO_DEF_PATTERN.findall(text)
        }

    @staticmethod
    def _extract_macro_calls(text: str) -> set[str]:
        calls = set()
        for name, _args in _DBT_MACRO_CALL_PATTERN.findall(text):
            low = name.lower()
            if low in _BUILTIN_DBT_MACROS:
                continue
            calls.add(f"macro:{low}")
        return calls

    @classmethod
    def _extract_ctes(cls, sql: str) -> tuple[dict[str, str], str]:
        stripped = sql.lstrip()
        if not stripped[:4].upper() == "WITH":
            return {}, sql
        idx = stripped.upper().find("WITH") + 4
        ctes: dict[str, str] = {}
        i = idx
        while i < len(stripped):
            while i < len(stripped) and stripped[i] in " \n\t,":
                i += 1
            name_match = re.match(r"([A-Za-z_][\w$]*)\s+AS\s*\(", stripped[i:], re.I)
            if not name_match:
                break
            cte_name = name_match.group(1).lower()
            i += name_match.end() - 1
            depth = 1
            start = i + 1
            i += 1
            while i < len(stripped) and depth > 0:
                if stripped[i] == "(":
                    depth += 1
                elif stripped[i] == ")":
                    depth -= 1
                i += 1
            ctes[cte_name] = stripped[start : i - 1].strip()
            while i < len(stripped) and stripped[i] in " \n\t":
                i += 1
            if i >= len(stripped) or stripped[i] != ",":
                break
            i += 1
        remainder = stripped[i:].strip() if i < len(stripped) else ""
        if remainder.upper().startswith("SELECT"):
            return ctes, remainder
        # fallback: parse entire string as select when we failed to isolate cleanly
        return ctes, stripped

    @classmethod
    def _extract_from_join_aliases(cls, text: str) -> dict[str, str]:
        alias_map: dict[str, str] = {}
        pattern = re.compile(
            r"\b(?:FROM|JOIN)\s+(?!\()([`\"\[]?[A-Za-z_][\w\.$]*[`\"\]]?|this)(?:\s+(?:AS\s+)?([A-Za-z_][\w$]*))?",
            re.I,
        )
        for match in pattern.finditer(text):
            table_raw = cls._clean_identifier(match.group(1)).lower()
            alias = (match.group(2) or "").strip().lower()
            if alias in _SQL_KEYWORDS:
                alias = ""
            alias_map[table_raw] = table_raw
            if alias:
                alias_map[alias] = table_raw
        return alias_map

    @classmethod
    def _extract_identifiers_from_expr(
        cls, expr: str, alias_map: dict[str, str], cte_names: set[str]
    ) -> set[tuple[str, str]]:
        refs: set[tuple[str, str]] = set()
        for table_raw, col_raw in re.findall(r"([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)", expr):
            refs.add((table_raw.lower(), col_raw.lower()))
        cleaned = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
        cleaned = re.sub(r"\bAS\s+[A-Za-z_][\w$]*\s*$", " ", cleaned, flags=re.I)
        inferred_table = ""
        unique_tables = {tbl for tbl in alias_map.values() if tbl}
        if len(unique_tables) == 1:
            inferred_table = next(iter(unique_tables))
        if not refs:
            reserved = set(alias_map) | _SQL_KEYWORDS | cte_names
            for token in _IDENTIFIER_RE.findall(cleaned):
                low = token.lower()
                if low in reserved or low.isdigit():
                    continue
                refs.add((inferred_table, low))
        return refs

    @classmethod
    def _extract_top_select_clause(cls, text: str) -> str:
        upper = text.upper()
        sel_idx = upper.find("SELECT")
        if sel_idx < 0:
            return ""
        depth = 0
        i = sel_idx + len("SELECT")
        start = i
        while i < len(text):
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif depth == 0 and upper[i : i + 4] == "FROM":
                return text[start:i].strip()
            i += 1
        return text[start:].strip()

    @classmethod
    def _split_sql_list(cls, text: str) -> list[str]:
        items: list[str] = []
        current: list[str] = []
        depth = 0
        quote = ""
        for ch in text:
            if quote:
                current.append(ch)
                if ch == quote:
                    quote = ""
                continue
            if ch in {"'", '"'}:
                quote = ch
                current.append(ch)
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                items.append("".join(current).strip())
                current = []
                continue
            current.append(ch)
        tail = "".join(current).strip()
        if tail:
            items.append(tail)
        return items

    @classmethod
    def _extract_select_alias(cls, expr: str) -> str:
        match = re.search(r"\bAS\s+([A-Za-z_][\w$]*)\s*$", expr, re.I)
        if match:
            return match.group(1)
        pieces = expr.strip().split()
        if len(pieces) >= 2 and pieces[-1].lower() not in _SQL_KEYWORDS:
            return pieces[-1]
        bare = expr.strip()
        col_match = re.fullmatch(r"(?:[A-Za-z_][\w$]*\.)?([A-Za-z_][\w$]*)", bare)
        if col_match:
            return col_match.group(1)
        return ""

    @classmethod
    def _extract_filter_columns(cls, text: str) -> set[str]:
        cols: set[str] = set()
        for clause in ("WHERE", "HAVING", "QUALIFY", "ON"):
            body = cls._extract_clause_body(text, clause)
            for table, col in re.findall(r"([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)", body):
                cols.add(col.lower())
            for token in _IDENTIFIER_RE.findall(body):
                low = token.lower()
                if low in _SQL_KEYWORDS:
                    continue
                cols.add(low)
        return cols

    @classmethod
    def _extract_clause_body(cls, text: str, clause: str) -> str:
        pattern = re.compile(
            rf"\b{clause}\b\s+(.*?)(?=\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|\bQUALIFY\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)",
            re.I | re.S,
        )
        match = pattern.search(text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _clean_identifier(value: str) -> str:
        return value.strip().strip('`"[]')
