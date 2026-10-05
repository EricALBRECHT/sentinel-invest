class SecError(Exception):
    """Base error for SEC collection."""


class MissingSecCik(SecError):
    """The company has no CIK, so EDGAR cannot be queried."""


class SecClientError(SecError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SecCompanyFactsNotFound(SecClientError):
    """EDGAR has no companyfacts document for this CIK."""
