"""Public Talea declaration, constraint, and validation-error API."""

from talea.callables import validate_call
from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.contract import Contract
from talea.declaration.metadata import Alias
from talea.errors import ErrorCode, ErrorData, ErrorTree, ValidationError
from talea.json_schema import SchemaProjectionError
from talea.metadata import Deprecated, Description, Examples, ReadOnly, Sensitive, Title, WriteOnly
from talea.representation import Representation
from talea.resources import ResourceLimitError, ResourcePolicy
from talea.serialization import SerializationError, serialize
from talea.spec import Spec, apply_patch, check, derive_spec, field, transform
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
    "ErrorTree",
    "Examples",
    "Le",
    "Lt",
    "MaxLength",
    "MinLength",
    "MultipleOf",
    "Pattern",
    "ReadOnly",
    "Representation",
    "ResourceLimitError",
    "ResourcePolicy",
    "SchemaProjectionError",
    "Sensitive",
    "Spec",
    "apply_patch",
    "SerializationError",
    "ValidationError",
    "Title",
    "WriteOnly",
    "check",
    "create_spec",
    "derive_spec",
    "field",
    "serialize",
    "transform",
    "validate_call",
]

__version__ = "0.6.0"
