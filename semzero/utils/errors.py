class SchemaNotFoundError(Exception):
    """Raised when a requested table or schema cannot be found in the database."""

    pass


class UnknownNodeError(Exception):
    """Raised when a node ID is not found in the schema graph."""

    pass


class MappingConfidenceError(Exception):
    """Raised when a column mapping falls below the required confidence threshold."""

    pass


class UnsupportedColumnTypeError(Exception):
    """Raised when a SQL column type cannot be mapped to a known vector representation."""

    pass


class SchemaValidationError(Exception):
    """Raised when the challenger schema does not match the expected production schema."""

    pass
