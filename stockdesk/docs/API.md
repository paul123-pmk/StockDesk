# JSON API

## Session flow

This API is for trusted first-party clients, not a public multi-tenant service.
Use the same origin as the application. There are no bearer tokens or CORS
allowlists. Responses for domain errors have this shape:

```json
{"error": {"code": "conflict", "message": "A human-readable explanation."}}
```

1. `GET /api/session` obtains an anonymous cookie and `csrf_token`.
2. `POST /api/login` sends `username` and `password` as JSON, with that cookie
   and the `X-CSRF-Token` header.
3. On success call `GET /api/session` again. Login rotates the token. Keep the
   authenticated cookie and the new token for subsequent writes.
4. `POST /api/logout` requires authentication and CSRF; it revokes all sessions
   belonging to that account, then clears the current cookie.

Example using the development dependency `httpx` and an interactive password:

```python
import getpass
import secrets
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=20) as client:
    csrf = client.get("/api/session").json()["csrf_token"]
    response = client.post("/api/login", json={
        "username": input("Username: "),
        "password": getpass.getpass(),
    }, headers={"X-CSRF-Token": csrf})
    response.raise_for_status()
    csrf = client.get("/api/session").json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    response = client.get("/api/inventory", params={"page_size": 50})
    response.raise_for_status()
    print(response.json())
```

The generated OpenAPI document is available to authenticated users at
`GET /api/schema`. Dictionary bodies are further validated in services; consult
this guide for allowed fields and business transitions. External Swagger/CDN
assets are not loaded.

## Data conventions

IDs and quantities are JSON integers, not numeric strings, floats or booleans.
A stock quantity is at most 1,000,000,000. Stock-operation quantity is at least
one. Unit costs and reorder levels may be zero. Costs use integer minor units:
`unit_cost_minor: 4500` displays as 45.00 in the configured currency label.
Currency is not stored per transaction; no FX conversion or historical costing
is performed. Database timestamps are UTC ISO text.

Unknown body fields are rejected. Codes are normalized to uppercase, contain
letters/digits/dot/underscore/hyphen, and are at most 40 characters. Usernames
are stored lowercase and matched case-insensitively. Text disallows control
characters. Product names are at most 160 characters and notes at most 500.
All authenticated accounts read operational records; administrators alone
manage catalog/accounts and read the admin audit/reconciliation endpoints.

List endpoints accept `page` (1+) and `page_size` (1–200, default 50). They return
`items`, `total`, `page`, `page_size`, and `pages`. Individual page reads may
reflect concurrent changes; a CSV export uses one consistent snapshot.

## Inventory and catalog

| Method / path | Body or query | Role |
|---|---|---|
| GET /healthz | None; no business records returned | Public |
| GET /api/products | `search`, `page`, `page_size` | Any signed-in |
| GET /api/products/{id} | None | Any signed-in |
| POST /api/products | `sku`, `name`, optional `unit`, `reorder_level`, `unit_cost_minor` | Admin |
| PUT /api/products/{id} | `expected_version`, `name`, `unit`, `reorder_level`, `unit_cost_minor` | Admin |
| POST /api/products/{id}/active | `active` boolean | Admin |
| GET /api/warehouses | None | Any signed-in |
| POST /api/warehouses | `code`, `name` | Admin |
| GET /api/suppliers | None | Any signed-in |
| POST /api/suppliers | `code`, `name` | Admin |
| GET /api/inventory | `warehouse_id`, `search`, `low_only`, pagination | Any signed-in |
| GET /api/dashboard | None | Any signed-in |
| GET /api/movements | `product_id`, pagination | Any signed-in |
| POST /api/movements | See below | Admin/operator |
| GET /api/exports/inventory.csv | Optional `warehouse_id`, `search`; max 10,000 rows | Any signed-in |

A product edit supplies `expected_version` using the current `version` from a product read. A stale
version returns 409 rather than overwriting another editor's changes. Product
SKU is deliberately not editable in this initial release.

Movement body:

