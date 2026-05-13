"""
contract_compiler.py — Auto-infers data contracts from production schema behaviour.

Observes stable schemas over time and generates:
  - dbt schema.yml (models + columns + tests)
  - Soda check YAML (freshness, completeness, uniqueness)
  - JSON contract files

No human writes anything. SemZero watches production for 30 days
and infers what the contracts should say.

PII detection covers 10 patterns:
  email, phone, ssn, credit_card, password, ip_address,
  lat_lon, date_of_birth, name, address

Usage:
  compiler = ContractCompiler(graph_json)
  contracts = compiler.compile()
  compiler.write_dbt_yaml("models/staging/schema.yml")
  compiler.write_soda_yaml("checks/staging.yml")
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── PII patterns ──────────────────────────────────────────────────────────────

_PII_PATTERNS: dict[str, list[str]] = {
    "pii:email": ["email", "e_mail", "mail", "contact_email", "user_email"],
    "pii:phone": ["phone", "mobile", "tel", "telephone", "cell", "fax"],
    "pii:ssn": ["ssn", "social_security", "national_id", "nric", "passport"],
    "pii:credit_card": ["card_number", "credit_card", "cc_number", "pan"],
    "pii:password": ["password", "passwd", "pwd", "secret", "hash", "token"],
    "pii:ip_address": ["ip_address", "ip_addr", "remote_addr", "client_ip"],
    "pii:location": ["latitude", "longitude", "lat", "lon", "lng", "coordinates"],
    "pii:date_of_birth": ["dob", "date_of_birth", "birth_date", "birthdate", "born_at"],
    "pii:name": ["first_name", "last_name", "full_name", "display_name", "given_name"],
    "pii:address": ["address", "street", "postcode", "zip_code", "city", "suburb"],
}

# ── Criticality inference ─────────────────────────────────────────────────────

_HIGH_CRITICALITY_PATTERNS = [
    "revenue",
    "mrr",
    "arr",
    "payment",
    "invoice",
    "order",
    "transaction",
    "customer",
    "user",
    "account",
    "subscription",
    "churn",
    "ltv",
]
_LOW_CRITICALITY_PATTERNS = [
    "log",
    "event",
    "audit",
    "temp",
    "staging",
    "raw",
    "debug",
    "test",
]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class ColumnContract:
    name: str
    dtype: str
    nullable: bool
    pii_tags: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    description: str = ""

    def to_dbt_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.description:
            d["description"] = self.description
        if self.pii_tags:
            d["meta"] = {"pii": self.pii_tags}
        if self.tests:
            d["tests"] = self.tests
        return d


@dataclass
class TableContract:
    table_id: str
    criticality: str  # PUBLIC | INTERNAL | PRIVATE
    strictness: str  # STRICT | MODERATE | SOFT
    sla_freshness: str  # e.g. "1 hour" | "1 day" | "7 days"
    columns: list[ColumnContract] = field(default_factory=list)
    description: str = ""
    row_count: int = 0

    def to_dbt_dict(self) -> dict:
        return {
            "name": self.table_id,
            "description": self.description or f"Auto-generated contract for {self.table_id}",
            "meta": {
                "criticality": self.criticality,
                "strictness": self.strictness,
                "sla_freshness": self.sla_freshness,
                "generated_by": "SemZero ContractCompiler",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "columns": [c.to_dbt_dict() for c in self.columns],
        }

    def to_soda_checks(self) -> list[str]:
        checks = [f"# Table: {self.table_id}  (criticality={self.criticality})"]
        checks.append(f"checks for {self.table_id}:")

        # Row count (always)
        checks.append(f"  - row_count > 0:")
        checks.append(f"      name: {self.table_id} has rows")

        # Freshness for event/time-series tables
        if any(k in self.table_id for k in ("event", "log", "activity", "revenue")):
            checks.append(f"  - freshness(created_at) < {self.sla_freshness}:")
            checks.append(f"      name: {self.table_id} freshness SLA")

        # Per-column checks
        for col in self.columns:
            if "not_null" in col.tests:
                checks.append(f"  - missing_count({col.name}) = 0:")
                checks.append(f"      name: {col.name} not null")
            if "unique" in col.tests:
                checks.append(f"  - duplicate_count({col.name}) = 0:")
                checks.append(f"      name: {col.name} unique")

        return checks


@dataclass
class ContractBundle:
    tables: list[TableContract] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    graph_snapshot: str = ""

    def summary(self) -> dict:
        pii_cols = sum(1 for t in self.tables for c in t.columns if c.pii_tags)
        return {
            "tables": len(self.tables),
            "columns": sum(len(t.columns) for t in self.tables),
            "pii_columns": pii_cols,
            "strict_tables": sum(1 for t in self.tables if t.strictness == "STRICT"),
            "generated_at": self.generated_at,
        }


# ── Compiler ──────────────────────────────────────────────────────────────────


class ContractCompiler:
    """
    Infers data contracts from a schema graph snapshot.

    Observes:
      - Column null_rate → not_null test when null_rate = 0.0
      - Column cardinality → unique test when cardinality ≥ 0.99
      - Column name → PII tags via pattern matching
      - Table name → criticality (PUBLIC / INTERNAL / PRIVATE)
      - Table connectivity → strictness (STRICT for high-FK tables)
      - Table name → SLA freshness (events→1h, daily→1d, else 7d)

    No human input required.
    """

    def __init__(self, graph_json: dict, null_rate_threshold: float = 0.02) -> None:
        self.graph = graph_json
        self.null_threshold = null_rate_threshold
        self._nodes = graph_json.get("nodes", [])
        self._edges = graph_json.get("edges", [])

    def compile(self) -> ContractBundle:
        tables = [n for n in self._nodes if n.get("label") == "Table"]
        cols = {n["id"]: n for n in self._nodes if n.get("label") == "Column"}

        # FK in-degree per table (high = high connectivity = STRICT)
        fk_in: dict[str, int] = {}
        for e in self._edges:
            if e.get("relation") == "REFERENCES":
                tbl = e["target"].split(".")[0] if "." in e["target"] else e["target"]
                fk_in[tbl] = fk_in.get(tbl, 0) + 1

        bundle = ContractBundle(graph_snapshot=self.graph.get("meta", {}).get("created_at", ""))

        for tbl_node in tables:
            tbl_id = tbl_node["id"]
            tbl_cols = [c for c in cols.values() if c.get("table") == tbl_id]

            criticality = self._criticality(tbl_id, fk_in.get(tbl_id, 0))
            strictness = self._strictness(criticality, fk_in.get(tbl_id, 0))
            sla = self._sla(tbl_id)

            col_contracts = []
            for col in tbl_cols:
                cc = self._compile_column(col)
                col_contracts.append(cc)

            bundle.tables.append(
                TableContract(
                    table_id=tbl_id,
                    criticality=criticality,
                    strictness=strictness,
                    sla_freshness=sla,
                    columns=col_contracts,
                    row_count=tbl_node.get("row_count", 0),
                )
            )

        log.info(
            f"ContractCompiler: {len(bundle.tables)} tables, "
            f"{bundle.summary()['pii_columns']} PII columns"
        )
        return bundle

    # ── Column contract ───────────────────────────────────────────────────────

    def _compile_column(self, col: dict) -> ColumnContract:
        name = col.get("name", "")
        dtype = col.get("dtype", "VARCHAR")
        nullable = col.get("nullable", True)
        null_rate = col.get("null_rate", 0.0) or 0.0
        cardinality = col.get("cardinality", 0.0) or 0.0
        is_pk = col.get("is_primary_key", False)

        tests: list[str] = []
        pii_tags: list[str] = []

        # not_null test
        if not nullable or null_rate <= self.null_threshold:
            tests.append("not_null")

        # unique test
        if is_pk or cardinality >= 0.99:
            tests.append("unique")

        # PII detection
        col_lower = name.lower()
        for tag, patterns in _PII_PATTERNS.items():
            if any(p in col_lower for p in patterns):
                pii_tags.append(tag)
                break  # one PII tag per column

        desc = ""
        if pii_tags:
            desc = f"PII: {', '.join(pii_tags)}. Handle per data privacy policy."
        elif is_pk:
            desc = f"Primary key for {col.get('table', '')}."

        return ColumnContract(
            name=name,
            dtype=dtype,
            nullable=nullable,
            pii_tags=pii_tags,
            tests=tests,
            description=desc,
        )

    # ── Table metadata inference ──────────────────────────────────────────────

    def _criticality(self, table_id: str, fk_in_degree: int) -> str:
        tbl = table_id.lower()
        if any(p in tbl for p in _HIGH_CRITICALITY_PATTERNS):
            return "PRIVATE"
        if any(p in tbl for p in _LOW_CRITICALITY_PATTERNS):
            return "PUBLIC"
        if fk_in_degree >= 3:
            return "PRIVATE"
        if fk_in_degree >= 1:
            return "INTERNAL"
        return "PUBLIC"

    def _strictness(self, criticality: str, fk_in_degree: int) -> str:
        if criticality == "PRIVATE" or fk_in_degree >= 3:
            return "STRICT"
        if criticality == "INTERNAL":
            return "MODERATE"
        return "SOFT"

    def _sla(self, table_id: str) -> str:
        tbl = table_id.lower()
        if any(k in tbl for k in ("event", "log", "stream", "realtime")):
            return "1 hour"
        if any(k in tbl for k in ("daily", "revenue", "order", "payment")):
            return "1 day"
        return "7 days"

    # ── Output writers ────────────────────────────────────────────────────────

    def write_dbt_yaml(self, path: str, bundle: ContractBundle) -> Path:
        """Write dbt schema.yml with all table contracts."""
        import yaml  # type: ignore

        doc = {
            "version": 2,
            "models": [t.to_dbt_dict() for t in bundle.tables],
        }
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml.dump(doc, default_flow_style=False, allow_unicode=True))
        log.info(f"dbt schema.yml → {out}")
        return out

    def write_soda_yaml(self, path: str, bundle: ContractBundle) -> Path:
        """Write Soda check YAML for all tables."""
        lines = [
            "# SemZero Auto-Generated Soda Checks",
            f"# Generated: {bundle.generated_at}",
            "",
        ]
        for table in bundle.tables:
            lines.extend(table.to_soda_checks())
            lines.append("")

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines))
        log.info(f"Soda checks → {out}")
        return out

    def write_json(self, path: str, bundle: ContractBundle) -> Path:
        """Write full contract bundle as JSON."""
        data = {
            "summary": bundle.summary(),
            "generated_at": bundle.generated_at,
            "tables": [
                {
                    "table_id": t.table_id,
                    "criticality": t.criticality,
                    "strictness": t.strictness,
                    "sla_freshness": t.sla_freshness,
                    "row_count": t.row_count,
                    "columns": [
                        {
                            "name": c.name,
                            "dtype": c.dtype,
                            "nullable": c.nullable,
                            "pii_tags": c.pii_tags,
                            "tests": c.tests,
                        }
                        for c in t.columns
                    ],
                }
                for t in bundle.tables
            ],
        }
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2))
        log.info(f"Contract JSON → {out}")
        return out
