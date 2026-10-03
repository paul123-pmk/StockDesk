CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    session_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE login_attempts (
    name_hash TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    window_start REAL NOT NULL,
    blocked_until REAL NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE warehouses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
) STRICT;

CREATE TABLE suppliers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
) STRICT;

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    reorder_level INTEGER NOT NULL CHECK(reorder_level BETWEEN 0 AND 1000000000),
    unit_cost_minor INTEGER NOT NULL CHECK(unit_cost_minor BETWEEN 0 AND 1000000000),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE balances (
    product_id INTEGER NOT NULL REFERENCES products(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity BETWEEN 0 AND 1000000000),
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (product_id, warehouse_id)
) STRICT;

CREATE TABLE operations (
    id INTEGER PRIMARY KEY,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK(kind IN ('receive','issue','transfer','po_receive','count_post')),
    request_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (actor_id, request_key)
) STRICT;

CREATE TABLE movements (
    id INTEGER PRIMARY KEY,
    operation_id INTEGER NOT NULL REFERENCES operations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    delta INTEGER NOT NULL CHECK(delta <> 0),
    balance_after INTEGER NOT NULL CHECK(balance_after BETWEEN 0 AND 1000000000),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX movements_balance ON movements(product_id,warehouse_id,id);
CREATE INDEX movements_operation ON movements(operation_id);

CREATE TABLE purchase_orders (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','partial','received','cancelled')),
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE purchase_lines (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
    received INTEGER NOT NULL DEFAULT 0 CHECK(received BETWEEN 0 AND quantity),
    unit_cost_minor INTEGER NOT NULL CHECK(unit_cost_minor BETWEEN 0 AND 1000000000),
    UNIQUE(order_id,product_id)
) STRICT;

CREATE TABLE stock_counts (
    id INTEGER PRIMARY KEY,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted','cancelled')),
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    operation_id INTEGER REFERENCES operations(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX one_draft_per_warehouse ON stock_counts(warehouse_id) WHERE status='draft';

CREATE TABLE count_lines (
    id INTEGER PRIMARY KEY,
    count_id INTEGER NOT NULL REFERENCES stock_counts(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    expected_quantity INTEGER NOT NULL CHECK(expected_quantity BETWEEN 0 AND 1000000000),
    expected_version INTEGER NOT NULL,
    counted_quantity INTEGER CHECK(counted_quantity BETWEEN 0 AND 1000000000),
    UNIQUE(count_id,product_id)
) STRICT;

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX audit_entity ON audit_events(entity_type,entity_id,id);

CREATE TRIGGER immutable_movements_update BEFORE UPDATE ON movements BEGIN
    SELECT RAISE(ABORT,'Stock movements are immutable');
END;
CREATE TRIGGER immutable_movements_delete BEFORE DELETE ON movements BEGIN
    SELECT RAISE(ABORT,'Stock movements are immutable');
END;
CREATE TRIGGER immutable_operations_update BEFORE UPDATE ON operations BEGIN
    SELECT RAISE(ABORT,'Stock operations are immutable');
END;
CREATE TRIGGER immutable_operations_delete BEFORE DELETE ON operations BEGIN
    SELECT RAISE(ABORT,'Stock operations are immutable');
END;
CREATE TRIGGER immutable_audit_update BEFORE UPDATE ON audit_events BEGIN
    SELECT RAISE(ABORT,'Audit events are immutable');
END;
CREATE TRIGGER immutable_audit_delete BEFORE DELETE ON audit_events BEGIN
    SELECT RAISE(ABORT,'Audit events are immutable');
END;
PRAGMA user_version=1;
