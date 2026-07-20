class CollectorError(Exception):
    """Base error for any collector failure."""


class CollectorTimeoutError(CollectorError):
    """The remote source did not respond within the configured timeout."""


class CollectorResponseError(CollectorError):
    """The remote source responded, but the response could not be used."""
