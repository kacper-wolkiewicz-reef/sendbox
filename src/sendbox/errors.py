"""Error types shared across sendbox."""


class SendboxError(Exception):
    """Raised for any expected, user-facing sendbox failure."""


class RequestRejected(SendboxError):
    """Raised when the container asks for an ssh connection sendbox will not make."""
