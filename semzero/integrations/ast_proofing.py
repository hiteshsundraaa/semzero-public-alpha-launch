from __future__ import annotations

import ast
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_PARSE_CACHE: dict[tuple[str, int, int], "SourceAssetReference"] = {}

import networkx as nx
from jinja2 import Environment, meta

from .compiler_lineage import SQLCompilerLineage
from .python_compiler_lineage import PythonCompilerLineage

log = logging.getLogger(__name__)

_APP_FILE_SUFFIXES = {".sql", ".py", ".ts", ".tsx", ".js", ".jsx", ".prisma", ".yml", ".yaml"}
_SCAN_GLOB_PATTERNS = (
    "*.sql",
    "*.py",
    "*.ts",
    "*.tsx",
    "*.js",
    "*.jsx",
    "*.prisma",
    "*.yml",
    "*.yaml",
)

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
_PANDAS_METHODS = {
    "merge",
    "join",
    "groupby",
    "agg",
    "astype",
    "rename",
    "drop",
    "fillna",
    "dropna",
    "sort_values",
    "pivot",
    "pivot_table",
    "query",
    "assign",
    "filter",
    "read_sql",
    "read_gbq",
    "to_sql",
    "melt",
    "explode",
}
_SQL_OP_PATTERNS = {
    "join": r"\bJOIN\b",
    "aggregate": r"\b(COUNT|SUM|AVG|MIN|MAX|APPROX_COUNT_DISTINCT|COUNT_IF)\s*\(",
    "window": r"\bOVER\s*\(",
    "cast": r"\b(CAST|TRY_CAST|SAFE_CAST)::?\s*\(|::",
    "filter": r"\bWHERE\b|\bQUALIFY\b|\bHAVING\b",
    "group_by": r"\bGROUP\s+BY\b",
    "order_by": r"\bORDER\s+BY\b",
    "cte": r"\bWITH\b",
    "incremental": r"\bis_incremental\s*\(|\bincremental\b|\bmerge_strategy\b|\bwatermark\b",
    "merge_into": r"\bMERGE\s+INTO\b",
    "flatten": r"\bLATERAL\s+FLATTEN\b|\bEXPLODE\s*\(|\bUNNEST\s*\(",
    "qualify": r"\bQUALIFY\b",
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
_JINJA_BLOCK_PATTERN = re.compile(
    r"\{%\s*(if|elif|else|endif|for|endfor|macro|endmacro|set)\b(.*?)%\}", re.I | re.S
)
_DBT_MACRO_DEF_PATTERN = re.compile(
    r"\{%\s*macro\s+([A-Za-z_][\w]*)\s*\((.*?)\)\s*%\}(.*?)\{%\s*endmacro\s*%\}", re.I | re.S
)
_DBT_MACRO_CALL_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][\w]*)\s*\((.*?)\)\s*\}\}", re.I | re.S)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][\w$]*")
_BUILTIN_DBT_MACROS = {"ref", "source", "config", "var", "env_var", "this", "is_incremental"}


@dataclass
class SourceAssetReference:
    path: str
    language: str
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    select_star: bool = False
    string_sql_count: int = 0
    source_kind: str = "warehouse"
    semantic_roles: list[str] = field(default_factory=list)
    lineage_pairs: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)
    macro_defs: list[str] = field(default_factory=list)
    macro_calls: list[str] = field(default_factory=list)
    exact_lineage_pairs: list[str] = field(default_factory=list)
    lineage_provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "tables": self.tables,
            "columns": self.columns,
            "assets": self.assets,
            "operations": self.operations,
            "select_star": self.select_star,
            "string_sql_count": self.string_sql_count,
            "source_kind": self.source_kind,
            "semantic_roles": self.semantic_roles,
            "lineage_pairs": self.lineage_pairs,
            "filters": self.filters,
            "snippets": self.snippets,
            "macro_defs": self.macro_defs,
            "macro_calls": self.macro_calls,
            "exact_lineage_pairs": self.exact_lineage_pairs,
            "lineage_provenance": self.lineage_provenance,
        }

    @property
    def match_tokens(self) -> set[str]:
        tokens = getattr(self, "_match_tokens", None)
        if tokens is None:
            tokens = (
                set(self.assets)
                | set(self.tables)
                | set(self.columns)
                | set(self.filters)
                | set(self.lineage_pairs)
                | set(getattr(self, "exact_lineage_pairs", []) or [])
            )
            setattr(self, "_match_tokens", tokens)
        return tokens


@dataclass
class ProofFinding:
    node_id: str
    asset_path: str
    language: str
    direct_hits: list[str] = field(default_factory=list)
    downstream_hits: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    expected_failure_mode: str = ""
    suggested_fix: str = ""
    severity: str = "LOW"
    confidence: float = 0.0
    lineage_hits: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    evidence_snippets: list[str] = field(default_factory=list)
    exact_lineage_hits: list[str] = field(default_factory=list)
    lineage_provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "asset_path": self.asset_path,
            "language": self.language,
            "direct_hits": self.direct_hits,
            "downstream_hits": self.downstream_hits,
            "operations": self.operations,
            "expected_failure_mode": self.expected_failure_mode,
            "suggested_fix": self.suggested_fix,
            "severity": self.severity,
            "confidence": round(self.confidence, 3),
            "lineage_hits": self.lineage_hits,
            "filters": self.filters,
            "evidence_snippets": self.evidence_snippets,
            "exact_lineage_hits": self.exact_lineage_hits,
            "lineage_provenance": self.lineage_provenance,
        }


@dataclass
class ProofBundle:
    findings: list[ProofFinding] = field(default_factory=list)
    scanned_paths: list[str] = field(default_factory=list)
    scanned_files: int = 0
    direct_hit_count: int = 0
    downstream_hit_count: int = 0
    indexed_token_count: int = 0
    candidate_source_count: int = 0
    full_scan_fallbacks: int = 0
    provenance_node_count: int = 0
    provenance_edge_count: int = 0

    def for_node(self, node_id: str) -> list[ProofFinding]:
        matches = [f for f in self.findings if f.node_id == node_id]
        return sorted(matches, key=lambda f: (-f.confidence, f.asset_path))

    def summary(self) -> dict[str, Any]:
        return {
            "scanned_files": self.scanned_files,
            "scanned_paths": self.scanned_paths,
            "direct_hit_count": self.direct_hit_count,
            "downstream_hit_count": self.downstream_hit_count,
            "finding_count": len(self.findings),
            "high_confidence_findings": sum(1 for f in self.findings if f.confidence >= 0.75),
            "cross_modal_findings": sum(
                1
                for f in self.findings
                if f.language in {"typescript", "javascript", "prisma", "yaml"}
            ),
            "lineage_backed_findings": sum(1 for f in self.findings if f.lineage_hits),
            "exact_lineage_findings": sum(1 for f in self.findings if f.exact_lineage_hits),
            "filter_backed_findings": sum(1 for f in self.findings if f.filters),
            "macro_backed_findings": sum(
                1
                for f in self.findings
                if any(str(hit).startswith("macro:") for hit in f.downstream_hits)
            ),
            "indexed_token_count": self.indexed_token_count,
            "candidate_source_count": self.candidate_source_count,
            "full_scan_fallbacks": self.full_scan_fallbacks,
            "provenance_node_count": self.provenance_node_count,
            "provenance_edge_count": self.provenance_edge_count,
            "parse_cache_entries": len(_PARSE_CACHE),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "findings": [f.to_dict() for f in self.findings]}

    def to_markdown(self, limit: int = 6) -> str:
        if not self.findings:
            return "### 🧠 AST Proofing\n\n> No direct downstream source references were found in the scanned SQL/Python assets."
        lines = [
            "### 🧠 AST Proofing",
            "",
            "| Asset | Changed Node | Failure Mode | Confidence |",
            "|---|---|---|---|",
        ]
        for finding in sorted(self.findings, key=lambda f: (-f.confidence, f.asset_path))[:limit]:
            lines.append(
                f"| `{Path(finding.asset_path).name}` | `{finding.node_id}` | {finding.expected_failure_mode[:70]} | {finding.confidence:.0%} |"
            )
        return "\n".join(lines)


