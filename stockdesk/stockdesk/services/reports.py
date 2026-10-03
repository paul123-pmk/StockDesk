"""Read models and safe CSV exports. Values are operational estimates, not accounts."""

import csv
import io
import sqlite3
from math import ceil
from typing import Any

from ..validation import integer, text
from .common import row


def _search(value: str) -> str:
    value = text(value, "Search", maximum=100, empty=True)
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def paginated(connection: sqlite3.Connection, sql: str, parameters: tuple = (), *,
              page: int = 1, page_size: int = 50) -> dict:
    page = integer(page, "Page", minimum=1, maximum=1_000_000)
    page_size = integer(page_size, "Page size", minimum=1, maximum=200)
    total = connection.execute("SELECT COUNT(*) FROM (" + sql + ")", parameters).fetchone()[0]
    rows = connection.execute(sql + " LIMIT ? OFFSET ?", (*parameters, page_size, (page-1)*page_size)).fetchall()
    return {"items": [dict(item) for item in rows], "total": total, "page": page,
            "page_size": page_size, "pages": max(1, ceil(total/page_size))}


def products(connection: sqlite3.Connection, *, search: str = "", page: int = 1, page_size: int = 50) -> dict:
    term = _search(search)
    return paginated(connection, "SELECT * FROM products WHERE sku LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' ORDER BY sku",
                     (term, term), page=page, page_size=page_size)


def inventory(connection: sqlite3.Connection, *, warehouse_id: int | None = None,
              search: str = "", page: int = 1, page_size: int = 50, low_only: bool = False) -> dict:
    term = _search(search)
    sql = (
        "SELECT p.id AS product_id,p.sku,p.name,p.unit,p.reorder_level,p.unit_cost_minor,"
        "w.id AS warehouse_id,w.code AS warehouse_code,w.name AS warehouse_name,"
        "COALESCE(b.quantity,0) AS quantity,COALESCE(b.version,0) AS balance_version,"
        "COALESCE(b.quantity,0)*p.unit_cost_minor AS value_minor "
        "FROM products p CROSS JOIN warehouses w LEFT JOIN balances b "
        "ON b.product_id=p.id AND b.warehouse_id=w.id "
        "WHERE p.active=1 AND w.active=1 AND (p.sku LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\')"
    )
    params = (term, term)
    if warehouse_id is not None:
        row(connection, "warehouses", warehouse_id)
        sql += " AND w.id=?"
        params += (warehouse_id,)
    if low_only:
        sql += " AND p.reorder_level>0 AND COALESCE(b.quantity,0)<=p.reorder_level"
    return paginated(connection, sql + " ORDER BY p.sku,w.code", params, page=page, page_size=page_size)


def named_records(connection: sqlite3.Connection, table: str) -> list[dict]:
    if table not in {"warehouses", "suppliers"}:
        raise RuntimeError("Unknown report table")
    return [dict(item) for item in connection.execute(f"SELECT * FROM {table} ORDER BY code")]


def active_products(connection: sqlite3.Connection) -> list[dict]:
    return [dict(item) for item in connection.execute("SELECT * FROM products WHERE active=1 ORDER BY sku")]


def movements(connection: sqlite3.Connection, *, product_id: int | None = None,
              page: int = 1, page_size: int = 50) -> dict:
    sql = ("SELECT m.*,p.sku,p.name,w.code AS warehouse_code,o.kind,o.note,u.username "
           "FROM movements m JOIN products p ON p.id=m.product_id JOIN warehouses w ON w.id=m.warehouse_id "
           "JOIN operations o ON o.id=m.operation_id JOIN users u ON u.id=o.actor_id")
    params = ()
    if product_id is not None:
        row(connection, "products", product_id)
        sql += " WHERE m.product_id=?"
        params = (product_id,)
    return paginated(connection, sql + " ORDER BY m.id DESC", params, page=page, page_size=page_size)


def orders(connection: sqlite3.Connection, *, page: int = 1, page_size: int = 50) -> dict:
    return paginated(connection,
        "SELECT o.*,s.name AS supplier_name,w.name AS warehouse_name FROM purchase_orders o "
        "JOIN suppliers s ON s.id=o.supplier_id JOIN warehouses w ON w.id=o.warehouse_id ORDER BY o.id DESC",
        page=page, page_size=page_size)


def counts(connection: sqlite3.Connection, *, page: int = 1, page_size: int = 50) -> dict:
    return paginated(connection,
        "SELECT c.*,w.name AS warehouse_name FROM stock_counts c JOIN warehouses w ON w.id=c.warehouse_id ORDER BY c.id DESC",
        page=page, page_size=page_size)


def audit_events(connection: sqlite3.Connection, *, page: int = 1, page_size: int = 50) -> dict:
    return paginated(connection,
        "SELECT a.*,u.username FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC",
        page=page, page_size=page_size)


def dashboard(connection: sqlite3.Connection) -> dict:
    values = connection.execute("SELECT b.quantity,p.unit_cost_minor FROM balances b JOIN products p ON p.id=b.product_id").fetchall()
    # Python integers avoid SQLite's fixed-width overflow when many values are summed.
    return {
        "products": connection.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0],
        "warehouses": connection.execute("SELECT COUNT(*) FROM warehouses WHERE active=1").fetchone()[0],
        "units": sum(item["quantity"] for item in values),
        "value_minor": sum(item["quantity"]*item["unit_cost_minor"] for item in values),
        "open_orders": connection.execute("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('submitted','partial')").fetchone()[0],
        "low_stock": inventory(connection, low_only=True, page_size=5),
        "recent": movements(connection, page_size=8),
    }


def reconcile(connection: sqlite3.Connection) -> dict:
    """Check materialized balances and every recorded running balance without modifying data."""
    expected = {}
    issues = []
    for movement in connection.execute("SELECT * FROM movements ORDER BY id"):
        key = (movement["product_id"], movement["warehouse_id"])
        expected[key] = expected.get(key, 0) + movement["delta"]
        if expected[key] != movement["balance_after"]:
            issues.append({"type": "running_balance", "movement_id": movement["id"],
                           "expected": expected[key], "recorded": movement["balance_after"]})
    actual = {(item["product_id"], item["warehouse_id"]): item["quantity"] for item in connection.execute("SELECT * FROM balances")}
    for key in sorted(expected.keys() | actual.keys()):
        if expected.get(key, 0) != actual.get(key, 0):
            issues.append({"type": "balance", "product_id": key[0], "warehouse_id": key[1],
                           "ledger_quantity": expected.get(key, 0), "stored_quantity": actual.get(key, 0)})
    return {"ok": not issues, "issues": issues, "checked_balances": len(expected.keys() | actual.keys())}


def csv_text(items: list[dict], columns: list[str]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    for item in items:
        cells: list[Any] = []
        for column in columns:
            value = item.get(column, "")
            if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
                value = "'" + value
            cells.append(value)
        writer.writerow(cells)
    return stream.getvalue()
