"""JSON API. Authentication and CSRF are shared dependencies, not route conventions."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from .db import read_snapshot
from .errors import DomainError
from .security import DB, User, Writer, check_csrf, csrf_token, current_user, establish_session
from .services import auth, catalog, counts, inventory, purchasing, reports
from .services.common import row
from .validation import fields

router = APIRouter()


@router.get("/healthz", tags=["operations"])
def health(db: DB):
    return {"status": "ok", "schema_version": 1}


@router.get("/api/session", tags=["session"])
def session_info(request: Request, db: DB):
    user = None
    if request.session.get("user_id") is not None:
        try:
            user = current_user(request, db)
        except DomainError:
            user = None
    return {"csrf_token": csrf_token(request), "user": user}


@router.post("/api/login", tags=["session"])
def login(request: Request, data: dict, db: DB):
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    fields(data, {"username", "password"})
    user = auth.login(db, data.get("username"), data.get("password"))
    establish_session(request, user)
    return {"user": user, "csrf_token": csrf_token(request)}


@router.post("/api/logout", tags=["session"])
def logout(request: Request, db: DB, user: Writer):
    auth.revoke_sessions(db, user["id"])
    request.session.clear()
    return {"logged_out": True}


@router.get("/api/schema", tags=["operations"])
def schema(request: Request, user: User):
    return request.app.openapi()


@router.get("/api/products", tags=["catalog"])
def products(db: DB, user: User, search: str = "", page: int = 1, page_size: int = 50):
    return reports.products(db, search=search, page=page, page_size=page_size)


@router.post("/api/products", status_code=201, tags=["catalog"])
def add_product(data: dict, db: DB, user: Writer):
    fields(data, {"sku", "name", "unit", "reorder_level", "unit_cost_minor"})
    return catalog.add_product(db, user["id"], sku=data.get("sku"), name=data.get("name"),
        unit=data.get("unit", "each"), reorder_level=data.get("reorder_level", 0), unit_cost_minor=data.get("unit_cost_minor", 0))


@router.get("/api/products/{product_id}", tags=["catalog"])
def product(product_id: int, db: DB, user: User):
    return row(db, "products", product_id)


@router.put("/api/products/{product_id}", tags=["catalog"])
def edit_product(product_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"expected_version", "name", "unit", "reorder_level", "unit_cost_minor"})
    return catalog.update_product(db, user["id"], product_id,
        expected_version=data.get("expected_version"), name=data.get("name"), unit=data.get("unit"),
        reorder_level=data.get("reorder_level"), unit_cost_minor=data.get("unit_cost_minor"))


@router.post("/api/products/{product_id}/active", tags=["catalog"])
def product_active(product_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"active"})
    return catalog.set_product_active(db, user["id"], product_id, data.get("active"))


@router.get("/api/warehouses", tags=["catalog"])
def warehouses(db: DB, user: User):
    return reports.named_records(db, "warehouses")


@router.post("/api/warehouses", status_code=201, tags=["catalog"])
def add_warehouse(data: dict, db: DB, user: Writer):
    fields(data, {"code", "name"})
    return catalog.add_warehouse(db, user["id"], code=data.get("code"), name=data.get("name"))


@router.get("/api/suppliers", tags=["catalog"])
def suppliers(db: DB, user: User):
    return reports.named_records(db, "suppliers")


@router.post("/api/suppliers", status_code=201, tags=["catalog"])
def add_supplier(data: dict, db: DB, user: Writer):
    fields(data, {"code", "name"})
    return catalog.add_supplier(db, user["id"], code=data.get("code"), name=data.get("name"))


@router.get("/api/inventory", tags=["inventory"])
def stock(db: DB, user: User, warehouse_id: int | None = None, search: str = "",
          page: int = 1, page_size: int = 50, low_only: bool = False):
    return reports.inventory(db, warehouse_id=warehouse_id, search=search, page=page, page_size=page_size, low_only=low_only)


@router.get("/api/dashboard", tags=["reports"])
def dashboard(db: DB, user: User):
    return reports.dashboard(db)


@router.get("/api/movements", tags=["inventory"])
def movements(db: DB, user: User, product_id: int | None = None, page: int = 1, page_size: int = 50):
    return reports.movements(db, product_id=product_id, page=page, page_size=page_size)


@router.post("/api/movements", tags=["inventory"])
def post_movement(request: Request, data: dict, db: DB, user: Writer):
    fields(data, {"kind", "product_id", "warehouse_id", "quantity", "destination_id", "note"})
    return inventory.move(db, user["id"], kind=data.get("kind"), product_id=data.get("product_id"),
        warehouse_id=data.get("warehouse_id"), quantity=data.get("quantity"),
        destination_id=data.get("destination_id"), note=data.get("note", ""), key=request.headers.get("Idempotency-Key"))


@router.get("/api/orders", tags=["purchasing"])
def orders(db: DB, user: User, page: int = 1, page_size: int = 50):
    return reports.orders(db, page=page, page_size=page_size)


@router.post("/api/orders", status_code=201, tags=["purchasing"])
def create_order(data: dict, db: DB, user: Writer):
    fields(data, {"supplier_id", "warehouse_id", "note"})
    return purchasing.create_order(db, user["id"], supplier_id=data.get("supplier_id"),
        warehouse_id=data.get("warehouse_id"), note=data.get("note", ""))


@router.get("/api/orders/{order_id}", tags=["purchasing"])
def order(order_id: int, db: DB, user: User):
    return purchasing.get_order(db, order_id)


@router.post("/api/orders/{order_id}/lines", status_code=201, tags=["purchasing"])
def add_order_line(order_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"product_id", "quantity", "unit_cost_minor"})
    return purchasing.add_line(db, user["id"], order_id, product_id=data.get("product_id"),
        quantity=data.get("quantity"), unit_cost_minor=data.get("unit_cost_minor"))


@router.delete("/api/orders/{order_id}/lines/{line_id}", status_code=204, tags=["purchasing"])
def delete_order_line(order_id: int, line_id: int, db: DB, user: Writer):
    purchasing.remove_line(db, user["id"], order_id, line_id)
    return Response(status_code=204)


@router.post("/api/orders/{order_id}/submit", tags=["purchasing"])
def submit_order(order_id: int, db: DB, user: Writer):
    return purchasing.submit_order(db, user["id"], order_id)


@router.post("/api/orders/{order_id}/cancel", tags=["purchasing"])
def cancel_order(order_id: int, db: DB, user: Writer):
    return purchasing.cancel_order(db, user["id"], order_id)


@router.post("/api/orders/{order_id}/receive", tags=["purchasing"])
def receive_order(request: Request, order_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"lines", "note"})
    return purchasing.receive_order(db, user["id"], order_id, lines=data.get("lines"),
        note=data.get("note", ""), key=request.headers.get("Idempotency-Key"))


@router.get("/api/counts", tags=["stock counts"])
def stock_counts(db: DB, user: User, page: int = 1, page_size: int = 50):
    return reports.counts(db, page=page, page_size=page_size)


@router.post("/api/counts", status_code=201, tags=["stock counts"])
def create_count(data: dict, db: DB, user: Writer):
    fields(data, {"warehouse_id", "note"})
    return counts.create_count(db, user["id"], warehouse_id=data.get("warehouse_id"), note=data.get("note", ""))


@router.get("/api/counts/{count_id}", tags=["stock counts"])
def stock_count(count_id: int, db: DB, user: User):
    return counts.get_count(db, count_id)


@router.put("/api/counts/{count_id}/lines", tags=["stock counts"])
def save_count(count_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"lines"})
    return counts.save_lines(db, user["id"], count_id, lines=data.get("lines"))


@router.post("/api/counts/{count_id}/post", tags=["stock counts"])
def post_count(request: Request, count_id: int, db: DB, user: Writer):
    return counts.post_count(db, user["id"], count_id, key=request.headers.get("Idempotency-Key"))


@router.post("/api/counts/{count_id}/cancel", tags=["stock counts"])
def cancel_count(count_id: int, db: DB, user: Writer):
    return counts.cancel_count(db, user["id"], count_id)


@router.get("/api/users", tags=["accounts"])
def users(db: DB, user: User):
    auth.require_role(db, user["id"], "admin")
    return [auth.public_user(item) for item in db.execute("SELECT * FROM users ORDER BY username")]


@router.post("/api/users", status_code=201, tags=["accounts"])
def add_user(data: dict, db: DB, user: Writer):
    fields(data, {"username", "password", "role"})
    return auth.create_user(db, user["id"], data.get("username"), data.get("password"), data.get("role", "viewer"))


@router.put("/api/users/{user_id}/access", tags=["accounts"])
def update_access(user_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"role", "enabled"})
    return auth.set_user_access(db, user["id"], user_id, data.get("role"), data.get("enabled"))


@router.put("/api/users/{user_id}/password", tags=["accounts"])
def reset_password(user_id: int, data: dict, db: DB, user: Writer):
    fields(data, {"password"})
    return auth.set_password(db, user["id"], user_id, data.get("password"))


@router.get("/api/audit", tags=["reports"])
def audit_log(db: DB, user: User, page: int = 1, page_size: int = 50):
    auth.require_role(db, user["id"], "admin")
    return reports.audit_events(db, page=page, page_size=page_size)


@router.get("/api/reconcile", tags=["reports"])
def reconcile(db: DB, user: User):
    auth.require_role(db, user["id"], "admin")
    return reports.reconcile(db)


@router.get("/api/exports/inventory.csv", tags=["reports"])
def inventory_export(db: DB, user: User, warehouse_id: int | None = None, search: str = ""):
    with read_snapshot(db):
        first = reports.inventory(db, warehouse_id=warehouse_id, search=search, page_size=200)
        if first["total"] > 10_000:
            raise DomainError("Export exceeds 10,000 rows. Narrow the warehouse or search filter.")
        items = first["items"]
        for page in range(2, first["pages"]+1):
            items.extend(reports.inventory(db, warehouse_id=warehouse_id, search=search, page=page, page_size=200)["items"])
    columns = ["sku", "name", "warehouse_code", "unit", "quantity", "reorder_level", "unit_cost_minor", "value_minor"]
    return Response(reports.csv_text(items, columns), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="stockdesk-inventory.csv"'})