def _path_cache_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _parse_source_text(path: Path, text: str) -> SourceAssetReference:
    suffix = path.suffix.lower()
    norm = str(path.resolve())
    if suffix == ".sql":
        return SQLAssetParser.parse(text, norm)
    if suffix == ".py":
        return PythonAssetParser(norm).parse(text)
    if suffix in {".yml", ".yaml"}:
        return DbtYamlParser.parse(text, norm)
    return AppSchemaParser.parse(text, norm)


class SQLAssetParser:
    @staticmethod
    def strip_comments(sql: str) -> str:
        sql = re.sub(r"--.*?$", "", sql, flags=re.M)
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
        return sql

    @classmethod
    def parse(cls, text: str, path: str = "") -> SourceAssetReference:
        compact = cls.strip_comments(text)
        tables: set[str] = set()
        columns: set[str] = set()
        assets: set[str] = set()
        operations: set[str] = set()
        filters: set[str] = set()
        snippets: list[str] = []
        alias_map: dict[str, str] = {}
        lineage_pairs: set[str] = set()
        semantic_roles: set[str] = set()
        macro_defs: set[str] = set()
        macro_calls: set[str] = set()

        compact, jinja_meta = cls._normalise_jinja_sql(compact)
        operations.update(jinja_meta["operations"])
        snippets.extend(jinja_meta["snippets"])
        assets.update(jinja_meta["assets"])
        lineage_pairs.update(jinja_meta["lineage_pairs"])
        filters.update(jinja_meta["filters"])
        macro_defs.update(jinja_meta["macro_defs"])
        macro_calls.update(jinja_meta["macro_calls"])

        cls._consume_dbt_tokens(compact, tables, assets, operations)
        compact = cls._normalize_dbt_sql(compact)

        for opname, pattern in _SQL_OP_PATTERNS.items():
            if re.search(pattern, compact, re.I | re.S):
                operations.add(opname)

        cte_names = {
            name.lower()
            for name in re.findall(
                r"\bWITH\s+([A-Za-z_][\w]*)\s+AS\s*\(|,\s*([A-Za-z_][\w]*)\s+AS\s*\(", compact, re.I
            )
            for name in name
            if name
        }

        for table_token, alias in cls._iter_from_join_targets(compact):
            raw_table = cls._clean_identifier(table_token)
            if not raw_table:
                continue
            table_name = raw_table.split(".")[-1].lower()
            tables.add(table_name)
            assets.add(table_name)
            if alias and alias not in _SQL_KEYWORDS:
                alias_map[alias] = table_name

        for table_token, alias in cls._iter_merge_targets(compact):
            raw_table = cls._clean_identifier(table_token)
            if not raw_table:
                continue
            table_name = raw_table.split(".")[-1].lower()
            tables.add(table_name)
            assets.add(table_name)
            if alias and alias not in _SQL_KEYWORDS:
                alias_map[alias] = table_name

        for left_raw, col_raw in re.findall(r"([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)", compact):
            left = left_raw.lower()
            table_name = alias_map.get(left, left)
            col = col_raw.lower()
            cls._add_column_reference(columns, assets, semantic_roles, table_name, col, cte_names)
            lineage_pairs.add(f"{table_name}.{col}")

        selected = cls._extract_top_select_clause(compact)
        select_star = False
        if selected:
            select_star = bool(re.search(r"(^|,)\s*(?:[A-Za-z_][\w$]*\.)?\*\s*(,|$)", selected))
            for expr in cls._split_sql_list(selected):
                alias = cls._extract_select_alias(expr)
                source_refs = cls._extract_identifiers_from_expr(expr, alias_map, cte_names)
                for table_name, col in source_refs:
                    cls._add_column_reference(
                        columns, assets, semantic_roles, table_name, col, cte_names
                    )
                    if alias:
                        lineage_pairs.add(f"{alias.lower()}<-{table_name}.{col}")
                    else:
                        lineage_pairs.add(f"{table_name}.{col}")
                if alias and source_refs:
                    columns.add(alias.lower())
                    assets.add(alias.lower())
                    semantic_roles.update(cls._semantic_roles_for_token(alias.lower()))

        for clause in ("where", "having", "qualify"):
            body = cls._extract_clause_body(compact, clause)
            if body:
                for table_name, col in cls._extract_identifiers_from_expr(
                    body, alias_map, cte_names
                ):
                    cls._add_column_reference(
                        columns, assets, semantic_roles, table_name, col, cte_names
                    )
                    filters.add(f"{table_name}.{col}" if table_name else col)
                literal_filters = re.findall(
                    r"([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\s+(?:=|IN\s*\(|NOT\s+IN\s*\(|ILIKE|LIKE)",
                    body,
                    re.I,
                )
                filters.update(item.lower() for item in literal_filters)
                snippets.append(cls._trim_snippet(body))

        for body in re.findall(
            r"\bPARTITION\s+BY\s+(.*?)(?:\bORDER\s+BY\b|\)|$)", compact, re.I | re.S
        ):
            for table_name, col in cls._extract_identifiers_from_expr(body, alias_map, cte_names):
                cls._add_column_reference(
                    columns, assets, semantic_roles, table_name, col, cte_names
                )
                lineage_pairs.add(f"window:{table_name}.{col}" if table_name else f"window:{col}")

        for body in re.findall(
            r"\bGROUP\s+BY\s+(.*?)(?:\bHAVING\b|\bQUALIFY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
            compact,
            re.I | re.S,
        ):
            for table_name, col in cls._extract_identifiers_from_expr(body, alias_map, cte_names):
                cls._add_column_reference(
                    columns, assets, semantic_roles, table_name, col, cte_names
                )
                lineage_pairs.add(f"group:{table_name}.{col}" if table_name else f"group:{col}")

        compiled = SQLCompilerLineage.compile(text)
        exact_lineage_pairs = sorted(compiled.exact_pairs)
        lineage_pairs.update(exact_lineage_pairs)
        assets.update(compiled.tables)
        filters.update(compiled.filters)
        for line_col in compiled.columns.values():
            columns.add(line_col.output_name)
            assets.add(line_col.output_name)
            semantic_roles.update(cls._semantic_roles_for_token(line_col.output_name))
            assets.update(line_col.source_tables)
            for src in line_col.exact_sources | line_col.inferred_sources:
                assets.add(src)
                parts = str(src).split(".")
                if parts:
                    semantic_roles.update(cls._semantic_roles_for_token(parts[-1]))

        return SourceAssetReference(
            path=path,
            language="sql",
            tables=sorted(tables),
            columns=sorted(columns),
            assets=sorted(assets),
            operations=sorted(operations),
            select_star=select_star,
            string_sql_count=1,
            semantic_roles=sorted(semantic_roles),
            lineage_pairs=sorted(lineage_pairs),
            filters=sorted(filters),
            snippets=snippets[:6],
            macro_defs=sorted(macro_defs | compiled.macro_defs),
            macro_calls=sorted(macro_calls | compiled.macro_calls),
            exact_lineage_pairs=exact_lineage_pairs,
            lineage_provenance=compiled.provenance_classes,
        )

    @classmethod
    def _normalise_jinja_sql(cls, text: str) -> tuple[str, dict[str, set[str] | list[str]]]:
        operations: set[str] = set()
        assets: set[str] = set()
        lineage_pairs: set[str] = set()
        filters: set[str] = set()
        snippets: list[str] = []
        macro_defs: set[str] = set()
        macro_calls: set[str] = set()
        rendered = text

        if "{{" in text or "{%" in text:
            operations.add("jinja")

        if _DBT_THIS_PATTERN.search(text):
            operations.add("dbt_this")
            assets.add("this")

        if re.search(r"\{%\s*if\b", text, re.I):
            operations.add("jinja_branch")
        if re.search(r"\bis_incremental\s*\(", text, re.I):
            operations.update({"incremental", "incremental_branch"})

        for macro_name, _params, body in _DBT_MACRO_DEF_PATTERN.findall(text):
            macro_token = f"macro:{macro_name.lower()}"
            operations.update({"dbt_macro_definition", "dbt_macro"})
            macro_defs.add(macro_token)
            assets.add(macro_token)
            rendered = rendered.replace(
                f"{{% macro {macro_name}({_params}) %}}{body}{{% endmacro %}}", body
            )
            snippets.append(cls._trim_snippet(body))
            for token in _IDENTIFIER_RE.findall(body):
                low = token.lower()
                if low in _SQL_KEYWORDS or low in _BUILTIN_DBT_MACROS:
                    continue
                lineage_pairs.add(f"{macro_token}<-{low}")

        for macro_name, args in _DBT_MACRO_CALL_PATTERN.findall(text):
            low = macro_name.lower()
            if low in _BUILTIN_DBT_MACROS:
                continue
            macro_token = f"macro:{low}"
            operations.update({"dbt_macro_call", "dbt_macro"})
            macro_calls.add(macro_token)
            assets.add(macro_token)
            snippets.append(cls._trim_snippet(f"{macro_name}({args})"))
            for token in _IDENTIFIER_RE.findall(args):
                arg = token.lower()
                if arg in _SQL_KEYWORDS or arg in _BUILTIN_DBT_MACROS:
                    continue
                assets.add(arg)
                lineage_pairs.add(f"{macro_token}<-{arg}")
                semantic_roles = cls._semantic_roles_for_token(arg)
                if "domain" in semantic_roles:
                    filters.add(arg)

        rendered = re.sub(
            r"\{%\s*(if|elif|else|endif|for|endfor|set)\b.*?%\}", " ", rendered, flags=re.I | re.S
        )
        rendered = _DBT_THIS_PATTERN.sub(" this ", rendered)

        try:
            env = Environment()
            parsed = env.parse(text)
            undeclared = {name.lower() for name in meta.find_undeclared_variables(parsed)}
            for name in sorted(undeclared):
                if name in {"this", "target", "adapter", "execute"}:
                    continue
                operations.add("jinja_variable")
                assets.add(name)
        except Exception:
            pass

        return rendered, {
            "operations": operations,
            "assets": assets,
            "lineage_pairs": lineage_pairs,
            "filters": filters,
            "snippets": snippets[:6],
            "macro_defs": macro_defs,
            "macro_calls": macro_calls,
        }

    @classmethod
    def _consume_dbt_tokens(
        cls, text: str, tables: set[str], assets: set[str], operations: set[str]
    ) -> None:
        for ref_name in _DBT_REF_PATTERN.findall(text):
            token = ref_name.lower().split(".")[-1]
            tables.add(token)
            assets.add(token)
            operations.add("dbt_ref")
        for source_schema, source_table in _DBT_SOURCE_PATTERN.findall(text):
            token = source_table.lower()
            tables.add(token)
            assets.update({token, f"{source_schema.lower()}.{token}"})
            operations.add("dbt_source")
        if _DBT_CONFIG_PATTERN.search(text):
            operations.add("dbt_config")
        if _DBT_VARIABLE_PATTERN.search(text):
            operations.add("dbt_variable")

    @classmethod
    def _normalize_dbt_sql(cls, text: str) -> str:
        text = _DBT_CONFIG_PATTERN.sub(" ", text)
        text = _DBT_REF_PATTERN.sub(lambda m: m.group(1), text)
        text = _DBT_SOURCE_PATTERN.sub(lambda m: f"{m.group(1)}.{m.group(2)}", text)
        text = _DBT_VARIABLE_PATTERN.sub(lambda m: m.group(1), text)
        return text

    @classmethod
    def _iter_from_join_targets(cls, text: str):
        pattern = re.compile(
            r"\b(?:FROM|JOIN)\s+(?!\()([`\"\[]?[A-Za-z_][\w\.$]*[`\"\]]?)(?:\s+(?:AS\s+)?([A-Za-z_][\w$]*))?",
            re.I,
        )
        for match in pattern.finditer(text):
            yield match.group(1), (match.group(2) or "").strip().lower()

    @classmethod
    def _iter_merge_targets(cls, text: str):
        pattern = re.compile(
            r"\bMERGE\s+INTO\s+([`\"\[]?[A-Za-z_][\w\.$]*[`\"\]]?)(?:\s+(?:AS\s+)?([A-Za-z_][\w$]*))?.*?\bUSING\s+([`\"\[]?[A-Za-z_][\w\.$]*[`\"\]]?)(?:\s+(?:AS\s+)?([A-Za-z_][\w$]*))?",
            re.I | re.S,
        )
        for match in pattern.finditer(text):
            yield match.group(1), (match.group(2) or "").strip().lower()
            yield match.group(3), (match.group(4) or "").strip().lower()

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
        return ""

    @classmethod
    def _extract_clause_body(cls, text: str, clause: str) -> str:
        pattern = re.compile(
            rf"\b{clause}\b\s+(.*?)(?=\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|\bQUALIFY\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)",
            re.I | re.S,
        )
        match = pattern.search(text)
        return match.group(1).strip() if match else ""

    @classmethod
    def _split_sql_list(cls, text: str) -> list[str]:
        items: list[str] = []
        current: list[str] = []
        depth = 0
        quote: Optional[str] = None
        for ch in text:
            if quote:
                current.append(ch)
                if ch == quote:
                    quote = None
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
        if len(pieces) >= 2 and pieces[-1].lower() not in _SQL_KEYWORDS and ")" in expr:
            return pieces[-1]
        return ""

    @classmethod
    def _extract_identifiers_from_expr(
        cls, expr: str, alias_map: dict[str, str], cte_names: set[str]
    ) -> set[tuple[str, str]]:
        refs: set[tuple[str, str]] = set()
        for table_raw, col_raw in re.findall(r"([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)", expr):
            table_name = alias_map.get(table_raw.lower(), table_raw.lower())
            refs.add((table_name, col_raw.lower()))
        cleaned = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
        if not refs:
            reserved = set(alias_map) | _SQL_KEYWORDS | cte_names
            for token in _IDENTIFIER_RE.findall(cleaned):
                low = token.lower()
                if low in reserved or low.isdigit():
                    continue
                refs.add(("", low))
        return refs

    @staticmethod
    def _add_column_reference(
        columns: set[str],
        assets: set[str],
        semantic_roles: set[str],
        table_name: str,
        col: str,
        cte_names: set[str],
    ) -> None:
        columns.add(col)
        assets.add(col)
        semantic_roles.update(SQLAssetParser._semantic_roles_for_token(col))
        if table_name and table_name not in _SQL_KEYWORDS and table_name not in cte_names:
            assets.add(table_name)
            assets.add(f"{table_name}.{col}")

    @staticmethod
    def _semantic_roles_for_token(token: str) -> set[str]:
        roles: set[str] = set()
        low = token.lower()
        if low.endswith("_id") or low == "id":
            roles.add("identity")
        if any(tok in low for tok in ("status", "state", "type", "category")):
            roles.add("domain")
        if any(tok in low for tok in ("ts", "time", "date", "_at")):
            roles.add("temporal")
        if any(tok in low for tok in ("amount", "price", "revenue", "total", "cost")):
            roles.add("metric")
        return roles

    @staticmethod
    def _trim_snippet(text: str, width: int = 140) -> str:
        compact = " ".join(text.split())
        return compact[:width] + ("…" if len(compact) > width else "")

    @staticmethod
    def _clean_identifier(value: str) -> str:
        return value.strip().strip('`"[]')


