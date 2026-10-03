import concurrent.futures
import secrets
import sqlite3
import threading

import pytest

from conftest import load


def movement(db, actor, world, kind="receive", quantity=25, **kwargs):
    args = dict(kind=kind, product_id=world["product"]["id"],
                warehouse_id=world["main"]["id"], quantity=quantity,
                key=secrets.token_urlsafe(18))
    args.update(kwargs)
    return load("stockdesk.services.inventory").move(db, actor["id"], **args)


def quantity(db, world, place="main", product="product"):
    return load("stockdesk.services.inventory").get_balance(
        db, world[product]["id"], world[place]["id"]
    )["quantity"]


def test_receive_and_issue_update_balances_and_ledger(db, owner, world):
    movement(db, owner, world)
    movement(db, owner, world, "issue", 9)
    assert quantity(db, world) == 16
    rows = db.execute("SELECT delta,balance_after FROM movements ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [(25, 25), (-9, 16)]


def test_issue_full_balance_leaves_zero(db, owner, world):
    movement(db, owner, world)
    movement(db, owner, world, "issue", 25)
    assert quantity(db, world) == 0


def test_insufficient_stock_does_not_leave_operation(db, owner, world):
    before = db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(ValueError, match="Insufficient"):
        movement(db, owner, world, "issue", 1)
    assert quantity(db, world) == 0
    assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before


def test_transfer_is_two_balanced_legs(db, owner, world):
    movement(db, owner, world)
    result = movement(db, owner, world, "transfer", 10, destination_id=world["annex"]["id"])
    assert quantity(db, world) == 15
    assert quantity(db, world, "annex") == 10
    rows = db.execute("SELECT delta FROM movements WHERE operation_id=?", (result["id"],)).fetchall()
    assert sorted(row[0] for row in rows) == [-10, 10]


def test_transfer_destination_overflow_rolls_back_source(db, owner, world):
    movement(db, owner, world)
    movement(db, owner, world, quantity=1_000_000_000, warehouse_id=world["annex"]["id"])
    before = db.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
    with pytest.raises(ValueError):
        movement(db, owner, world, "transfer", 1, destination_id=world["annex"]["id"])
    assert quantity(db, world) == 25
    assert quantity(db, world, "annex") == 1_000_000_000
    assert db.execute("SELECT COUNT(*) FROM movements").fetchone()[0] == before


def test_same_location_transfer_rejected(db, owner, world):
    movement(db, owner, world)
    with pytest.raises(ValueError):
        movement(db, owner, world, "transfer", 1, destination_id=world["main"]["id"])


def test_duplicate_key_is_a_replay_not_a_second_receipt(db, owner, world):
    key = secrets.token_urlsafe(18)
    first = movement(db, owner, world, key=key)
    second = movement(db, owner, world, key=key)
    assert first["id"] == second["id"]
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert quantity(db, world) == 25
    assert db.execute("SELECT COUNT(*) FROM movements").fetchone()[0] == 1


def test_key_reuse_with_changed_payload_rejected(db, owner, world):
    key = secrets.token_urlsafe(18)
    movement(db, owner, world, key=key)
    with pytest.raises(ValueError, match="different"):
        movement(db, owner, world, quantity=24, key=key)
    assert quantity(db, world) == 25


def test_idempotency_keys_are_actor_scoped(db, owner, operator, world):
    key = secrets.token_urlsafe(18)
    movement(db, owner, world, key=key)
    movement(db, operator, world, key=key)
    assert quantity(db, world) == 50


def test_viewer_cannot_post(db, viewer, world):
    with pytest.raises(ValueError) as error:
        movement(db, viewer, world)
    assert error.value.status == 403


@pytest.mark.parametrize("bad", [0, -1, 1.1, True, "10", None, 10**12])
def test_invalid_movement_quantities(db, owner, world, bad):
    with pytest.raises(ValueError):
        movement(db, owner, world, quantity=bad)


@pytest.mark.parametrize("key", ["", "short", "has spaces in key", "x" * 129, None])
def test_invalid_keys_rejected(db, owner, world, key):
    with pytest.raises(ValueError):
        movement(db, owner, world, key=key)


def test_missing_product_and_location(db, owner, world):
    for override in ({"product_id": 999}, {"warehouse_id": 999}):
        with pytest.raises(ValueError) as error:
            movement(db, owner, world, **override)
        assert error.value.status == 404


def test_movement_and_operation_records_immutable(db, owner, world):
    movement(db, owner, world)
    for sql in ("DELETE FROM movements", "UPDATE movements SET delta=2",
                "DELETE FROM operations", "UPDATE operations SET note='altered'"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(sql)


def test_catalog_duplicate_sku_conflicts_case_insensitively(db, owner, world):
    with pytest.raises(ValueError) as error:
        load("stockdesk.services.catalog").add_product(db, owner["id"], sku="bolt-m8", name="Duplicate")
    assert error.value.status == 409


def test_catalog_edit_detects_stale_version(db, owner, world):
    catalog = load("stockdesk.services.catalog")
    product = world["product"]
    updated = catalog.update_product(db, owner["id"], product["id"], expected_version=1,
                                     name="Updated", unit="each", reorder_level=5, unit_cost_minor=90)
    assert updated["version"] == 2
    with pytest.raises(ValueError, match="changed"):
        catalog.update_product(db, owner["id"], product["id"], expected_version=1,
                               name="Stale", unit="each", reorder_level=5, unit_cost_minor=90)


def test_archival_requires_zero_stock_and_prevents_posting(db, owner, world):
    catalog = load("stockdesk.services.catalog")
    movement(db, owner, world)
    with pytest.raises(ValueError, match="stock"):
        catalog.set_product_active(db, owner["id"], world["product"]["id"], False)
    movement(db, owner, world, "issue", 25)
    catalog.set_product_active(db, owner["id"], world["product"]["id"], False)
    with pytest.raises(ValueError, match="inactive"):
        movement(db, owner, world)
    catalog.set_product_active(db, owner["id"], world["product"]["id"], True)
    movement(db, owner, world)
    assert quantity(db, world) == 25


def test_only_admin_can_create_catalog(db, operator):
    with pytest.raises(ValueError) as error:
        load("stockdesk.services.catalog").add_warehouse(db, operator["id"], code="X", name="X")
    assert error.value.status == 403


def test_concurrent_withdrawals_cannot_oversell(db, db_path, owner, world):
    movement(db, owner, world, quantity=10)
    barrier = threading.Barrier(2)

    def withdraw():
        conn = load("stockdesk.db").connect(db_path)
        try:
            barrier.wait(timeout=5)
            try:
                movement(conn, owner, world, "issue", 8)
                return "accepted"
            except ValueError as error:
                assert "Insufficient" in str(error)
                return "rejected"
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: withdraw(), range(2)))
    assert sorted(results) == ["accepted", "rejected"]
    assert quantity(db, world) == 2


def test_concurrent_duplicate_receipts_only_apply_once(db, db_path, owner, world):
    barrier = threading.Barrier(2)
    key = secrets.token_urlsafe(18)

    def receive():
        conn = load("stockdesk.db").connect(db_path)
        try:
            barrier.wait(timeout=5)
            return movement(conn, owner, world, key=key)
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: receive(), range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert quantity(db, world) == 25
