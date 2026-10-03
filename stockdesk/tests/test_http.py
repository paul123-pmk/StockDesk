import re
import secrets

import pytest
from fastapi.testclient import TestClient

from conftest import load


@pytest.fixture
def client(db_path, owner, password, world):
    Settings = load("stockdesk.config").Settings
    settings = Settings(database=db_path, secret_key=secrets.token_urlsafe(40),
                        trusted_hosts=("testserver", "localhost", "127.0.0.1"))
    app = load("stockdesk.app").create_app(settings)
    with TestClient(app) as client:
        yield client


def token(client):
    return client.get("/api/session").json()["csrf_token"]


def headers(client):
    return {"X-CSRF-Token": token(client), "Idempotency-Key": secrets.token_urlsafe(18)}


def login(client, password, username="owner"):
    result = client.post("/api/login", json={"username": username, "password": password}, headers=headers(client))
    assert result.status_code == 200, result.text
    return result


def form_token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def test_health_is_public_and_contains_no_records(client):
    result = client.get("/healthz")
    assert result.status_code == 200
    assert result.json() == {"status": "ok", "schema_version": 1}


def test_anonymous_api_denied_and_html_redirects(client):
    assert client.get("/api/products").status_code == 401
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_needs_csrf(client, password):
    result = client.post("/api/login", json={"username": "owner", "password": password})
    assert result.status_code == 403
    assert "password" not in result.json()["error"]["message"].lower()


def test_login_rotates_csrf_and_sets_secure_attributes(client, password):
    old = token(client)
    result = client.post("/api/login", json={"username": "owner", "password": password}, headers={"X-CSRF-Token": old})
    assert result.status_code == 200
    assert token(client) != old
    cookie = result.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert password not in result.text


def test_write_needs_authenticated_session_and_csrf(client, password, world):
    login(client, password)
    payload = {"kind": "receive", "product_id": world["product"]["id"],
               "warehouse_id": world["main"]["id"], "quantity": 10}
    result = client.post("/api/movements", json=payload)
    assert result.status_code == 403
    result = client.post("/api/movements", json=payload, headers=headers(client))
    assert result.status_code == 200
    report = client.get("/api/inventory?warehouse_id="+str(world["main"]["id"])).json()
    assert report["items"][0]["quantity"] == 10


def test_api_replay_and_changed_payload(client, password, world):
    login(client, password)
    h = headers(client)
    payload = {"kind": "receive", "product_id": world["product"]["id"],
               "warehouse_id": world["main"]["id"], "quantity": 10}
    one = client.post("/api/movements", json=payload, headers=h)
    two = client.post("/api/movements", json=payload, headers=h)
    assert one.json()["id"] == two.json()["id"]
    assert two.json()["replayed"] is True
    payload["quantity"] = 11
    assert client.post("/api/movements", json=payload, headers=h).status_code == 409


@pytest.mark.parametrize("path", ["/", "/products", "/products/1", "/inventory", "/movements",
                                  "/orders", "/counts", "/catalog", "/users", "/audit", "/api-guide"])
def test_all_main_pages_render(client, password, path):
    login(client, password)
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert "StockDesk" in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("path", ["/api/products", "/api/inventory", "/api/movements", "/api/orders",
                                  "/api/counts", "/api/warehouses", "/api/suppliers", "/api/users",
                                  "/api/audit", "/api/dashboard", "/api/reconcile", "/api/schema"])
def test_json_read_endpoints(client, password, path):
    login(client, password)
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert "password_hash" not in response.text


