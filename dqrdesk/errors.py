class DeskError(Exception):
    """Expected, user-actionable product error."""


class ValidationError(DeskError):
    """Input or configuration validation failed."""


class IntegrityError(DeskError):
    """Stored state or an invariant failed integrity checks."""


class ReviewError(DeskError):
    """A requested review operation is unsafe or invalid."""

