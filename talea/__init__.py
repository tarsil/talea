"""Public Talea declaration, constraint, and validation-error API."""

from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.contract import Contract
from talea.declaration.metadata import Alias
from talea.errors import ErrorCode, ErrorData, ValidationError
from talea.metadata import Deprecated, Description, Examples, ReadOnly, Sensitive, Title, WriteOnly
from talea.serialization import SerializationError, serialize
from talea.spec import Spec, check, field, transform
from talea.spec.dynamic import create_spec
from talea.tagged import Discriminator

__all__ = [
    "Alias",
    "Contract",
    "Deprecated",
    "Description",
    "Discriminator",
    "Ge",
    "Gt",
    "ErrorCode",
    "ErrorData",
    "Examples",
    "Le",
    "Lt",
    "MaxLength",
    "MinLength",
    "MultipleOf",
    "Pattern",
    "ReadOnly",
    "Sensitive",
    "Spec",
    "SerializationError",
    "ValidationError",
    "Title",
    "WriteOnly",
    "check",
    "create_spec",
    "field",
    "serialize",
    "transform",
]

__version__ = "0.1.0"