def test_html_login_and_logout(client, password):
    page = client.get("/login")
    response = client.post("/login", data={"csrf_token": form_token(page), "username": "owner", "password": password}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/")
    response = client.post("/logout", data={"csrf_token": form_token(page)}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/products").status_code == 401


def test_viewer_cannot_mutate_or_read_account_admin(client, password, viewer):
    login(client, password, "viewer")
    assert client.get("/api/products").status_code == 200
    assert client.get("/users").status_code == 403
    assert client.get("/api/users").status_code == 403
    result = client.post("/api/products", headers=headers(client), json={"sku": "NEW", "name": "New"})
    assert result.status_code == 403


def test_revoked_session_denied(client, password, db, owner):
    login(client, password)
    load("stockdesk.services.auth").set_password(db, owner["id"], owner["id"], "new-"+secrets.token_urlsafe(20))
    assert client.get("/api/products").status_code == 401


def test_security_headers_and_host_validation(client, password):
    result = client.get("/login")
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in result.headers["content-security-policy"]
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/healthz", headers={"Host": "evil.invalid"}).status_code == 400


def test_oversized_body_rejected(client):
    result = client.post("/api/login", content="x" * (256*1024+1), headers={"Content-Type": "application/json"})
    assert result.status_code == 413


def test_invalid_json_has_no_internal_details(client, password):
    login(client, password)
    result = client.post("/api/products", content="{broken", headers={**headers(client), "Content-Type": "application/json"})
    assert result.status_code == 422
    assert "Traceback" not in result.text


def test_product_name_is_html_escaped(client, password):
    login(client, password)
    result = client.post("/api/products", headers=headers(client),
                         json={"sku": "XSS-TEST", "name": '<script>alert("x")</script>'})
    assert result.status_code == 201
    page = client.get("/products")
    assert '<script>alert("x")</script>' not in page.text
    assert "&lt;script&gt;" in page.text


def test_api_rejects_extra_fields_and_float_quantity(client, password, world):
    login(client, password)
    assert client.post("/api/products", headers=headers(client), json={"sku": "X", "name": "X", "admin": True}).status_code == 422
    assert client.post("/api/movements", headers=headers(client), json={"kind": "receive", "product_id": 1,
        "warehouse_id": 1, "quantity": 1.5}).status_code == 422


def test_inventory_csv_download(client, password):
    login(client, password)
    result = client.get("/api/exports/inventory.csv")
    assert result.status_code == 200
    assert "text/csv" in result.headers["content-type"]
    assert "attachment" in result.headers["content-disposition"]
    assert "BOLT-M8" in result.text


def test_full_purchase_workflow_through_api(client, password, world):
    login(client, password)
    order = client.post("/api/orders", headers=headers(client), json={"supplier_id": world["supplier"]["id"],
        "warehouse_id": world["main"]["id"]}).json()
    root = "/api/orders/"+str(order["id"])
    line = client.post(root+"/lines", headers=headers(client), json={"product_id": world["product"]["id"],
        "quantity": 3, "unit_cost_minor": 4500}).json()
    assert client.post(root+"/submit", headers=headers(client)).status_code == 200
    assert client.post(root+"/receive", headers=headers(client), json={"lines": [{"line_id": line["id"], "quantity": 3}]}).status_code == 200
    assert client.get(root).json()["status"] == "received"
    assert client.get("/orders/"+str(order["id"])).status_code == 200


def test_full_count_workflow_through_api(client, password, world):
    login(client, password)
    count = client.post("/api/counts", headers=headers(client), json={"warehouse_id": world["main"]["id"]}).json()
    root = "/api/counts/"+str(count["id"])
    lines = [{"line_id": line["id"], "quantity": 7} for line in count["lines"]]
    assert client.put(root+"/lines", headers=headers(client), json={"lines": lines}).status_code == 200
    assert client.post(root+"/post", headers=headers(client)).status_code == 200
    assert client.get(root).json()["status"] == "posted"
    assert client.get("/counts/"+str(count["id"])).status_code == 200


def test_bad_html_csrf_and_unknown_actions(client, password):
    login(client, password)
    assert client.post("/actions/product-create", data={"name": "Bad"}).status_code == 403
    assert client.post("/actions/no-such-action", data={"csrf_token": token(client)}).status_code == 404


def test_logout_revokes_a_previously_issued_cookie(client, password):
    login(client, password)
    old_cookie = client.cookies.get("stockdesk_session")
    assert client.post("/api/logout", headers=headers(client)).status_code == 200
    client.cookies.clear()
    client.cookies.set("stockdesk_session", old_cookie)
    assert client.get("/api/products").status_code == 401


def html_post(client, action, data):
    result = client.post("/actions/"+action, data={"csrf_token": token(client), **data}, follow_redirects=False)
    assert result.status_code == 303, result.text
    assert client.get(result.headers["location"]).status_code == 200
    return result.headers["location"]


def test_html_product_catalog_and_movements(client, password, world):
    login(client, password)
    html_post(client, "warehouse-create", {"code": "THIRD", "name": "Third warehouse"})
    html_post(client, "supplier-create", {"code": "EXTRA", "name": "Another synthetic supplier"})
    location = html_post(client, "product-create", {"sku": "HTML-1", "name": "HTML product", "unit": "each",
        "reorder_level": "5", "unit_cost_minor": "1500"})
    product_id = location.split("/")[-1]
    html_post(client, "product-edit", {"product_id": product_id, "expected_version": "1", "name": "Edited product",
        "unit": "each", "reorder_level": "4", "unit_cost_minor": "1700"})
    payload = {"product_id": product_id, "warehouse_id": str(world["main"]["id"]), "quantity": "20",
               "kind": "receive", "request_key": secrets.token_urlsafe(18), "note": "Synthetic HTML receipt"}
    html_post(client, "movement", payload)
    payload.update(kind="transfer", quantity="5", destination_id=str(world["annex"]["id"]), request_key=secrets.token_urlsafe(18))
    html_post(client, "movement", payload)
    payload.update(kind="issue", quantity="15", request_key=secrets.token_urlsafe(18))
    html_post(client, "movement", payload)
    payload.update(warehouse_id=str(world["annex"]["id"]), quantity="5", request_key=secrets.token_urlsafe(18))
    html_post(client, "movement", payload)
    html_post(client, "product-active", {"product_id": product_id, "active": "false"})
    assert client.get("/api/products/"+product_id).json()["active"] == 0
    html_post(client, "product-active", {"product_id": product_id, "active": "true"})


def test_html_purchase_and_count_actions(client, password, world):
    login(client, password)
    location = html_post(client, "order-create", {"supplier_id": str(world["supplier"]["id"]),
        "warehouse_id": str(world["main"]["id"]), "note": "Synthetic form order"})
    order_id = location.split("/")[-1]
    html_post(client, "order-line", {"order_id": order_id, "product_id": str(world["product"]["id"]),
        "quantity": "4", "unit_cost_minor": "20"})
    order = client.get("/api/orders/"+order_id).json()
    line_id = order["lines"][0]["id"]
    html_post(client, "order-remove-line", {"order_id": order_id, "line_id": str(line_id)})
    html_post(client, "order-line", {"order_id": order_id, "product_id": str(world["product"]["id"]),
        "quantity": "4", "unit_cost_minor": "20"})
    line_id = client.get("/api/orders/"+order_id).json()["lines"][0]["id"]
    html_post(client, "order-submit", {"order_id": order_id})
    html_post(client, "order-receive", {"order_id": order_id, "qty_"+str(line_id): "4",
        "request_key": secrets.token_urlsafe(18), "note": "Form receipt"})
    assert client.get("/api/orders/"+order_id).json()["status"] == "received"
    other = html_post(client, "order-create", {"supplier_id": str(world["supplier"]["id"]),
        "warehouse_id": str(world["main"]["id"])})
    html_post(client, "order-cancel", {"order_id": other.split("/")[-1]})
    location = html_post(client, "count-create", {"warehouse_id": str(world["main"]["id"]), "note": "Form count"})
    count_id = location.split("/")[-1]
    count = client.get("/api/counts/"+count_id).json()
    inputs = {"qty_"+str(line["id"]): "3" for line in count["lines"]}
    html_post(client, "count-save", {"count_id": count_id, "mode": "save", **inputs})
    html_post(client, "count-save", {"count_id": count_id, "mode": "post", "request_key": secrets.token_urlsafe(18), **inputs})
    assert client.get("/api/counts/"+count_id).json()["status"] == "posted"
    next_count = html_post(client, "count-create", {"warehouse_id": str(world["main"]["id"])})
    html_post(client, "count-cancel", {"count_id": next_count.split("/")[-1]})


def test_html_user_management(client, password):
    login(client, password)
    html_post(client, "user-create", {"username": "new-operator", "password": "synthetic-"+secrets.token_urlsafe(18), "role": "operator"})
    target = next(user for user in client.get("/api/users").json() if user["username"] == "new-operator")
    html_post(client, "user-access", {"user_id": str(target["id"]), "role": "viewer", "enabled": "true"})
    html_post(client, "user-password", {"user_id": str(target["id"]), "password": "new-synthetic-"+secrets.token_urlsafe(18)})
    changed = next(user for user in client.get("/api/users").json() if user["id"] == target["id"])
    assert changed["role"] == "viewer"
    assert changed["session_version"] == target["session_version"] + 2


def test_expired_session_is_refused(client, password, monkeypatch):
    login(client, password)
    security = load("stockdesk.security")
    future = security.time.time() + 9*60*60
    monkeypatch.setattr(security.time, "time", lambda: future)
    assert client.get("/api/products").status_code == 401


def test_chunked_body_limit(client):
    def chunks():
        for _ in range(5):
            yield b"x" * 65536
    result = client.post("/api/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert result.status_code == 413


def test_unicode_csrf_value_is_rejected_without_crash(client):
    token(client)
    result = client.post("/login", data={"csrf_token": "résumé", "username": "owner", "password": "not-a-password"})
    assert result.status_code == 403


def test_csv_export_keeps_one_snapshot_across_pages(client, password, db, db_path, world, monkeypatch):
    import csv
    import io
    from contextlib import closing

    db.executemany("INSERT INTO products(sku,name,unit,reorder_level,unit_cost_minor) VALUES (?,?, 'each',0,1)",
                   [(f"ZZ-{i:04}", f"Synthetic item {i}") for i in range(201)])
    login(client, password)
    reports = load("stockdesk.services.reports")
    original = reports.inventory
    changed = False

    def change_between_pages(connection, **kwargs):
        nonlocal changed
        result = original(connection, **kwargs)
        if not changed:
            changed = True
            with closing(load("stockdesk.db").connect(db_path)) as writer:
                writer.execute("INSERT INTO products(sku,name,unit,reorder_level,unit_cost_minor) "
                               "VALUES ('AA-NEW','Concurrent product','each',0,1)")
        return result

    monkeypatch.setattr(reports, "inventory", change_between_pages)
    response = client.get(f"/api/exports/inventory.csv?warehouse_id={world['main']['id']}")
    assert response.status_code == 200
    records = list(csv.DictReader(io.StringIO(response.text)))
    skus = [item["sku"] for item in records]
    assert len(skus) == 203
    assert len(set(skus)) == 203
    assert "AA-NEW" not in skus
