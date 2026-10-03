"""Append-only stock ledger, bounded balances and atomic idempotent movements."""

import hashlib
import json
import sqlite3

from ..db import atomic
from ..errors import DomainError, conflict
from ..validation import MAX_QUANTITY, choice, identifier, idempotency_key, integer, text
from .auth import require_role
from .common import audit, row


def get_balance(connection: sqlite3.Connection, product_id: int, warehouse_id: int) -> dict:
    identifier(product_id, "Product ID")
    identifier(warehouse_id, "Warehouse ID")
    result = connection.execute(
        "SELECT quantity,version FROM balances WHERE product_id=? AND warehouse_id=?", (product_id, warehouse_id)
    ).fetchone()
    return dict(result) if result else {"quantity": 0, "version": 0}


def begin_operation(connection: sqlite3.Connection, actor: int, kind: str, key: str,
                    payload: dict, note: str = "") -> dict:
    """Internal: caller holds an immediate transaction and has checked authorization."""
    if not connection.in_transaction:
        raise RuntimeError("Posting requires an open transaction")
    key = idempotency_key(key)
    digest = hashlib.sha256(json.dumps({"kind": kind, "payload": payload},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    existing = connection.execute(
        "SELECT id,payload_hash FROM operations WHERE actor_id=? AND request_key=?", (actor, key)
    ).fetchone()
    if existing:
        if existing["payload_hash"] != digest:
            raise conflict("This idempotency key was already used for a different request.")
        return {"id": existing["id"], "replayed": True}
    cursor = connection.execute(
        "INSERT INTO operations(actor_id,kind,request_key,payload_hash,note) VALUES (?,?,?,?,?)",
        (actor, kind, key, digest, note),
    )
    return {"id": cursor.lastrowid, "replayed": False}


def apply_delta(connection: sqlite3.Connection, operation_id: int, product_id: int,
                warehouse_id: int, delta: int) -> None:
    """Internal posting primitive; database constraints provide a second boundary."""
    if not connection.in_transaction:
        raise RuntimeError("Posting requires an open transaction")
    row(connection, "products", product_id, active=True)
    row(connection, "warehouses", warehouse_id, active=True)
    if type(delta) is not int or delta == 0:
        raise DomainError("A ledger delta must be a nonzero integer.")
    current = get_balance(connection, product_id, warehouse_id)
    new_quantity = current["quantity"] + delta
    if new_quantity < 0:
        raise conflict(f"Insufficient stock: {current['quantity']} units are available.")
    if new_quantity > MAX_QUANTITY:
        raise conflict("This movement would exceed the maximum balance.")
    connection.execute(
        "INSERT INTO balances(product_id,warehouse_id,quantity,version) VALUES (?,?,?,1) "
        "ON CONFLICT(product_id,warehouse_id) DO UPDATE SET quantity=excluded.quantity,version=balances.version+1",
        (product_id, warehouse_id, new_quantity),
    )
    connection.execute(
        "INSERT INTO movements(operation_id,product_id,warehouse_id,delta,balance_after) VALUES (?,?,?,?,?)",
        (operation_id, product_id, warehouse_id, delta, new_quantity),
    )


def move(connection: sqlite3.Connection, actor: int, *, kind: str, product_id: int,
         warehouse_id: int, quantity: int, key: str, note: str = "",
         destination_id: int | None = None) -> dict:
    kind = choice(kind, "Movement kind", ("receive", "issue", "transfer"))
    identifier(product_id, "Product ID")
    identifier(warehouse_id, "Warehouse ID")
    quantity = integer(quantity, "Quantity", minimum=1)
    note = text(note, "Note", maximum=500, empty=True)
    if kind == "transfer":
        identifier(destination_id, "Destination ID")
        if destination_id == warehouse_id:
            raise DomainError("The source and destination must be different warehouses.")
    elif destination_id is not None:
        raise DomainError("A destination is only valid for a transfer.")
    payload = dict(product_id=product_id, warehouse_id=warehouse_id, quantity=quantity,
                   destination_id=destination_id, note=note)
    with atomic(connection):
        require_role(connection, actor, "admin", "operator")
        operation = begin_operation(connection, actor, kind, key, payload, note)
        if operation["replayed"]:
            return operation
        apply_delta(connection, operation["id"], product_id, warehouse_id,
                    quantity if kind == "receive" else -quantity)
        if kind == "transfer":
            apply_delta(connection, operation["id"], product_id, destination_id, quantity)
        audit(connection, actor, kind, "operation", operation["id"], payload)
        return operation
