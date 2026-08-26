"""Errors raised when canonical Talea truth cannot be projected faithfully."""

__all__ = ["SchemaProjectionError"]


class SchemaProjectionError(TypeError):
    """Report a contract whose external JSON representation is unknowable.

    Projection rejects declarations such as arbitrary input transforms and
    output serializers in the mode where their callback-defined domain cannot
    be inferred. Validation failures remain owned by :class:`ValidationError`;
    this exception is limited to standards-document generation.
    """
