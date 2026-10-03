"""Stable business failures, safe to expose without SQL or stack traces."""


class DomainError(ValueError):
    def __init__(self, message: str, code: str = "invalid_input", status: int = 422):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def conflict(message: str) -> DomainError:
    return DomainError(message, "conflict", 409)


def missing(kind: str) -> DomainError:
    return DomainError(f"{kind} was not found.", "not_found", 404)