class PythonAssetParser(ast.NodeVisitor):
    def __init__(self, path: str = "") -> None:
        self.path = path
        self.tables: set[str] = set()
        self.columns: set[str] = set()
        self.assets: set[str] = set()
        self.operations: set[str] = set()
        self.sql_fragments: list[SourceAssetReference] = []
        self.select_star = False
        self.alias_tables: dict[str, str] = {}
        self.lineage_pairs: set[str] = set()
        self.filters: set[str] = set()
        self.snippets: list[str] = []
        self.semantic_roles: set[str] = set()

    def parse(self, text: str) -> SourceAssetReference:
        tree = ast.parse(text)
        self.visit(tree)
        for fragment in self.sql_fragments:
            self.tables.update(fragment.tables)
            self.columns.update(fragment.columns)
            self.assets.update(fragment.assets)
            self.operations.update(fragment.operations)
            self.select_star = self.select_star or fragment.select_star
            self.lineage_pairs.update(fragment.lineage_pairs)
            self.filters.update(fragment.filters)
            self.snippets.extend(fragment.snippets)
            self.semantic_roles.update(fragment.semantic_roles)
        compiler = PythonCompilerLineage.compile(text)
        compiler_pairs = sorted(compiler.exact_pairs)
        self.lineage_pairs.update(compiler_pairs)
        self.filters.update(compiler.filters)
        self.operations.update({"python_compiler_lineage"} if compiler_pairs else set())
        exact_provenance = dict(compiler.provenance_classes)
        return SourceAssetReference(
            path=self.path,
            language="python",
            tables=sorted(
                set(self.tables)
                | set().union(*(state.source_tables for state in compiler.dataframes.values()))
            ),
            columns=sorted(set(self.columns) | set(compiler.columns)),
            assets=sorted(
                set(self.assets)
                | set().union(*(state.source_tables for state in compiler.dataframes.values()))
                | {pair.split("<-")[0] for pair in compiler_pairs}
            ),
            operations=sorted(self.operations),
            select_star=self.select_star,
            string_sql_count=len(self.sql_fragments),
            semantic_roles=sorted(self.semantic_roles),
            lineage_pairs=sorted(self.lineage_pairs),
            filters=sorted(self.filters),
            snippets=self.snippets[:6],
            exact_lineage_pairs=compiler_pairs,
            lineage_provenance=exact_provenance,
        )

    def visit_Assign(self, node: ast.Assign) -> Any:
        value = node.value
        table = self._extract_table_from_expr(value)
        if table:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.alias_tables[target.id] = table
        self.generic_visit(node)
        return node

    def visit_Call(self, node: ast.Call) -> Any:  # noqa: D401
        func_name = self._call_name(node.func)
        if func_name in _PANDAS_METHODS:
            self.operations.add(func_name)
        if func_name in {"ref", "source"}:
            for arg in node.args:
                lit = self._literal(arg)
                if lit:
                    token = str(lit).lower().split(".")[-1]
                    self.tables.add(token)
                    self.assets.add(token)
        if func_name in {
            "merge",
            "join",
            "groupby",
            "agg",
            "sort_values",
            "astype",
            "rename",
            "drop",
            "fillna",
            "query",
            "assign",
            "is_incremental",
        }:
            for keyword in node.keywords:
                if keyword.arg in {"on", "left_on", "right_on", "by", "subset", "columns"}:
                    self._consume_literal_columns(keyword.value)
                    for col in self._literal_list(keyword.value):
                        self.lineage_pairs.add(f"pyop:{func_name}:{col}")
                if keyword.arg in {"query", "expr"}:
                    lit = self._literal(keyword.value)
                    if isinstance(lit, str):
                        self.snippets.append(SQLAssetParser._trim_snippet(lit))
                        self._consume_query_string(lit)
        if func_name == "query" and node.args:
            lit = self._literal(node.args[0])
            if isinstance(lit, str):
                self.snippets.append(SQLAssetParser._trim_snippet(lit))
                self._consume_query_string(lit)
        if func_name == "rename":
            self._record_rename_lineage(node)
        if func_name == "assign":
            self._record_assign_lineage(node)
        sql_text = None
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            lit = self._literal(arg)
            if (
                lit
                and isinstance(lit, str)
                and re.search(r"\bSELECT\b.*\bFROM\b", lit, re.I | re.S)
            ):
                sql_text = lit
                break
        if sql_text:
            self.sql_fragments.append(SQLAssetParser.parse(sql_text, self.path))
        if func_name in {"merge", "join"}:
            self._record_python_join_lineage(node)
        self.generic_visit(node)
        return node

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        self._consume_literal_columns(node.slice)
        table = self._table_for_value(node.value)
        for col in self._literal_list(node.slice):
            self.columns.add(col)
            self.assets.add(col)
            self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(col))
            if table:
                self.tables.add(table)
                self.assets.add(table)
                self.assets.add(f"{table}.{col}")
                self.lineage_pairs.add(f"{table}.{col}")
        self.generic_visit(node)
        return node

    def visit_Compare(self, node: ast.Compare) -> Any:
        left = self._literal(node.left)
        if isinstance(left, str):
            low = left.lower()
            self.filters.add(low)
            self.columns.add(low)
            self.assets.add(low)
        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, str):
            value = node.value.strip()
            if re.search(r"\bSELECT\b.*\bFROM\b", value, re.I | re.S):
                self.sql_fragments.append(SQLAssetParser.parse(value, self.path))
        return node

    def _record_python_join_lineage(self, node: ast.Call) -> None:
        arg_tables: list[str] = []
        if isinstance(node.func, ast.Attribute):
            left_table = self._table_for_value(node.func.value)
            if left_table:
                arg_tables.append(left_table)
        for arg in node.args[:1]:
            table = self._table_for_value(arg)
            if table:
                arg_tables.append(table)
        for kw in node.keywords:
            if kw.arg in {"left_on", "right_on", "on"}:
                for col in self._literal_list(kw.value):
                    self.columns.add(col)
                    self.assets.add(col)
                    self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(col))
                    for table in arg_tables:
                        self.assets.add(f"{table}.{col}")
                        self.lineage_pairs.add(f"{table}.{col}")
            if kw.arg == "by":
                for col in self._literal_list(kw.value):
                    self.filters.add(col)
        if len(arg_tables) >= 2:
            self.lineage_pairs.add(f"join:{arg_tables[0]}->{arg_tables[1]}")

    def _record_rename_lineage(self, node: ast.Call) -> None:
        mapping_node = next((kw.value for kw in node.keywords if kw.arg == "columns"), None)
        if not isinstance(mapping_node, ast.Dict):
            return
        table = ""
        if isinstance(node.func, ast.Attribute):
            table = self._table_for_value(node.func.value)
        for key, value in zip(mapping_node.keys, mapping_node.values):
            old_name = self._literal(key)
            new_name = self._literal(value)
            if not isinstance(old_name, str) or not isinstance(new_name, str):
                continue
            old_low, new_low = old_name.lower(), new_name.lower()
            self.columns.update({old_low, new_low})
            self.assets.update({old_low, new_low})
            self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(old_low))
            self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(new_low))
            self.lineage_pairs.add(f"rename:{new_low}<-{old_low}")
            if table:
                self.assets.update({table, f"{table}.{old_low}", f"{table}.{new_low}"})
                self.lineage_pairs.add(f"{table}.{new_low}<-{table}.{old_low}")

    def _record_assign_lineage(self, node: ast.Call) -> None:
        table = ""
        if isinstance(node.func, ast.Attribute):
            table = self._table_for_value(node.func.value)
        for kw in node.keywords:
            if not kw.arg:
                continue
            new_col = kw.arg.lower()
            self.columns.add(new_col)
            self.assets.add(new_col)
            self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(new_col))
            refs = self._extract_columns_from_ast(kw.value)
            if not refs:
                self.lineage_pairs.add(f"assign:{new_col}")
            for ref in refs:
                self.columns.add(ref)
                self.assets.add(ref)
                self.lineage_pairs.add(f"assign:{new_col}<-{ref}")
                if table:
                    self.assets.add(table)
                    self.assets.add(f"{table}.{ref}")
                    self.assets.add(f"{table}.{new_col}")
                    self.lineage_pairs.add(f"{table}.{new_col}<-{table}.{ref}")

    def _consume_query_string(self, expr: str) -> None:
        for token in re.findall(r"([A-Za-z_][\w$]*)\s*(?:==|=|!=|>=|<=|>|<|\bin\b)", expr):
            low = token.lower()
            if low in _SQL_KEYWORDS:
                continue
            self.filters.add(low)
            self.columns.add(low)
            self.assets.add(low)
            self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(low))

    def _extract_columns_from_ast(self, node: ast.AST) -> set[str]:
        refs: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript):
                refs.update(self._literal_list(child.slice))
            elif (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value.lower() not in _SQL_KEYWORDS
            ):
                if re.fullmatch(r"[A-Za-z_][\w$]*", child.value):
                    refs.add(child.value.lower())
        return refs

    def _consume_literal_columns(self, node: ast.AST) -> None:
        for item in self._literal_list(node):
            low = item.lower()
            if low and low not in _SQL_KEYWORDS:
                self.columns.add(low)
                self.assets.add(low)
                self.semantic_roles.update(SQLAssetParser._semantic_roles_for_token(low))

    def _literal_list(self, node: ast.AST) -> list[str]:
        lit = self._literal(node)
        if isinstance(lit, str):
            return [lit.lower()]
        if isinstance(lit, list):
            return [str(item).lower() for item in lit if isinstance(item, str)]
        return []

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
                    inner = self._literal(value.value)
                    parts.append(str(inner) if inner is not None else "{}")
            return "".join(parts)
        if isinstance(node, ast.Dict):
            return {
                self._literal(key): self._literal(value)
                for key, value in zip(node.keys, node.values)
            }
        if isinstance(node, ast.List):
            return [self._literal(elt) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return [self._literal(elt) for elt in node.elts]
        if isinstance(node, ast.Set):
            return [self._literal(elt) for elt in node.elts]
        return None

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return node.id
        return ""

    def _extract_table_from_expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Call):
            func = self._call_name(node.func)
            if func in {"read_sql", "read_gbq"} and node.args:
                lit = self._literal(node.args[0])
                if isinstance(lit, str):
                    parsed = SQLAssetParser.parse(lit, self.path)
                    self.sql_fragments.append(parsed)
                    return parsed.tables[0] if parsed.tables else ""
            if func in {"ref", "source"} and node.args:
                lit = self._literal(node.args[-1])
                if isinstance(lit, str):
                    return lit.lower().split(".")[-1]
            if isinstance(node.func, ast.Attribute) and func in {
                "merge",
                "join",
                "rename",
                "assign",
            }:
                return self._table_for_value(node.func.value)
        return ""

    def _table_for_value(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.alias_tables.get(node.id, "")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return self.alias_tables.get(node.value.id, "")
        return ""


class AppSchemaParser:
    _COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.M | re.S)

    @classmethod
    def parse(cls, text: str, path: str = "") -> SourceAssetReference:
        compact = cls._COMMENT_RE.sub("", text)
        lower = compact.lower()
        language = cls._language_from_path(path)
        tables: set[str] = set()
        columns: set[str] = set()
        assets: set[str] = set()
        operations: set[str] = set()
        roles: set[str] = set()
        lineage_pairs: set[str] = set()
        filters: set[str] = set()
        snippets: list[str] = []

        for raw in re.findall(r"\bmodel\s+([A-Za-z_][\w]*)", compact):
            tables.add(raw.lower())
            assets.add(raw.lower())
            operations.add("schema")
        for raw in re.findall(r"\b(?:interface|type|enum|class)\s+([A-Za-z_][\w]*)", compact):
            tables.add(raw.lower())
            assets.add(raw.lower())
            operations.add("contract")
        for raw in re.findall(r"\b(?:prisma|db|ctx\.db)\.([A-Za-z_][\w]*)", compact):
            tables.add(raw.lower())
            assets.add(raw.lower())
            operations.add("orm")
        for table, field in re.findall(
            r"\b([A-Za-z_][\w]*)\s*:\s*z\.object\s*\((.*?)\)\s*", compact, re.S
        ):
            tables.add(table.lower())
            assets.add(table.lower())
            operations.add("validator")
            for key in re.findall(r"([A-Za-z_][\w]*)\s*:", field):
                low = key.lower()
                columns.add(low)
                assets.add(low)
                lineage_pairs.add(f"{table.lower()}.{low}")
        for quoted in re.findall(r"[\"\']([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)[\"\']", compact):
            token = quoted.lower()
            if token in _SQL_KEYWORDS or len(token) <= 1:
                continue
            assets.add(token)
            if "." in token:
                table, col = token.split(".", 1)
                tables.add(table)
                columns.add(col)
                lineage_pairs.add(f"{table}.{col}")
            elif token.endswith(("_id", "_ts", "_at", "_date")):
                columns.add(token)
        for sql_text in re.findall(
            r"(?:`([^`]*select[^`]*)`|\"([^\"]*select[^\"]*)\"|'([^']*select[^']*)')",
            compact,
            re.I | re.S,
        ):
            payload = next((frag for frag in sql_text if frag), "")
            if payload:
                parsed = SQLAssetParser.parse(payload, path)
                tables.update(parsed.tables)
                columns.update(parsed.columns)
                assets.update(parsed.assets)
                operations.update(parsed.operations)
                lineage_pairs.update(parsed.lineage_pairs)
                filters.update(parsed.filters)
                snippets.extend(parsed.snippets)
        for query_table in re.findall(
            r"\b(?:from|join|update|into)\s*:\s*['\"]([A-Za-z_][\w]*)['\"]", compact, re.I
        ):
            tables.add(query_table.lower())
            assets.add(query_table.lower())
            operations.add("query_builder")
        for enum_block in re.findall(r"z\.enum\s*\(\s*\[([^\]]+)\]\s*\)", compact, re.I | re.S):
            operations.add("enum")
            if re.search(r"status|state|type|category", lower):
                roles.add("domain")
            snippets.append(SQLAssetParser._trim_snippet(enum_block))
        for field_name, field_type in re.findall(
            r"\b([A-Za-z_][\w]*)\s*[?:]\s*(string|number|boolean|date|datetime|timestamp|int|float|decimal|bigint)",
            compact,
            re.I,
        ):
            low = field_name.lower()
            if low in _SQL_KEYWORDS:
                continue
            columns.add(low)
            assets.add(low)
            roles.update(SQLAssetParser._semantic_roles_for_token(low))
            lineage_pairs.add(low)
        for field_name, field_type in re.findall(
            r"\b([A-Za-z_][\w]*)\s+(String|Int|Float|Boolean|DateTime|Json|Decimal|BigInt)\b",
            compact,
        ):
            low = field_name.lower()
            if low in _SQL_KEYWORDS:
                continue
            columns.add(low)
            assets.add(low)
            roles.update(SQLAssetParser._semantic_roles_for_token(low))
            lineage_pairs.add(low)
        for filt in re.findall(r"\bwhere\s*:\s*\{([^\}]+)\}", compact, re.I | re.S):
            for key in re.findall(r"([A-Za-z_][\w]*)\s*:", filt):
                filters.add(key.lower())
                columns.add(key.lower())
                assets.add(key.lower())
        op_map = {
            "join": [r"\binclude\b", r"\bpopulate\b", r"\bjoin\b"],
            "aggregate": [
                r"\bgroupby\b",
                r"\baggregate\b",
                r"\bcount\b",
                r"\bsum\b",
                r"\bgroupBy\b",
            ],
            "validator": [r"\bz\.", r"\byup\.", r"\bvalidator\b"],
            "enum": [r"\benum\b", r"\bz\.enum\b"],
            "nullable": [r"\bnullable\b", r"\boptional\b", r"\?\s*:"],
            "orm": [
                r"\bprisma\.",
                r"\bsqlalchemy\b",
                r"\bsequelize\b",
                r"\btypeorm\b",
                r"\bdrizzle\b",
            ],
            "incremental": [
                r"\bis_incremental\s*\(",
                r"\bincremental\b",
                r"\bmerge_strategy\b",
                r"\bwatermark\b",
            ],
            "sql_string": [r"\bselect\b.+\bfrom\b"],
        }
        for opname, patterns in op_map.items():
            if any(re.search(pattern, lower, re.I | re.S) for pattern in patterns):
                operations.add(opname)
        return SourceAssetReference(
            path=path,
            language=language,
            tables=sorted(tables),
            columns=sorted(columns),
            assets=sorted(assets | {f"{t}.{c}" for t in tables for c in columns if c in lower}),
            operations=sorted(operations),
            select_star=False,
            string_sql_count=0,
            source_kind="application",
            semantic_roles=sorted(roles),
            lineage_pairs=sorted(lineage_pairs),
            filters=sorted(filters),
            snippets=snippets[:6],
        )

    @staticmethod
    def _language_from_path(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == ".prisma":
            return "prisma"
        if suffix in {".ts", ".tsx"}:
            return "typescript"
        return "javascript"


class DbtYamlParser:
    _SECTION_RE = re.compile(r"^\s*(models|sources|exposures):", re.M)
    _REF_RE = re.compile(r"ref\(['\"]([A-Za-z_][\w\.]*)['\"]\)", re.I)
    _SOURCE_RE = re.compile(
        r"source\(['\"]([A-Za-z_][\w]*)['\"]\s*,\s*['\"]([A-Za-z_][\w]*)['\"]\)", re.I
    )
    _TAG_RE = re.compile(r"\btags:\s*\[([^\]]+)\]", re.I)
    _TEST_RE = re.compile(r"\b(not_null|unique|relationships|accepted_values|freshness)\b", re.I)

    @classmethod
    def parse(cls, text: str, path: str = "") -> SourceAssetReference:
        lower = text.lower()
        tables: set[str] = set()
        columns: set[str] = set()
        assets: set[str] = set()
        operations: set[str] = {"dbt_yaml", "contract"}
        roles: set[str] = set()
        lineage_pairs: set[str] = set()
        filters: set[str] = set()
        snippets: list[str] = []

        if cls._SECTION_RE.search(text):
            operations.add("dbt_contract")
        current_model = ""
        in_columns = False
        for raw in text.splitlines():
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("models:", "sources:", "exposures:")):
                operations.add(stripped[:-1])
            name_match = re.match(r"^\s*-\s*name:\s*([A-Za-z_][\w]*)", line)
            if name_match:
                token = name_match.group(1).lower()
                indent = len(line) - len(line.lstrip())
                if indent <= 2:
                    current_model = token
                    tables.add(token)
                    assets.add(token)
                    in_columns = False
                elif current_model:
                    columns.add(token)
                    assets.add(token)
                    assets.add(f"{current_model}.{token}")
                    lineage_pairs.add(f"{current_model}.{token}")
                    roles.update(SQLAssetParser._semantic_roles_for_token(token))
                continue
            if re.match(r"^\s*columns:\s*$", line):
                in_columns = True
                operations.add("column_contracts")
                continue
            if in_columns and current_model:
                col_match = re.match(r"^\s*-\s*name:\s*([A-Za-z_][\w]*)", line)
                if col_match:
                    token = col_match.group(1).lower()
                    columns.add(token)
                    assets.add(token)
                    assets.add(f"{current_model}.{token}")
                    lineage_pairs.add(f"{current_model}.{token}")
                    roles.update(SQLAssetParser._semantic_roles_for_token(token))
                    continue
            for ref_name in cls._REF_RE.findall(line):
                token = ref_name.lower().split(".")[-1]
                tables.add(token)
                assets.add(token)
                operations.add("dbt_ref")
            for source_schema, source_table in cls._SOURCE_RE.findall(line):
                token = source_table.lower()
                tables.add(token)
                assets.update({token, f"{source_schema.lower()}.{token}"})
                operations.add("dbt_source")
            if "accepted_values" in stripped and current_model:
                operations.add("accepted_values")
                if columns:
                    filters.add(sorted(columns)[-1])
            if cls._TEST_RE.search(stripped):
                operations.add("tests")
            if "depends_on" in stripped or "exposures:" in stripped:
                operations.add("exposure")
            if stripped.startswith(("maturity:", "owner:")):
                operations.add("governance")
            tag_match = cls._TAG_RE.search(line)
            if tag_match:
                operations.add("tags")
                for tag in re.findall(r"[A-Za-z_][\w-]*", tag_match.group(1)):
                    assets.add(tag.lower())
            if stripped.startswith(("description:", "tests:")):
                snippets.append(SQLAssetParser._trim_snippet(stripped))

        if any(role == "domain" for role in roles):
            operations.add("enum")
        return SourceAssetReference(
            path=path,
            language="yaml",
            tables=sorted(tables),
            columns=sorted(columns),
            assets=sorted(assets),
            operations=sorted(operations),
            select_star=False,
            string_sql_count=0,
            source_kind="application",
            semantic_roles=sorted(roles),
            lineage_pairs=sorted(lineage_pairs),
            filters=sorted(filters),
            snippets=snippets[:6],
        )