```json
{"kind":"receive","product_id":1,"warehouse_id":1,"quantity":12,"note":"Delivery checked at receiving"}
```

The kind is `receive`, `issue`, or `transfer`. A transfer also supplies
`destination_id`, which must differ from the source warehouse. Each stock
operation requires a 12–128 character `Idempotency-Key` made from letters,
digits, underscore and hyphen. Generate a key once and retain it with your
request. Keys are scoped to the acting user across operation kinds.

An exact retry returns the original operation ID with `replayed: true`, even
after an order/count completes. A changed normalized payload with the same key
returns 409. A failed transaction creates no operation or partial stock update.
A new deliberate action needs a new key. This guarantee applies to movement,
order-receive and count-post endpoints, not arbitrary catalog CRUD requests.

## Purchase orders

| Method / path | Input |
|---|---|
| GET /api/orders | Pagination |
| POST /api/orders | `supplier_id`, `warehouse_id`, optional `note` |
| GET /api/orders/{id} | None |
| POST /api/orders/{id}/lines | `product_id`, `quantity`, `unit_cost_minor` |
| DELETE /api/orders/{id}/lines/{line_id} | None |
| POST /api/orders/{id}/submit | None |
| POST /api/orders/{id}/cancel | None |
| POST /api/orders/{id}/receive | `lines` array; idempotency header required |

All writes require admin/operator, a valid cookie and CSRF. Draft orders can
have lines added/removed. Submission requires at least one line. Submitted and
partial orders accept receipts. An order becomes received when every line is
fully received. Only draft or unreceived submitted orders can be cancelled.

Receipt example (use the order's LINE ID, not the product ID):

```json
{"lines":[{"line_id":7,"quantity":4},{"line_id":8,"quantity":2}]}
```

Duplicate line IDs, lines belonging to a different order, zero quantities and
excess receipt quantities are rejected. One bad line rolls back the full
receipt. List order is normalized for idempotent retries.

## Stock counts

| Method / path | Input |
|---|---|
| GET /api/counts | Pagination |
| POST /api/counts | `warehouse_id`, optional `note` |
| GET /api/counts/{id} | None |
| PUT /api/counts/{id}/lines | `lines` array |
| POST /api/counts/{id}/post | Idempotency header required |
| POST /api/counts/{id}/cancel | None |

Count-line input uses the COUNT LINE ID and a nonnegative counted quantity:

```json
{"lines":[{"line_id":1,"quantity":18},{"line_id":2,"quantity":0}]}
```

Partial saves are allowed, but posting requires every captured line to have a
count. Stock versions must still match the count snapshot. Changed stock which
later returns to the same quantity still makes the count stale. Posting only
creates movement rows where the physical quantity differs from the snapshot.

## Accounts and admin reports

| Method / path | Input |
|---|---|
| GET /api/users | None |
| POST /api/users | `username`, `password`, `role` |
| PUT /api/users/{id}/access | `role`, `enabled` boolean |
| PUT /api/users/{id}/password | `password` |
| GET /api/audit | Pagination |
| GET /api/reconcile | None |

All these endpoints require admin. Roles are `admin`, `operator`, `viewer`.
There is no password-hash field in account responses. Resetting a password or
changing access increments the user's session version. The last active admin
is protected from demotion and disabling.

## Failure handling

| Status | Meaning / client action |
|---|---|
| 401 | Missing, expired, revoked, disabled or invalid authentication; sign in again |
| 403 | Role or CSRF failure; obtain valid access/token, never blindly bypass |
| 404 | Resource missing |
| 409 | Version/idempotency/state/stock conflict; inspect current state |
| 413 | Body exceeds 256 KiB |
| 422 | Malformed or invalid field; correct input |
| 429 | Login temporarily blocked |
| 503 | Storage unavailable/busy or database not initialized; investigate then retry safely |

Do not assume a timeout means a stock request failed. Retry with the same
idempotency key and business body to discover its original result. Never change
keys simply to force a timed-out request through a second time.
