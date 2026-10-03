# StockDesk design

## Purpose and constraints
Build a runnable, local-first inventory operations application, not a simulated
mature licensing submission. The user requested a new codebase without further
approval rounds. This implementation chooses the previously suggested StockDesk
concept. All initial work is AI-generated and its origin stays visible.

Python 3.11 or newer; FastAPI, Jinja2, Uvicorn; SQLite on a local disk. A single
business controls all warehouses. Whole-unit stock only; unit costs are integer
minor currency units. The default currency label is KES; no currency conversion,
accounting valuation method, tax calculation or payments. No third-party source,
vendor trees, generated filler, fixed passwords or personal customer data.

## Alternatives and selection
A Flask monolith was originally proposed but the execution environment has no
Flask and package download access failed. FastAPI and its test stack are already
available, so use FastAPI rather than ship an untested dependency stack.
A separate SPA would increase authentication and build complexity without
improving the core stock workflow. Server-rendered forms are selected instead.

## Domain and invariants
Users have administrator, operator or viewer roles. Only administrators manage
catalogs and accounts. Administrators and operators post inventory operations,
purchase orders and stock counts. Viewers have read-only access to all warehouses
and cost reports. The last enabled administrator cannot be removed or demoted.

A product/location balance never goes below zero or above 1,000,000,000 units.
Stock transactions run under SQLite BEGIN IMMEDIATE. A transfer commits both
legs or neither. Movement records and audit events reject UPDATE and DELETE.
Idempotency keys are scoped to actor and operation payload: exact replay returns
the original operation ID; reusing a key for a different payload is a conflict.

Purchase orders move draft -> submitted -> partial -> received. Only draft lines
can change. Receipts cannot exceed remaining ordered quantities and a multi-line
receipt is atomic. Cancellation is allowed only before any receipt.

A stock count snapshots every active product at one warehouse, including empty
balances. At most one draft exists per warehouse and at most 500 products are
included. All lines must be counted before posting. Every balance version must
still match, detecting quantity changes even when the final quantity is the same.
A stale count must be cancelled and recreated, not silently reconciled.

## Architecture and data flow
stockdesk/db.py owns connections, migrations and transaction scopes.
stockdesk/services/ owns validation, access policy and transactions, independent
of HTTP. API and HTML endpoints call these same services. Read models and CSV
exports are separate from mutation services. All SQL user values use parameters.

Per-request connections use foreign keys and a busy timeout. Async HTML handlers
parse forms then dispatch database work to a thread. Sync API handlers run in
FastAPI's thread pool. No connection is shared across independent requests.

## Security boundaries
Server-signed HttpOnly SameSite session cookies, CSRF checks for every write
including login, login throttling, scrypt password hashing, session-version
revocation, request-body limits, template escaping, same-origin-only assets,
CSV formula neutralization, and no raw SQL errors in HTTP responses.
Secrets and runtime databases live in ignored instance storage. Production mode
requires an externally supplied session secret, trusted hosts and secure cookies.
This is not a penetration-tested or multi-tenant production service.

## Delivery and acceptance
Ship source, tests, documentation, a CI definition and a self-contained Git
bundle. Exercise HTTP forms and JSON APIs, transaction rollback, permissions,
replays, stale counts, concurrency, backup restoration and reconciliation.
Record actual test results and code metrics, not acceptance promises. CI is
provided but must not be described as executed on hosted runners unless it was.
No fake issues or PR exports will be created. Bundle clone and integrity must
be checked locally before delivery.
