"""Own immutable canonical truth for declared Talea callable boundaries."""

from dataclasses import dataclass
from inspect import Signature
from types import FunctionType
from typing import Literal

from talea.schema.nodes import Schema

type ParameterKind = Literal[
    "POSITIONAL_ONLY",
    "POSITIONAL_OR_KEYWORD",
    "VAR_POSITIONAL",
    "KEYWORD_ONLY",
    "VAR_KEYWORD",
]
type ParameterRole = Literal["value", "receiver"]
type CallableKind = Literal["function", "instance_method", "class_method", "static_method"]


class _MissingDefault:
    """Represent the absence of a Python callable default."""

    __slots__ = ()


MISSING_DEFAULT = _MissingDefault()


@dataclass(frozen=True, slots=True)
class _CallableParameter:
    """Retain one resolved parameter and its future-complete binding shape."""

    name: str
    kind: ParameterKind
    schema: Schema | None
    default: object
    default_is_immutable: bool
    sensitive: bool
    role: ParameterRole = "value"
    unpack_typed_dict: bool = False

    @property
    def required(self) -> bool:
        """Return whether Python binding requires an explicit argument."""

        return self.default is MISSING_DEFAULT


@dataclass(frozen=True, slots=True)
class _CallableSchema:
    """Own the complete resolved contract for one declared callable.

    The original :class:`inspect.Signature` is declaration evidence and public
    shape, while ordered parameter records are Talea's binding plan. Runtime
    wrappers compile from this value once and never interpret the Signature or
    annotations on a successful call.
    """

    function: FunctionType
    signature: Signature
    parameters: tuple[_CallableParameter, ...]
    return_schema: Schema
    return_sensitive: bool
    is_async: bool
    callable_kind: CallableKind = "function"
