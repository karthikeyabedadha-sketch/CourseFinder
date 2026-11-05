class StoreError(Exception):
    """Base class for expected, user-presentable data errors."""


class DuplicateError(StoreError):
    """A record with the same unique value (URL, slug, name) already exists."""


class NotFoundError(StoreError):
    """The requested record does not exist or is not visible."""
