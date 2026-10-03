"""Server-rendered screens and thin form adapters over the shared services."""

import secrets

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from .db import atomic
from .errors import DomainError
from .security import DB, User, check_csrf, csrf_token, establish_session
from .services import auth, catalog, counts, inventory, purchasing, reports
from .services.common import row
from .validation import choice, form_integer

router = APIRouter(include_in_schema=False)


def render(request: Request, template: str, user: dict | None = None, *, title: str = "StockDesk", **data):
    return request.app.state.templates.TemplateResponse(request=request, name=template,
        context={"user": user, "title": title, "csrf_token": csrf_token(request),
                 "request_key": secrets.token_urlsafe(24), "currency": request.app.state.settings.currency,
                 "notice": request.session.pop("notice", None), **data})


async def checked_form(request: Request):
    form = await request.form(max_fields=1200, max_files=0, max_part_size=256*1024)
    if len(form.multi_items()) != len(form):
        raise DomainError("Duplicate form fields are not supported.")
    check_csrf(request, form.get("csrf_token"))
    return dict(form)


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", title="Sign in")


@router.post("/login")
async def login_form(request: Request, db: DB):
    form = await checked_form(request)
    user = await run_in_threadpool(auth.login, db, form.get("username"), form.get("password"))
    establish_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout_form(request: Request, db: DB, user: User):
    await checked_form(request)
    await run_in_threadpool(auth.revoke_sessions, db, user["id"])
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/")
def dashboard(request: Request, db: DB, user: User):
    return render(request, "dashboard.html", user, title="Overview", stats=reports.dashboard(db))


@router.get("/products")
def products_page(request: Request, db: DB, user: User, search: str = "", page: int = 1):
    return render(request, "products.html", user, title="Products",
                  report=reports.products(db, search=search, page=page), search=search)


@router.get("/products/{product_id}")
def product_page(request: Request, product_id: int, db: DB, user: User):
    product = row(db, "products", product_id)
    places = reports.named_records(db, "warehouses")
    for place in places:
        place.update(inventory.get_balance(db, product_id, place["id"]))
    return render(request, "product.html", user, title=product["name"], product=product, places=places,
                  report=reports.movements(db, product_id=product_id, page_size=20))


@router.get("/inventory")
def inventory_page(request: Request, db: DB, user: User, warehouse_id: str = "", search: str = "",
                   low_only: str = "", page: int = 1):
    location = form_integer(warehouse_id, "Warehouse", 1) if warehouse_id else None
    return render(request, "inventory.html", user, title="Stock on hand", search=search,
                  selected_warehouse=location, low_only=low_only == "1", warehouses=reports.named_records(db, "warehouses"),
                  report=reports.inventory(db, warehouse_id=location, search=search, page=page, low_only=low_only == "1"))


@router.get("/movements")
def movements_page(request: Request, db: DB, user: User, page: int = 1):
    return render(request, "movements.html", user, title="Stock movements", report=reports.movements(db, page=page),
                  products=reports.active_products(db), warehouses=reports.named_records(db, "warehouses"))


@router.get("/orders")
def orders_page(request: Request, db: DB, user: User, page: int = 1):
    return render(request, "orders.html", user, title="Purchase orders", report=reports.orders(db, page=page),
                  suppliers=reports.named_records(db, "suppliers"), warehouses=reports.named_records(db, "warehouses"))


@router.get("/orders/{order_id}")
def order_page(request: Request, order_id: int, db: DB, user: User):
    return render(request, "order.html", user, title=f"Purchase order #{order_id:04}",
                  order=purchasing.get_order(db, order_id), products=reports.active_products(db))


@router.get("/counts")
def counts_page(request: Request, db: DB, user: User, page: int = 1):
    return render(request, "counts.html", user, title="Stock counts", report=reports.counts(db, page=page),
                  warehouses=reports.named_records(db, "warehouses"))


@router.get("/counts/{count_id}")
def count_page(request: Request, count_id: int, db: DB, user: User):
    return render(request, "count.html", user, title=f"Stock count #{count_id:04}", count=counts.get_count(db, count_id))


@router.get("/catalog")
def catalog_page(request: Request, db: DB, user: User):
    return render(request, "catalog.html", user, title="Warehouses & suppliers",
                  warehouses=reports.named_records(db, "warehouses"), suppliers=reports.named_records(db, "suppliers"))


@router.get("/users")
def users_page(request: Request, db: DB, user: User):
    auth.require_role(db, user["id"], "admin")
    users = [auth.public_user(item) for item in db.execute("SELECT * FROM users ORDER BY username")]
    return render(request, "users.html", user, title="Access management", users=users)


@router.get("/audit")
def audit_page(request: Request, db: DB, user: User, page: int = 1):
    auth.require_role(db, user["id"], "admin")
    return render(request, "audit.html", user, title="Audit trail", report=reports.audit_events(db, page=page),
                  reconciliation=reports.reconcile(db))


