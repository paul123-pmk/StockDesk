"""Purchase order state machine and all-or-nothing receiving."""

import sqlite3
from typing import Any

from ..db import atomic
from ..errors import DomainError, conflict
from ..validation import fields, identifier, integer, text
from .auth import require_role
from .common import audit, row
from .inventory import apply_delta, begin_operation


def normalize_lines(lines: Any, *, minimum: int = 1) -> list[dict]:
    if not isinstance(lines, list) or not 1 <= len(lines) <= 500:
        raise DomainError("Provide between 1 and 500 lines.")
    result = []
    seen = set()
    for line in lines:
        fields(line, {"line_id", "quantity"})
        line_id = identifier(line.get("line_id"), "Line ID")
        if line_id in seen:
            raise DomainError("A line may appear only once in a request.")
        seen.add(line_id)
        result.append({"line_id": line_id, "quantity": integer(line.get("quantity"), "Quantity", minimum)})
    return sorted(result, key=lambda item: item["line_id"])


def get_order(connection: sqlite3.Connection, order_id: int) -> dict:
    order = row(connection, "purchase_orders", order_id)
    order["supplier_name"] = row(connection, "suppliers", order["supplier_id"])["name"]
    order["warehouse_name"] = row(connection, "warehouses", order["warehouse_id"])["name"]
    order["lines"] = [dict(item) for item in connection.execute(
        "SELECT l.*,p.sku,p.name,p.unit FROM purchase_lines l JOIN products p ON p.id=l.product_id "
        "WHERE l.order_id=? ORDER BY l.id", (order_id,))]
    order["total_minor"] = sum(item["quantity"] * item["unit_cost_minor"] for item in order["lines"])
    order["remaining_units"] = sum(item["quantity"] - item["received"] for item in order["lines"])
    return order


def create_order(connection: sqlite3.Connection, actor: int, *, supplier_id: int,
                 warehouse_id: int, note: str = "") -> dict:
    note = text(note, "Note", maximum=500, empty=True)
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        row(connection, "suppliers", supplier_id, active=True)
        row(connection, "warehouses", warehouse_id, active=True)
        cursor = connection.execute(
            "INSERT INTO purchase_orders(supplier_id,warehouse_id,note,created_by) VALUES (?,?,?,?)",
            (supplier_id, warehouse_id, note, actor),
        )
        audit(connection, actor, "create", "purchase_order", cursor.lastrowid)
        return get_order(connection, cursor.lastrowid)


def add_line(connection: sqlite3.Connection, actor: int, order_id: int, *,
             product_id: int, quantity: int, unit_cost_minor: int) -> dict:
    quantity = integer(quantity, "Ordered quantity", minimum=1)
    cost = integer(unit_cost_minor, "Unit cost")
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        order = row(connection, "purchase_orders", order_id)
        if order["status"] != "draft":
            raise conflict("Only draft orders can be edited.")
        row(connection, "products", product_id, active=True)
        if connection.execute("SELECT COUNT(*) FROM purchase_lines WHERE order_id=?", (order_id,)).fetchone()[0] >= 500:
            raise DomainError("An order may contain at most 500 lines.")
        try:
            cursor = connection.execute(
                "INSERT INTO purchase_lines(order_id,product_id,quantity,unit_cost_minor) VALUES (?,?,?,?)",
                (order_id, product_id, quantity, cost),
            )
        except sqlite3.IntegrityError as exc:
            raise conflict("This product is already on the order. Remove its line before replacing it.") from exc
        audit(connection, actor, "add_line", "purchase_order", order_id, {"line_id": cursor.lastrowid})
        return row(connection, "purchase_lines", cursor.lastrowid)


def remove_line(connection: sqlite3.Connection, actor: int, order_id: int, line_id: int) -> None:
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        order = row(connection, "purchase_orders", order_id)
        if order["status"] != "draft":
            raise conflict("Only draft orders can be edited.")
        line = row(connection, "purchase_lines", line_id)
        if line["order_id"] != order_id:
            raise DomainError("This line does not belong to this order.")
        connection.execute("DELETE FROM purchase_lines WHERE id=?", (line_id,))
        audit(connection, actor, "remove_line", "purchase_order", order_id, {"line_id": line_id})


def submit_order(connection: sqlite3.Connection, actor: int, order_id: int) -> dict:
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        order = get_order(connection, order_id)
        if order["status"] != "draft":
            raise conflict("Only draft orders can be submitted.")
        if not order["lines"]:
            raise DomainError("Add at least one line before submitting.")
        for line in order["lines"]:
            row(connection, "products", line["product_id"], active=True)
        connection.execute("UPDATE purchase_orders SET status='submitted',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (order_id,))
        audit(connection, actor, "submit", "purchase_order", order_id)
        return get_order(connection, order_id)


def cancel_order(connection: sqlite3.Connection, actor: int, order_id: int) -> dict:
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        order = row(connection, "purchase_orders", order_id)
        if order["status"] == "cancelled":
            return get_order(connection, order_id)
        if order["status"] not in ("draft", "submitted"):
            raise conflict("Orders with receipts cannot be cancelled.")
        connection.execute("UPDATE purchase_orders SET status='cancelled',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (order_id,))
        audit(connection, actor, "cancel", "purchase_order", order_id)
        return get_order(connection, order_id)


def receive_order(connection: sqlite3.Connection, actor: int, order_id: int, *,
                  lines: list[dict], key: str, note: str = "") -> dict:
    identifier(order_id, "Order ID")
    lines = normalize_lines(lines)
    note = text(note, "Note", maximum=500, empty=True)
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        operation = begin_operation(connection, actor, "po_receive", key,
                                    {"order_id": order_id, "lines": lines, "note": note}, note)
        if operation["replayed"]:
            return operation
        order = row(connection, "purchase_orders", order_id)
        if order["status"] not in ("submitted", "partial"):
            raise conflict("Only submitted or partially received orders can receive stock.")
        for incoming in lines:
            line = row(connection, "purchase_lines", incoming["line_id"])
            if line["order_id"] != order_id:
                raise DomainError("A receipt line does not belong to this order.")
            if incoming["quantity"] > line["quantity"] - line["received"]:
                raise conflict("Received quantity exceeds the remaining ordered quantity.")
            apply_delta(connection, operation["id"], line["product_id"], order["warehouse_id"], incoming["quantity"])
            connection.execute("UPDATE purchase_lines SET received=received+? WHERE id=?",
                               (incoming["quantity"], line["id"]))
        remaining = connection.execute("SELECT 1 FROM purchase_lines WHERE order_id=? AND received<quantity LIMIT 1", (order_id,)).fetchone()
        status = "partial" if remaining else "received"
        connection.execute("UPDATE purchase_orders SET status=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (status, order_id))
        audit(connection, actor, "receive", "purchase_order", order_id, {"operation_id": operation["id"], "status": status})
        return operation
