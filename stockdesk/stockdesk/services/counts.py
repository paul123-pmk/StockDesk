"""Cycle counts use snapshots and balance versions to reject stale adjustments."""

import sqlite3

from ..db import atomic
from ..errors import DomainError, conflict
from ..validation import text
from .auth import require_role
from .common import audit, row
from .inventory import apply_delta, begin_operation, get_balance
from .purchasing import normalize_lines


def get_count(connection: sqlite3.Connection, count_id: int) -> dict:
    count = row(connection, "stock_counts", count_id)
    count["warehouse_name"] = row(connection, "warehouses", count["warehouse_id"])["name"]
    count["lines"] = [dict(item) for item in connection.execute(
        "SELECT c.*,p.sku,p.name,p.unit FROM count_lines c JOIN products p ON p.id=c.product_id "
        "WHERE c.count_id=? ORDER BY p.sku", (count_id,))]
    return count


def create_count(connection: sqlite3.Connection, actor: int, *, warehouse_id: int, note: str = "") -> dict:
    note = text(note, "Note", maximum=500, empty=True)
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        row(connection, "warehouses", warehouse_id, active=True)
        products = connection.execute("SELECT id FROM products WHERE active=1 ORDER BY id LIMIT 501").fetchall()
        if not products:
            raise DomainError("Create an active product before starting a count.")
        if len(products) > 500:
            raise DomainError("This release supports stock counts of at most 500 active products.")
        if connection.execute("SELECT 1 FROM stock_counts WHERE warehouse_id=? AND status='draft'", (warehouse_id,)).fetchone():
            raise conflict("This warehouse already has a draft count. Complete or cancel it first.")
        cursor = connection.execute("INSERT INTO stock_counts(warehouse_id,note,created_by) VALUES (?,?,?)", (warehouse_id, note, actor))
        count_id = cursor.lastrowid
        for product in products:
            balance = get_balance(connection, product["id"], warehouse_id)
            connection.execute(
                "INSERT INTO count_lines(count_id,product_id,expected_quantity,expected_version) VALUES (?,?,?,?)",
                (count_id, product["id"], balance["quantity"], balance["version"]),
            )
        audit(connection, actor, "create", "stock_count", count_id)
        return get_count(connection, count_id)


def save_lines(connection: sqlite3.Connection, actor: int, count_id: int, *, lines: list[dict]) -> dict:
    lines = normalize_lines(lines, minimum=0)
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        count = row(connection, "stock_counts", count_id)
        if count["status"] != "draft":
            raise conflict("Only draft counts can be edited.")
        for incoming in lines:
            line = row(connection, "count_lines", incoming["line_id"])
            if line["count_id"] != count_id:
                raise DomainError("A line does not belong to this stock count.")
            connection.execute("UPDATE count_lines SET counted_quantity=? WHERE id=?", (incoming["quantity"], line["id"]))
        audit(connection, actor, "save_lines", "stock_count", count_id, {"lines": lines})
        return get_count(connection, count_id)


def post_count(connection: sqlite3.Connection, actor: int, count_id: int, *, key: str) -> dict:
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        count = get_count(connection, count_id)
        operation = begin_operation(connection, actor, "count_post", key, {"count_id": count_id}, count["note"])
        if operation["replayed"]:
            return operation
        if count["status"] != "draft":
            raise conflict("Only draft counts can be posted.")
        for line in count["lines"]:
            if line["counted_quantity"] is None:
                raise DomainError("Enter a counted quantity for every line before posting.")
            row(connection, "products", line["product_id"], active=True)
            balance = get_balance(connection, line["product_id"], count["warehouse_id"])
            if balance["version"] != line["expected_version"]:
                raise conflict("Stock changed after this count began. Cancel the count and start a fresh one.")
            delta = line["counted_quantity"] - line["expected_quantity"]
            if delta:
                apply_delta(connection, operation["id"], line["product_id"], count["warehouse_id"], delta)
        connection.execute("UPDATE stock_counts SET status='posted',operation_id=? WHERE id=?", (operation["id"], count_id))
        audit(connection, actor, "post", "stock_count", count_id, {"operation_id": operation["id"]})
        return operation


def cancel_count(connection: sqlite3.Connection, actor: int, count_id: int) -> dict:
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        count = row(connection, "stock_counts", count_id)
        if count["status"] == "posted":
            raise conflict("A posted count cannot be cancelled. Start a new count to make a correction.")
        if count["status"] != "cancelled":
            connection.execute("UPDATE stock_counts SET status='cancelled' WHERE id=?", (count_id,))
            audit(connection, actor, "cancel", "stock_count", count_id)
        return get_count(connection, count_id)
