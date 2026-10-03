import csv
import io
import secrets

import pytest

from conftest import load
from test_inventory import movement, quantity


def purchase(db, owner, world):
    service = load("stockdesk.services.purchasing")
    order = service.create_order(db, owner["id"], supplier_id=world["supplier"]["id"],
                                 warehouse_id=world["main"]["id"], note="Synthetic restock")
    first = service.add_line(db, owner["id"], order["id"], product_id=world["product"]["id"],
                            quantity=10, unit_cost_minor=4500)
    second = service.add_line(db, owner["id"], order["id"], product_id=world["second"]["id"],
                             quantity=20, unit_cost_minor=800)
    return service, order, first, second


def test_purchase_order_partial_then_complete(db, owner, world):
    service, order, first, second = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    service.receive_order(db, owner["id"], order["id"],
                          lines=[{"line_id": first["id"], "quantity": 4}], key=secrets.token_urlsafe(18))
    assert service.get_order(db, order["id"])["status"] == "partial"
    assert quantity(db, world) == 4
    service.receive_order(db, owner["id"], order["id"], lines=[
        {"line_id": first["id"], "quantity": 6}, {"line_id": second["id"], "quantity": 20}],
        key=secrets.token_urlsafe(18))
    assert service.get_order(db, order["id"])["status"] == "received"
    assert quantity(db, world) == 10
    assert quantity(db, world, product="second") == 20


def test_invalid_second_receipt_line_rolls_back_first(db, owner, world):
    service, order, first, second = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    with pytest.raises(ValueError, match="remaining"):
        service.receive_order(db, owner["id"], order["id"], lines=[
            {"line_id": first["id"], "quantity": 5}, {"line_id": second["id"], "quantity": 21}],
            key=secrets.token_urlsafe(18))
    assert quantity(db, world) == 0
    assert service.get_order(db, order["id"])["lines"][0]["received"] == 0
    assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_receiving_draft_is_forbidden(db, owner, world):
    service, order, first, _ = purchase(db, owner, world)
    with pytest.raises(ValueError):
        service.receive_order(db, owner["id"], order["id"],
                              lines=[{"line_id": first["id"], "quantity": 1}], key=secrets.token_urlsafe(18))


def test_completed_receipt_replay_still_succeeds(db, owner, world):
    service, order, first, second = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    key = secrets.token_urlsafe(18)
    lines = [{"line_id": first["id"], "quantity": 10}, {"line_id": second["id"], "quantity": 20}]
    one = service.receive_order(db, owner["id"], order["id"], lines=lines, key=key)
    two = service.receive_order(db, owner["id"], order["id"], lines=list(reversed(lines)), key=key)
    assert one["id"] == two["id"]
    assert two["replayed"] is True
    assert quantity(db, world) == 10


def test_duplicate_product_line_rejected(db, owner, world):
    service, order, _, _ = purchase(db, owner, world)
    with pytest.raises(ValueError) as error:
        service.add_line(db, owner["id"], order["id"], product_id=world["product"]["id"],
                         quantity=1, unit_cost_minor=1)
    assert error.value.status == 409


def test_cannot_edit_submitted_order(db, owner, world):
    service, order, first, _ = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    with pytest.raises(ValueError):
        service.remove_line(db, owner["id"], order["id"], first["id"])


def test_empty_order_cannot_be_submitted(db, owner, world):
    service, order, first, second = purchase(db, owner, world)
    service.remove_line(db, owner["id"], order["id"], first["id"])
    service.remove_line(db, owner["id"], order["id"], second["id"])
    with pytest.raises(ValueError, match="line"):
        service.submit_order(db, owner["id"], order["id"])


def test_cancellation_before_receipt_and_not_after(db, owner, world):
    service, order, first, _ = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    service.receive_order(db, owner["id"], order["id"],
                          lines=[{"line_id": first["id"], "quantity": 1}], key=secrets.token_urlsafe(18))
    with pytest.raises(ValueError):
        service.cancel_order(db, owner["id"], order["id"])
    other = service.create_order(db, owner["id"], supplier_id=world["supplier"]["id"],
                                 warehouse_id=world["main"]["id"])
    assert service.cancel_order(db, owner["id"], other["id"])["status"] == "cancelled"


@pytest.mark.parametrize("lines", [[], [{"line_id": 1, "quantity": 0}],
                                   [{"line_id": 1, "quantity": 1}, {"line_id": 1, "quantity": 2}],
                                   [{"line_id": 999, "quantity": 1}],
                                   [{"line_id": 1, "quantity": 1, "extra": True}]])
def test_invalid_receipt_lines(db, owner, world, lines):
    service, order, _, _ = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    with pytest.raises(ValueError):
        service.receive_order(db, owner["id"], order["id"], lines=lines, key=secrets.token_urlsafe(18))
    assert quantity(db, world) == 0


def test_foreign_order_line_rejected(db, owner, world):
    service, order, _, _ = purchase(db, owner, world)
    _, other, line, _ = purchase(db, owner, world)
    service.submit_order(db, owner["id"], order["id"])
    with pytest.raises(ValueError):
        service.receive_order(db, owner["id"], order["id"],
                              lines=[{"line_id": line["id"], "quantity": 1}], key=secrets.token_urlsafe(18))
    assert service.get_order(db, other["id"])["status"] == "draft"


def start_count(db, owner, world):
    return load("stockdesk.services.counts").create_count(
        db, owner["id"], warehouse_id=world["main"]["id"], note="Synthetic cycle count")


def count_values(count, first=22, second=0):
    return [{"line_id": item["id"], "quantity": first if item["sku"] == "BOLT-M8" else second}
            for item in count["lines"]]


