"""Synthetic demonstration data. Never invoked automatically on application startup."""

import secrets
import sqlite3

from .db import atomic
from .errors import conflict
from .services import catalog, counts, inventory, purchasing
from .services.auth import require_role

PRODUCTS = (
    ("BLT-M8-30", "M8 × 30 mm hex bolt", "each", 50, 3500, 210),
    ("WSH-M8", "M8 flat washer", "each", 100, 800, 360),
    ("NUT-M8", "M8 hex nut", "each", 75, 1200, 190),
    ("TAPE-48", "48 mm packing tape", "roll", 15, 24500, 12),
    ("GLOVE-M", "General-purpose work gloves, medium", "pair", 20, 18000, 17),
    ("GLOVE-L", "General-purpose work gloves, large", "pair", 20, 18000, 42),
    ("CABLE-200", "200 mm cable ties", "pack", 10, 32500, 28),
    ("MARK-BLK", "Black permanent marker", "each", 12, 9500, 9),
    ("BOX-S", "Small shipping carton", "each", 25, 8500, 70),
    ("BOX-M", "Medium shipping carton", "each", 25, 13500, 90),
    ("FILM-500", "500 mm stretch film", "roll", 8, 115000, 6),
    ("LABEL-100", "100 mm shipping labels", "roll", 10, 65000, 24),
)


def seed_demo(connection: sqlite3.Connection, actor: int) -> dict:
    note = "Synthetic demo data — not a real delivery or business record."
    with atomic(connection):
        require_role(connection, actor, "admin")
        for table in ("products", "warehouses", "suppliers", "purchase_orders", "stock_counts"):
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise conflict("Demo setup requires empty product, warehouse, supplier, order and count catalogs.")
        main = catalog.add_warehouse(connection, actor, code="MAIN", name="Main storeroom · demo")
        annex = catalog.add_warehouse(connection, actor, code="ANNEX", name="Dispatch annex · demo")
        suppliers = [catalog.add_supplier(connection, actor, code=code, name=name) for code, name in (
            ("DEMO-HW", "Sample Hardware Supply · fictional"),
            ("DEMO-PK", "Sample Packaging Supply · fictional"),
            ("DEMO-WS", "Sample Workshop Supply · fictional"),
        )]
        products = []
        for sku, name, unit, reorder, cost, quantity in PRODUCTS:
            product = catalog.add_product(connection, actor, sku=sku, name=name, unit=unit,
                                          reorder_level=reorder, unit_cost_minor=cost)
            products.append(product)
            inventory.move(connection, actor, kind="receive", product_id=product["id"],
                           warehouse_id=main["id"], quantity=quantity, note=note, key=secrets.token_urlsafe(18))
        for index, amount in ((0, 60), (1, 100), (2, 50), (5, 15), (8, 20)):
            inventory.move(connection, actor, kind="transfer", product_id=products[index]["id"],
                           warehouse_id=main["id"], destination_id=annex["id"], quantity=amount,
                           note=note, key=secrets.token_urlsafe(18))
        order = purchasing.create_order(connection, actor, supplier_id=suppliers[1]["id"], warehouse_id=main["id"], note=note)
        first_line = None
        for index, amount in ((3, 40), (10, 20), (11, 30)):
            line = purchasing.add_line(connection, actor, order["id"], product_id=products[index]["id"],
                                        quantity=amount, unit_cost_minor=products[index]["unit_cost_minor"])
            first_line = first_line or line
        purchasing.submit_order(connection, actor, order["id"])
        purchasing.receive_order(connection, actor, order["id"],
            lines=[{"line_id": first_line["id"], "quantity": 10}], note=note, key=secrets.token_urlsafe(18))
        count = counts.create_count(connection, actor, warehouse_id=annex["id"], note="Synthetic demo cycle count.")
        counts.save_lines(connection, actor, count["id"], lines=[
            {"line_id": line["id"], "quantity": line["expected_quantity"]} for line in count["lines"][:3]])
        return {"products": len(products), "warehouses": 2, "suppliers": len(suppliers),
                "purchase_order_id": order["id"], "draft_count_id": count["id"]}
