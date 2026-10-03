"""Temporary databases and synthetic credentials; nothing is persisted in Git."""

import importlib
import secrets

import pytest


def load(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Required module not implemented: {exc.name}")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.sqlite"


@pytest.fixture
def db(db_path):
    persistence = load("stockdesk.db")
    connection = persistence.connect(db_path)
    persistence.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def password():
    return "test-only-" + secrets.token_urlsafe(20)


@pytest.fixture
def owner(db, password):
    return load("stockdesk.services.auth").bootstrap_user(db, "owner", password)


@pytest.fixture
def operator(db, owner, password):
    return load("stockdesk.services.auth").create_user(
        db, owner["id"], "operator", password, "operator"
    )


@pytest.fixture
def viewer(db, owner, password):
    return load("stockdesk.services.auth").create_user(
        db, owner["id"], "viewer", password, "viewer"
    )


@pytest.fixture
def world(db, owner):
    catalog = load("stockdesk.services.catalog")
    product = catalog.add_product(
        db, owner["id"], sku="BOLT-M8", name="M8 hex bolt", unit="each",
        reorder_level=10, unit_cost_minor=4500,
    )
    second = catalog.add_product(
        db, owner["id"], sku="WASH-M8", name="M8 washer", unit="each",
        reorder_level=20, unit_cost_minor=800,
    )
    main = catalog.add_warehouse(db, owner["id"], code="MAIN", name="Main store")
    annex = catalog.add_warehouse(db, owner["id"], code="ANNEX", name="Annex")
    supplier = catalog.add_supplier(db, owner["id"], code="SYNTH-01", name="Sample Supply")
    return dict(product=product, second=second, main=main, annex=annex, supplier=supplier)