def test_count_includes_empty_balances_and_posts_adjustment(db, owner, world):
    counts = load("stockdesk.services.counts")
    movement(db, owner, world)
    count = start_count(db, owner, world)
    assert len(count["lines"]) == 2
    counts.save_lines(db, owner["id"], count["id"], lines=count_values(count))
    result = counts.post_count(db, owner["id"], count["id"], key=secrets.token_urlsafe(18))
    assert result["id"] > 0
    assert quantity(db, world) == 22
    assert counts.get_count(db, count["id"])["status"] == "posted"
    adjustment = db.execute("SELECT delta FROM movements WHERE operation_id=?", (result["id"],)).fetchone()[0]
    assert adjustment == -3


def test_count_requires_every_line(db, owner, world):
    counts = load("stockdesk.services.counts")
    count = start_count(db, owner, world)
    counts.save_lines(db, owner["id"], count["id"], lines=count_values(count)[:1])
    with pytest.raises(ValueError, match="every"):
        counts.post_count(db, owner["id"], count["id"], key=secrets.token_urlsafe(18))


def test_stale_count_rejected_even_when_balance_returns_to_same_value(db, owner, world):
    counts = load("stockdesk.services.counts")
    movement(db, owner, world)
    count = start_count(db, owner, world)
    counts.save_lines(db, owner["id"], count["id"], lines=count_values(count))
    movement(db, owner, world, "issue", 1)
    movement(db, owner, world, "receive", 1)
    assert quantity(db, world) == 25
    with pytest.raises(ValueError, match="changed"):
        counts.post_count(db, owner["id"], count["id"], key=secrets.token_urlsafe(18))
    assert quantity(db, world) == 25
    assert counts.get_count(db, count["id"])["status"] == "draft"


def test_only_one_draft_count_per_warehouse(db, owner, world):
    counts = load("stockdesk.services.counts")
    first = start_count(db, owner, world)
    with pytest.raises(ValueError):
        start_count(db, owner, world)
    counts.cancel_count(db, owner["id"], first["id"])
    assert start_count(db, owner, world)["id"] != first["id"]


def test_count_replay_has_no_second_adjustment(db, owner, world):
    counts = load("stockdesk.services.counts")
    count = start_count(db, owner, world)
    counts.save_lines(db, owner["id"], count["id"], lines=count_values(count))
    key = secrets.token_urlsafe(18)
    one = counts.post_count(db, owner["id"], count["id"], key=key)
    two = counts.post_count(db, owner["id"], count["id"], key=key)
    assert one["id"] == two["id"]
    assert quantity(db, world) == 22


def test_zero_difference_count_has_no_zero_delta_entries(db, owner, world):
    counts = load("stockdesk.services.counts")
    count = start_count(db, owner, world)
    counts.save_lines(db, owner["id"], count["id"], lines=count_values(count, first=0))
    counts.post_count(db, owner["id"], count["id"], key=secrets.token_urlsafe(18))
    assert db.execute("SELECT COUNT(*) FROM movements").fetchone()[0] == 0


def test_viewer_cannot_create_orders_or_counts(db, viewer, world):
    with pytest.raises(ValueError) as error:
        start_count(db, viewer, world)
    assert error.value.status == 403
    with pytest.raises(ValueError) as error:
        load("stockdesk.services.purchasing").create_order(db, viewer["id"],
            supplier_id=world["supplier"]["id"], warehouse_id=world["main"]["id"])
    assert error.value.status == 403


def test_reconciliation_detects_balance_tampering(db, owner, world):
    reports = load("stockdesk.services.reports")
    movement(db, owner, world)
    assert reports.reconcile(db)["ok"] is True
    db.execute("UPDATE balances SET quantity=12")
    result = reports.reconcile(db)
    assert result["ok"] is False
    assert result["issues"][0]["ledger_quantity"] == 25
    assert result["issues"][0]["stored_quantity"] == 12


def test_inventory_search_pagination_and_low_stock(db, owner, world):
    reports = load("stockdesk.services.reports")
    movement(db, owner, world)
    result = reports.inventory(db, warehouse_id=world["main"]["id"], page_size=1)
    assert result["total"] == 2
    assert len(result["items"]) == 1
    low = reports.inventory(db, warehouse_id=world["main"]["id"], low_only=True)
    assert [r["sku"] for r in low["items"]] == ["WASH-M8"]
    assert reports.inventory(db, search="%' OR 1=1 --")["total"] == 0
    assert reports.inventory(db, search="%") ["total"] == 0


def test_inventory_value_uses_integer_minor_units(db, owner, world):
    movement(db, owner, world, quantity=3)
    stats = load("stockdesk.services.reports").dashboard(db)
    assert stats["value_minor"] == 13500
    assert type(stats["value_minor"]) is int


@pytest.mark.parametrize("value", ["=2+3", "+SUM(A1)", "-1+2", "@SUM(A1)", "  =1", "\t=1", "\r=1"])
def test_csv_neutralizes_untrusted_formulas(value):
    reports = load("stockdesk.services.reports")
    output = reports.csv_text([{"name": value}], ["name"])
    parsed = list(csv.reader(io.StringIO(output)))
    assert parsed[1][0].startswith("'")


def test_csv_preserves_numeric_negative_values_and_quotes_text():
    reports = load("stockdesk.services.reports")
    output = reports.csv_text([{"name": 'Bolt, "M8"', "delta": -3}], ["name", "delta"])
    assert list(csv.reader(io.StringIO(output)))[1] == ['Bolt, "M8"', "-3"]
