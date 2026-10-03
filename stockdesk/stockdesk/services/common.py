"""Small shared query and audit primitives; SQL identifiers are fixed allowlists."""

import json
import sqlite3
from typing import Any

from ..errors import DomainError, missing
from ..validation import identifier

TABLES = frozenset({"users", "products", "warehouses", "suppliers", "purchase_orders", "purchase_lines", "stock_counts", "count_lines"})


def row(connection: sqlite3.Connection, table: str, row_id: int, *, active: bool = False) -> dict:
    if table not in TABLES:
        raise RuntimeError("Unknown internal table")
    identifier(row_id)
    result = connection.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if result is None:
        raise missing(table.replace("_", " ").rstrip("s"))
    result = dict(result)
    if active and not result.get("active", result.get("enabled", 1)):
        raise DomainError("This record is inactive.", "inactive", 409)
    return result


def audit(connection: sqlite3.Connection, actor: int | None, action: str,
          entity: str, entity_id: int, details: dict[str, Any] | None = None) -> None:
    connection.execute(
        "INSERT INTO audit_events(actor_id,action,entity_type,entity_id,details) VALUES (?,?,?,?,?)",
        (actor, action, entity, entity_id, json.dumps(details or {}, sort_keys=True, separators=(",", ":"))),
    )
