"""The validators must reject coercions that change stock quantities."""

import importlib

import pytest


def module():
    try:
        return importlib.import_module("stockdesk.validation")
    except ModuleNotFoundError:
        pytest.fail("The validation module is not implemented")


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", None, [], {}, -1, 10**10])
def test_integer_rejects_coercion_and_out_of_range(bad):
    v = module()
    with pytest.raises(ValueError):
        v.integer(bad, "quantity")


@pytest.mark.parametrize("value", [0, 1, 900, 1_000_000_000])
def test_integer_accepts_exact_bounded_integers(value):
    assert module().integer(value, "quantity") == value


def test_positive_quantity_cannot_be_zero():
    with pytest.raises(ValueError):
        module().integer(0, "quantity", minimum=1)


@pytest.mark.parametrize("bad", ["", "   ", "\x00", "abc\nxyz", 23, None])
def test_text_rejects_empty_or_control_values(bad):
    with pytest.raises(ValueError):
        module().text(bad, "name")


def test_text_preserves_unicode_and_trims_edges():
    assert module().text("  Café bolt  ", "name") == "Café bolt"


@pytest.mark.parametrize("value", ["abc", "A-1", "SKU_201", "bin.3"])
def test_code_normalizes_case(value):
    assert module().code(value, "sku") == value.upper()


@pytest.mark.parametrize("value", ["../a", "bad sku", "=SUM(A1)", "a/b", "é"])
def test_code_rejects_unsafe_or_ambiguous_values(value):
    with pytest.raises(ValueError):
        module().code(value, "sku")


def test_unknown_fields_rejected():
    with pytest.raises(ValueError):
        module().fields({"sku": "X", "role": "admin"}, {"sku"})
