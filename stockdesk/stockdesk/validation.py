"""Strict domain validation; HTTP and CLI adapters must normalize explicitly."""

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .errors import DomainError

MAX_QUANTITY = 1_000_000_000
MAX_COST_MINOR = 1_000_000_000


def integer(value: Any, label: str, minimum: int = 0, maximum: int = MAX_QUANTITY) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError(f"{label} must be a whole number from {minimum} to {maximum}.")
    return value


def text(value: Any, label: str, maximum: int = 160, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DomainError(f"{label} must be text.")
    value = value.strip()
    if (not value and not empty) or len(value) > maximum:
        raise DomainError(f"{label} must contain {'0' if empty else '1'} to {maximum} characters.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise DomainError(f"{label} contains an unsupported control character.")
    return value


def code(value: Any, label: str) -> str:
    value = text(value, label, maximum=40)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise DomainError(f"{label} may use letters, numbers, dots, underscores and hyphens.")
    return value.upper()


def identifier(value: Any, label: str = "ID") -> int:
    return integer(value, label, minimum=1)


def flag(value: Any, label: str = "enabled") -> bool:
    if type(value) is not bool:
        raise DomainError(f"{label} must be true or false.")
    return value


def fields(payload: Any, allowed: set[str]) -> Mapping:
    if not isinstance(payload, Mapping):
        raise DomainError("Request must be an object.")
    unknown = set(payload) - allowed
    if unknown:
        raise DomainError("Unexpected fields: " + ", ".join(sorted(map(str, unknown))))
    return payload


def choice(value: Any, label: str, options: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in options:
        raise DomainError(f"{label} must be one of: {', '.join(options)}.")
    return value


def idempotency_key(value: Any) -> str:
    value = text(value, "Idempotency key", maximum=128)
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", value):
        raise DomainError("Idempotency key must contain 12–128 letters, numbers, underscores or hyphens.")
    return value


def form_integer(value: Any, label: str, minimum: int = 0) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value):
        raise DomainError(f"{label} must be a whole number.")
    return integer(int(value), label, minimum)