@router.get("/api-guide")
def api_guide(request: Request, user: User):
    return render(request, "api_guide.html", user, title="API & operating notes")


def _line_values(form: dict, *, include_zero: bool) -> list[dict]:
    lines = []
    for key, value in form.items():
        if key.startswith("qty_") and value != "":
            amount = form_integer(value, "Counted quantity" if include_zero else "Received quantity")
            if include_zero or amount:
                lines.append({"line_id": form_integer(key[4:], "Line", 1), "quantity": amount})
    return lines


def dispatch_action(action: str, db, actor: int, form: dict) -> str:
    number = lambda name, minimum=0: form_integer(form.get(name), name.replace("_", " "), minimum)
    if action == "product-create":
        product = catalog.add_product(db, actor, sku=form.get("sku"), name=form.get("name"), unit=form.get("unit"),
            reorder_level=number("reorder_level"), unit_cost_minor=number("unit_cost_minor"))
        return f"/products/{product['id']}"
    if action == "product-edit":
        product_id = number("product_id", 1)
        catalog.update_product(db, actor, product_id, expected_version=number("expected_version", 1),
            name=form.get("name"), unit=form.get("unit"), reorder_level=number("reorder_level"), unit_cost_minor=number("unit_cost_minor"))
        return f"/products/{product_id}"
    if action == "product-active":
        product_id = number("product_id", 1)
        active = choice(form.get("active"), "Active", ("true", "false")) == "true"
        catalog.set_product_active(db, actor, product_id, active)
        return f"/products/{product_id}"
    if action in ("warehouse-create", "supplier-create"):
        service = catalog.add_warehouse if action == "warehouse-create" else catalog.add_supplier
        service(db, actor, code=form.get("code"), name=form.get("name"))
        return "/catalog"
    if action == "movement":
        kind = form.get("kind")
        inventory.move(db, actor, kind=kind, product_id=number("product_id", 1), warehouse_id=number("warehouse_id", 1),
            quantity=number("quantity", 1), destination_id=number("destination_id", 1) if kind == "transfer" else None,
            key=form.get("request_key"), note=form.get("note", ""))
        return "/movements"
    if action == "order-create":
        order = purchasing.create_order(db, actor, supplier_id=number("supplier_id", 1),
            warehouse_id=number("warehouse_id", 1), note=form.get("note", ""))
        return f"/orders/{order['id']}"
    if action.startswith("order-"):
        order_id = number("order_id", 1)
        if action == "order-line":
            purchasing.add_line(db, actor, order_id, product_id=number("product_id", 1),
                quantity=number("quantity", 1), unit_cost_minor=number("unit_cost_minor"))
        elif action == "order-remove-line":
            purchasing.remove_line(db, actor, order_id, number("line_id", 1))
        elif action == "order-submit":
            purchasing.submit_order(db, actor, order_id)
        elif action == "order-cancel":
            purchasing.cancel_order(db, actor, order_id)
        elif action == "order-receive":
            purchasing.receive_order(db, actor, order_id, lines=_line_values(form, include_zero=False),
                key=form.get("request_key"), note=form.get("note", ""))
        else:
            raise DomainError("Unknown action.", "not_found", 404)
        return f"/orders/{order_id}"
    if action == "count-create":
        count = counts.create_count(db, actor, warehouse_id=number("warehouse_id", 1), note=form.get("note", ""))
        return f"/counts/{count['id']}"
    if action == "count-save":
        count_id = number("count_id", 1)
        mode = choice(form.get("mode", "save"), "Action", ("save", "post"))
        with atomic(db):
            counts.save_lines(db, actor, count_id, lines=_line_values(form, include_zero=True))
            if mode == "post":
                counts.post_count(db, actor, count_id, key=form.get("request_key"))
        return f"/counts/{count_id}"
    if action == "count-cancel":
        count_id = number("count_id", 1)
        counts.cancel_count(db, actor, count_id)
        return f"/counts/{count_id}"
    if action == "user-create":
        auth.create_user(db, actor, form.get("username"), form.get("password"), form.get("role"))
        return "/users"
    if action == "user-access":
        enabled = choice(form.get("enabled"), "Enabled", ("true", "false")) == "true"
        auth.set_user_access(db, actor, number("user_id", 1), form.get("role"), enabled)
        return "/users"
    if action == "user-password":
        auth.set_password(db, actor, number("user_id", 1), form.get("password"))
        return "/users"
    raise DomainError("Unknown action.", "not_found", 404)


@router.post("/actions/{action}")
async def action_form(action: str, request: Request, db: DB, user: User):
    form = await checked_form(request)
    destination = await run_in_threadpool(dispatch_action, action, db, user["id"], form)
    request.session["notice"] = "Saved successfully."
    return RedirectResponse(destination, status_code=303)
