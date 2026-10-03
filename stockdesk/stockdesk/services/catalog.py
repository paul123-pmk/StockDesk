"""Catalog administration with stable identities and optimistic product edits."""

import sqlite3

from ..db import atomic
from ..errors import conflict
from ..validation import code as validate_code, flag, integer, text
from .auth import require_role
from .common import audit, row


def add_product(connection: sqlite3.Connection, actor: int, *, sku: str, name: str,
                unit: str = "each", reorder_level: int = 0, unit_cost_minor: int = 0) -> dict:
    values = (validate_code(sku, "SKU"), text(name, "Product name"), text(unit, "Unit", 20),
              integer(reorder_level, "Reorder level"), integer(unit_cost_minor, "Unit cost"))
    with atomic(connection):
        require_role(connection, actor, "admin")
        try:
            cursor = connection.execute(
                "INSERT INTO products(sku,name,unit,reorder_level,unit_cost_minor) VALUES (?,?,?,?,?)", values
            )
        except sqlite3.IntegrityError as exc:
            raise conflict("That SKU already exists.") from exc
        audit(connection, actor, "create", "product", cursor.lastrowid, {"sku": values[0]})
        return row(connection, "products", cursor.lastrowid)


def update_product(connection: sqlite3.Connection, actor: int, product_id: int, *,
                   expected_version: int, name: str, unit: str,
                   reorder_level: int, unit_cost_minor: int) -> dict:
    expected_version = integer(expected_version, "Version", minimum=1)
    values = (text(name, "Product name"), text(unit, "Unit", 20),
              integer(reorder_level, "Reorder level"), integer(unit_cost_minor, "Unit cost"))
    with atomic(connection):
        require_role(connection, actor, "admin")
        product = row(connection, "products", product_id)
        if product["version"] != expected_version:
            raise conflict("This product changed. Refresh it before saving your changes.")
        connection.execute(
            "UPDATE products SET name=?,unit=?,reorder_level=?,unit_cost_minor=?,version=version+1 WHERE id=?",
            (*values, product_id),
        )
        audit(connection, actor, "edit", "product", product_id, {"previous_version": expected_version})
        return row(connection, "products", product_id)


def set_product_active(connection: sqlite3.Connection, actor: int, product_id: int, active: bool) -> dict:
    flag(active, "Active")
    with atomic(connection):
        require_role(connection, actor, "admin")
        row(connection, "products", product_id)
        if not active and connection.execute(
            "SELECT 1 FROM balances WHERE product_id=? AND quantity>0 LIMIT 1", (product_id,)
        ).fetchone():
            raise conflict("Issue or transfer all stock before archiving this product.")
        connection.execute("UPDATE products SET active=?,version=version+1 WHERE id=?", (int(active), product_id))
        audit(connection, actor, "activate" if active else "archive", "product", product_id)
        return row(connection, "products", product_id)


def _add_named(connection: sqlite3.Connection, actor: int, table: str, code: str, name: str) -> dict:
    if table not in {"warehouses", "suppliers"}:
        raise RuntimeError("Unknown catalog table")
    values = (validate_code(code, "Code"), text(name, "Name"))
    with atomic(connection):
        require_role(connection, actor, "admin")
        try:
            cursor = connection.execute(f"INSERT INTO {table}(code,name) VALUES (?,?)", values)
        except sqlite3.IntegrityError as exc:
            raise conflict("That code already exists.") from exc
        audit(connection, actor, "create", table.rstrip("s"), cursor.lastrowid)
        return row(connection, table, cursor.lastrowid)


def add_warehouse(connection: sqlite3.Connection, actor: int, *, code: str, name: str) -> dict:
    return _add_named(connection, actor, "warehouses", code, name)


def add_supplier(connection: sqlite3.Connection, actor: int, *, code: str, name: str) -> dict:
    return _add_named(connection, actor, "suppliers", code, name)
