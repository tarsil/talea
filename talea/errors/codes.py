"""Stable machine-readable categories for Talea validation failures."""

from enum import StrEnum

__all__ = ["ErrorCode"]


class ErrorCode(StrEnum):
    """Identify a validation failure independently of its human wording.

    Values are part of Talea's public compatibility surface. They serialize as
    JSON strings and may be compared with ordinary strings, while the enum gives
    generated validators one canonical vocabulary to bind at compile time.
    """

    TYPE = "type"
    LITERAL = "literal"
    UNION = "union"
    MISSING = "missing"
    UNEXPECTED = "unexpected"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    MULTIPLE_OF = "multiple_of"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    PATTERN = "pattern"
    TRANSFORM = "transform"
    FIELD_CHECK = "field_check"
    SPEC_CHECK = "spec_check"
    FACTORY = "factory"
    REPRESENTATION_LOAD = "representation_load"
    REPRESENTATION_RESULT = "representation_result"
    JSON_INVALID = "json_invalid"
    JSON_DUPLICATE = "json_duplicate"
    CYCLE = "cycle"
    DISCRIMINATOR_MISSING = "discriminator_missing"
    DISCRIMINATOR_UNKNOWN = "discriminator_unknown"
