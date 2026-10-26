class AuthError(Exception):
    """Base class for authentication service errors."""


class ValidationError(AuthError):
    """Invalid input (password, email, role, etc.)."""


class DuplicateEmailError(AuthError):
    """Email is already registered."""


class NotFoundError(AuthError):
    """Requested resource does not exist."""


class ForbiddenError(AuthError):
    """Authenticated caller is not allowed to perform this action."""