class ASTChangeProver:
    def __init__(
        self,
        graph_json: dict,
        source_paths: list[str],
        max_files: int = 200,
        boundary_hops: int = 1,
    ) -> None:
        self.graph_json = graph_json or {"nodes": [], "edges": []}
        self.source_paths = [str(p) for p in source_paths if p]
        self.max_files = max(1, int(max_files))
        self.boundary_hops = max(0, int(boundary_hops))
        self.graph = nx.DiGraph()
        for node in self.graph_json.get("nodes", []):
            self.graph.add_node(node["id"], **node)
        for edge in self.graph_json.get("edges", []):
            self.graph.add_edge(edge["source"], edge["target"], relation=edge.get("relation", ""))
        self._sources = self._scan_sources()
        self._macro_callers: dict[str, list[int]] = defaultdict(list)
        self._macro_defs: dict[str, list[int]] = defaultdict(list)
        self._asset_index: dict[str, set[int]] = defaultdict(set)
        for idx, source in enumerate(self._sources):
            for macro in getattr(source, "macro_calls", []) or []:
                self._macro_callers[macro].append(idx)
            for macro in getattr(source, "macro_defs", []) or []:
                self._macro_defs[macro].append(idx)
            for token in source.match_tokens:
                self._asset_index[token.lower()].add(idx)
        self._full_scan_fallbacks = 0
        self._candidate_source_count = 0

    def prove(self, drift_report: dict) -> ProofBundle:
        bundle = ProofBundle(
            scanned_paths=self.source_paths,
            scanned_files=len(self._sources),
            indexed_token_count=len(self._asset_index),
        )
        for event in drift_report.get("events", []) or []:
            node_id = str(event.get("node_id") or "")
            if not node_id:
                continue
            direct_assets, boundary_assets = self._event_assets(event)
            candidate_indices = self._candidate_sources(direct_assets, boundary_assets)
            if not candidate_indices:
                self._full_scan_fallbacks += 1
                candidate_indices = set(range(len(self._sources)))
            bundle.candidate_source_count += len(candidate_indices)
            for idx in sorted(candidate_indices):
                source = self._sources[idx]
                source_assets = source.match_tokens
                direct_hits = sorted(direct_assets & source_assets)
                downstream_hits = sorted(boundary_assets & source_assets)
                lineage_hits = sorted(
                    item
                    for item in source.lineage_pairs
                    if any(token in item for token in direct_assets | boundary_assets)
                )
                exact_lineage_hits = sorted(
                    item
                    for item in (getattr(source, "exact_lineage_pairs", []) or [])
                    if any(token in item for token in direct_assets | boundary_assets)
                )
                filter_hits = sorted(
                    item
                    for item in source.filters
                    if item in direct_assets
                    or item in boundary_assets
                    or item.split(".")[-1] in direct_assets
                )
                macro_hits = self._macro_hits(idx, direct_assets | boundary_assets)
                if macro_hits:
                    downstream_hits = sorted(set(downstream_hits) | set(macro_hits))
                if (
                    not direct_hits
                    and not downstream_hits
                    and not lineage_hits
                    and not exact_lineage_hits
                    and not filter_hits
                ):
                    continue
                finding = self._make_finding(
                    event,
                    source,
                    direct_hits,
                    downstream_hits,
                    lineage_hits,
                    exact_lineage_hits,
                    filter_hits,
                )
                bundle.findings.append(finding)
                bundle.direct_hit_count += (
                    len(direct_hits) + len(lineage_hits) + len(exact_lineage_hits)
                )
                bundle.downstream_hit_count += len(downstream_hits) + len(filter_hits)
        provenance_nodes: set[str] = set()
        provenance_edges = 0
        for source in self._sources:
            for pair in getattr(source, "exact_lineage_pairs", []) or []:
                if "<-" not in pair:
                    continue
                left, right = pair.split("<-", 1)
                provenance_nodes.add(left)
                provenance_nodes.add(right)
                provenance_edges += 1
        bundle.full_scan_fallbacks = self._full_scan_fallbacks
        bundle.provenance_node_count = len(provenance_nodes)
        bundle.provenance_edge_count = provenance_edges
        bundle.findings.sort(key=lambda f: (-f.confidence, f.asset_path, f.node_id))
        return bundle

    def _scan_sources(self) -> list[SourceAssetReference]:
        files: list[Path] = []
        for raw in self.source_paths:
            path = Path(raw)
            if not path.exists():
                continue
            if path.is_file() and path.suffix.lower() in _APP_FILE_SUFFIXES:
                files.append(path)
                continue
            if path.is_dir():
                for pattern in _SCAN_GLOB_PATTERNS:
                    files.extend(sorted(path.rglob(pattern)))
        sources: list[SourceAssetReference] = []
        seen: set[str] = set()
        for path in files:
            if len(sources) >= self.max_files:
                break
            norm = str(path.resolve())
            if norm in seen:
                continue
            seen.add(norm)
            try:
                cache_key = _path_cache_key(path)
                parsed = _PARSE_CACHE.get(cache_key)
                if parsed is None:
                    try:
                        text = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        text = path.read_text(encoding="latin-1")
                    parsed = _parse_source_text(path, text)
                    _PARSE_CACHE[cache_key] = parsed
            except SyntaxError as exc:
                log.debug("AST prover could not parse %s: %s", path, exc)
                continue
            except Exception as exc:
                log.debug("AST prover skipped %s: %s", path, exc)
                continue
            sources.append(parsed)
        return sources

    def _candidate_sources(self, direct_assets: set[str], boundary_assets: set[str]) -> set[int]:
        candidates: set[int] = set()
        for token in direct_assets | boundary_assets:
            candidates.update(self._asset_index.get(str(token).lower(), set()))
        for token in direct_assets | boundary_assets:
            if not str(token).startswith("macro:"):
                continue
            candidates.update(self._macro_callers.get(str(token).lower(), []))
            candidates.update(self._macro_defs.get(str(token).lower(), []))
        return candidates

    def _macro_hits(self, source_idx: int, event_assets: set[str]) -> list[str]:
        source = self._sources[source_idx]
        hits: set[str] = set()
        lower_assets = {str(asset).lower() for asset in event_assets}
        for macro in getattr(source, "macro_calls", []) or []:
            if any(
                str(asset).lower() in line.lower()
                for asset in lower_assets
                for line in (getattr(source, "lineage_pairs", []) or [])
            ):
                hits.add(macro)
            for def_idx in self._macro_defs.get(macro, []):
                definition = self._sources[def_idx]
                if definition.match_tokens & lower_assets:
                    hits.add(macro)
        return sorted(hits)

    def _event_assets(self, event: dict) -> tuple[set[str], set[str]]:
        node_id = str(event.get("node_id") or "").lower()
        before = event.get("before") or {}
        after = event.get("after") or {}
        direct_assets: set[str] = {node_id} if node_id else set()
        boundary_assets: set[str] = set()

        table = before.get("table") or after.get("table") or node_id.split(".")[0]
        col = (
            before.get("name")
            or after.get("name")
            or (node_id.split(".")[-1] if "." in node_id else "")
        )
        if table:
            direct_assets.add(str(table).lower())
        if col:
            direct_assets.add(str(col).lower())
            if table:
                direct_assets.add(f"{str(table).lower()}.{str(col).lower()}")
        for candidate in (
            before.get("name"),
            after.get("name"),
            before.get("column_name"),
            after.get("column_name"),
        ):
            if candidate:
                direct_assets.add(str(candidate).lower())
        for candidate in (
            before.get("domain_values"),
            after.get("domain_values"),
            before.get("enum_values"),
            after.get("enum_values"),
        ):
            if isinstance(candidate, list):
                direct_assets.update(str(item).lower() for item in candidate if item)

        if node_id and node_id in self.graph:
            boundary_assets.update(n.lower() for n in self._neighbors(node_id, self.boundary_hops))
        table_id = str(table).lower() if table else ""
        if table_id and table_id in self.graph:
            boundary_assets.update(n.lower() for n in self._neighbors(table_id, self.boundary_hops))
        boundary_assets -= direct_assets
        return direct_assets, boundary_assets

    def _neighbors(self, node_id: str, hops: int) -> set[str]:
        frontier = {node_id}
        seen = {node_id}
        for _ in range(max(hops, 0)):
            nxt: set[str] = set()
            for node in frontier:
                nxt.update(self.graph.successors(node))
                nxt.update(self.graph.predecessors(node))
            nxt -= seen
            seen.update(nxt)
            frontier = nxt
        return seen

    def _make_finding(
        self,
        event: dict,
        source: SourceAssetReference,
        direct_hits: list[str],
        downstream_hits: list[str],
        lineage_hits: list[str],
        exact_lineage_hits: list[str],
        filter_hits: list[str],
    ) -> ProofFinding:
        change_type = str(event.get("change_type") or "")
        operations = source.operations
        direct_count = len(direct_hits) + len(lineage_hits) + len(exact_lineage_hits)
        confidence = 0.32 + (0.15 * min(direct_count, 3)) + (0.07 * min(len(downstream_hits), 2))
        if getattr(source, "source_kind", "warehouse") == "application":
            confidence += 0.12
        if source.language == "yaml":
            confidence += 0.08
        if lineage_hits:
            confidence += 0.16
        if exact_lineage_hits:
            confidence += 0.24
        if filter_hits:
            confidence += 0.12
        if any(
            op in operations
            for op in {
                "join",
                "groupby",
                "aggregate",
                "window",
                "cast",
                "merge",
                "group_by",
                "merge_into",
                "incremental",
                "dbt_contract",
                "column_contracts",
            }
        ):
            confidence += 0.15
        if source.select_star:
            confidence += 0.08
        if getattr(source, "semantic_roles", None):
            confidence += min(0.08, 0.02 * len(source.semantic_roles))
        confidence = min(0.98, confidence)
        severity = (
            "HIGH"
            if direct_count >= 2 or confidence >= 0.8 or filter_hits or exact_lineage_hits
            else "MEDIUM"
            if (direct_hits or lineage_hits)
            else "LOW"
        )
        failure_mode = self._failure_mode(
            change_type,
            operations,
            direct_hits,
            downstream_hits,
            lineage_hits + exact_lineage_hits,
            filter_hits,
        )
        suggested_fix = self._suggested_fix(change_type, operations, direct_hits, filter_hits)
        return ProofFinding(
            node_id=str(event.get("node_id") or ""),
            asset_path=source.path,
            language=source.language,
            direct_hits=direct_hits[:6],
            downstream_hits=downstream_hits[:6],
            operations=operations[:8],
            expected_failure_mode=failure_mode,
            suggested_fix=suggested_fix,
            severity=severity,
            confidence=confidence,
            lineage_hits=lineage_hits[:6],
            exact_lineage_hits=exact_lineage_hits[:6],
            lineage_provenance={
                k: v
                for k, v in (getattr(source, "lineage_provenance", {}) or {}).items()
                if any(k == token.split("<-")[0].split("~")[0] for token in exact_lineage_hits[:6])
            },
            filters=filter_hits[:6],
            evidence_snippets=source.snippets[:4],
        )

    @staticmethod
    def _failure_mode(
        change_type: str,
        operations: list[str],
        direct_hits: list[str],
        downstream_hits: list[str],
        lineage_hits: list[str],
        filter_hits: list[str],
    ) -> str:
        ops = set(operations)
        if ops & {"dbt_contract", "column_contracts", "tests"} and change_type in {
            "COLUMN_REMOVED",
            "COLUMN_RENAMED",
            "TYPE_CHANGED",
            "NULLABLE_CHANGED",
        }:
            return "dbt contract metadata still encodes the legacy field behavior, so tests, exposures, or consumers can drift before the warehouse change is fully rolled out."
        if filter_hits and change_type in {
            "DOMAIN_EXPANSION",
            "DOMAIN_DRIFT",
            "STATS_DRIFTED",
            "TYPE_CHANGED",
        }:
            return "Hardcoded downstream filters still pin the legacy domain, so new values can be silently dropped while the pipeline looks green."
        if ops & {"orm", "validator", "contract", "schema"}:
            if change_type in {
                "COLUMN_RENAMED",
                "TABLE_RENAMED",
                "COLUMN_REMOVED",
                "TABLE_REMOVED",
            }:
                return "Application/backend schemas still reference the legacy field, so deploys can break before warehouse consumers are updated."
            if change_type in {"TYPE_CHANGED", "TYPE_NARROWING"}:
                return "Application validators or ORM models still expect the old shape, so API writes/reads can diverge from warehouse expectations."
        if change_type in {"COLUMN_REMOVED", "TABLE_REMOVED"}:
            if {"join", "merge"} & ops:
                return "Direct compile/runtime failure in join logic as soon as the removed field disappears."
            return "Direct compile/runtime failure in downstream SQL/Python that still selects the removed field."
        if change_type in {"TYPE_CHANGED", "TYPE_NARROWING"}:
            if "incremental" in ops:
                return "Incremental models can persist corrupted state or miss reconciliation when the underlying type contract changes."
            if {"aggregate", "cast", "groupby", "group_by", "window", "astype"} & ops:
                return "Type-sensitive transforms can error or silently coerce values, producing metric drift."
            return (
                "Consumers still reading the old type can fail casts or serialize the wrong shape."
            )
        if change_type == "NULLABLE_CHANGED":
            if {"join", "merge"} & ops:
                return "Null-intolerant joins can silently drop rows or explode duplicate handling."
            return "Writers/readers that assume the old nullability contract can fail unexpectedly."
        if change_type == "STATS_DRIFTED":
            if "incremental" in ops:
                return "Incremental logic can stay green while state diverges from the source of truth after deletes, replays, or late-arriving data."
            return "The pipeline can stay green while row counts, join completeness, or KPI semantics drift."
        if change_type in {"COLUMN_RENAMED", "TABLE_RENAMED"}:
            return "Downstream assets still reference the old name and will fail until dual-read or aliasing is added."
        if lineage_hits:
            return "Lineage-backed source references show the changed field still feeds downstream joins, metrics, validators, or contracts that need rollout control."
        if downstream_hits:
            return "A nearby downstream asset is structurally exposed and likely to break first."
        if direct_hits:
            return "The changed asset is referenced directly in code paths that will need rollout controls."
        return "The changed asset is referenced in a structurally relevant source path."

    @staticmethod
    def _suggested_fix(
        change_type: str, operations: list[str], direct_hits: list[str], filter_hits: list[str]
    ) -> str:
        ops = set(operations)
        if filter_hits:
            return "Add dual-domain handling first: patch hardcoded filters/groupings, replay with the new domain values, then remove the legacy-only branch after row-level parity checks pass."
        if ops & {"dbt_contract", "column_contracts", "tests"}:
            return "Update the dbt contract/tests first, keep a compatibility alias during rollout, and prove both the model SQL and contract surface are aligned before merge."
        if ops & {"orm", "validator", "contract", "schema"}:
            return "Ship a compatibility layer first: dual-read/dual-write the old and new field names, update backend validators/ORM models, then remove the legacy path after replay passes."
        if change_type in {"COLUMN_REMOVED", "TABLE_REMOVED", "COLUMN_RENAMED", "TABLE_RENAMED"}:
            return "Add a compatibility alias or shadow field first, patch direct readers, then remove the legacy path after traffic drops to zero."
        if change_type in {"TYPE_CHANGED", "TYPE_NARROWING"}:
            if "incremental" in ops:
                return "Backfill into a shadow column, replay incremental windows, and prove state reconciliation before flipping the contract."
            if {"aggregate", "cast", "astype", "window", "groupby", "group_by"} & ops:
                return "Introduce a shadow column with the new type, cast explicitly in downstream models, and validate aggregates before cutover."
            return "Dual-write into the new type and patch readers to cast explicitly before flipping the contract."
        if change_type == "NULLABLE_CHANGED":
            if {"join", "merge"} & ops:
                return "Backfill nulls, add COALESCE/defensive join logic, and keep NOT NULL enforcement until writer coverage is confirmed."
            return "Backfill nulls and add not_null tests before enforcing the tighter contract."
        if change_type == "STATS_DRIFTED":
            if "incremental" in ops:
                return "Run state-reconciliation checks, replay late-arriving windows, and prove the incremental model self-heals before merging."
            return "Replay representative workloads, compare key aggregates, and add quality thresholds before merging."
        if direct_hits:
            return "Patch the direct consumer path before merge so the change does not page the on-call engineer later."
        return "Review the referenced asset and add a rollout guard before merge."


def load_bundle(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
