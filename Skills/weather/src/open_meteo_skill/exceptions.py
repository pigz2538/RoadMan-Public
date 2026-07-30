"""Exception classes for Open-Meteo API."""


class OpenMeteoError(Exception):
    """Base exception for Open-Meteo API errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class APIError(OpenMeteoError):
    """Raised when the API returns an error response."""

    pass


class ValidationError(OpenMeteoError):
    """Raised when request parameters are invalid."""

    pass


class RateLimitError(OpenMeteoError):
    """Raised when API rate limit is exceeded."""

    pass


class TimeoutError(OpenMeteoError):
    """Raised when request times out."""

    pass


class ConnectionError(OpenMeteoError):
    """Raised when connection to API fails."""

    pass
