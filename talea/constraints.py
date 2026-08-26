"""Public immutable declarations for Talea's built-in constraints.

Constraint objects are annotation metadata and canonical declaration values.
They contain no runtime validation methods: schema resolution verifies and
normalizes them, then Talea's shared emitter compiles their checks directly.
"""

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from re import Pattern as RegexPattern

__all__ = ["Ge", "Gt", "Le", "Lt", "MaxLength", "MinLength", "MultipleOf", "Pattern"]

type Numeric = int | float | Decimal
type Constraint = Gt | Ge | Lt | Le | MultipleOf | MinLength | MaxLength | Pattern


def _validate_numeric(value: object, name: str, *, nonzero: bool = False) -> None:
    """Validate one strict, finite numeric constraint declaration value."""

    if type(value) not in (int, float, Decimal):
        raise TypeError(f"{name} requires an int, float, or Decimal value")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{name} requires a finite value")
    if type(value) is Decimal and not value.is_finite():
        raise ValueError(f"{name} requires a finite value")
    if nonzero and value == 0:
        raise ValueError(f"{name} requires a non-zero value")


@dataclass(frozen=True, slots=True)
class Gt[T: Numeric]:
    """Require a numeric value to be strictly greater than ``value``.

    The boundary must be a finite ``int``, ``float``, or ``Decimal``. Schema
    resolution later requires its concrete numeric family to match the
    constrained annotation, preventing implicit cross-family coercion.
    """

    value: T

    def __post_init__(self) -> None:
        _validate_numeric(self.value, type(self).__name__)


@dataclass(frozen=True, slots=True)
class Ge[T: Numeric]:
    """Require a numeric value to be greater than or equal to ``value``.

    Boundaries are finite and retain their exact numeric family. Talea
    canonicalizes redundant lower bounds when the annotation is resolved.
    """

    value: T

    def __post_init__(self) -> None:
        _validate_numeric(self.value, type(self).__name__)


@dataclass(frozen=True, slots=True)
class Lt[T: Numeric]:
    """Require a numeric value to be strictly less than ``value``.

    The declaration is immutable and carries truth only; it is never called
    as a runtime validator.
    """

    value: T

    def __post_init__(self) -> None:
        _validate_numeric(self.value, type(self).__name__)


@dataclass(frozen=True, slots=True)
class Le[T: Numeric]:
    """Require a numeric value to be less than or equal to ``value``.

    The boundary must be finite and of the same concrete numeric family as the
    constrained annotation.
    """

    value: T

    def __post_init__(self) -> None:
        _validate_numeric(self.value, type(self).__name__)


@dataclass(frozen=True, slots=True)
class MultipleOf[T: Numeric]:
    """Require a numeric value to be an integral multiple of ``value``.

    Zero and non-finite divisors are rejected immediately. Negative divisors
    are legal declaration syntax and normalize to their positive equivalent
    during schema resolution. Integer, floating-point, and Decimal execution
    use family-specific compiled semantics.
    """

    value: T

    def __post_init__(self) -> None:
        _validate_numeric(self.value, type(self).__name__, nonzero=True)


@dataclass(frozen=True, slots=True)
class MinLength:
    """Require a sized supported value to contain at least ``value`` members.

    Args:
        value: Non-negative exact integer length. Booleans are rejected.

    The marker supports strings, bytes, and Talea's concrete container schemas.
    Inapplicable annotations and contradictory ranges fail during resolution.
    """

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("MinLength requires an int value")
        if self.value < 0:
            raise ValueError("MinLength requires a non-negative value")


@dataclass(frozen=True, slots=True)
class MaxLength:
    """Require a sized supported value to contain at most ``value`` members.

    Args:
        value: Non-negative exact integer length. Booleans are rejected.

    The marker supports strings, bytes, and Talea's concrete container schemas.
    Inapplicable annotations and contradictory ranges fail during resolution.
    """

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("MaxLength requires an int value")
        if self.value < 0:
            raise ValueError("MaxLength requires a non-negative value")


@dataclass(frozen=True, slots=True, init=False)
class Pattern:
    """Require a string to contain a regular-expression match.

    Args:
        value: A string pattern or compiled string ``re.Pattern``. Compilation
            happens once when this declaration is created. Bytes patterns are
            rejected because ``Pattern`` applies only to strict ``str`` fields.

    Invalid expressions raise ``re.error`` before a Spec class can be created.
    Generated validators bind the compiled object safely and call ``search``;
    pattern text is never interpolated into generated Python source.
    """

    pattern: str
    flags: int
    _compiled: RegexPattern[str] = field(repr=False, compare=False, hash=False)

    def __init__(self, value: str | RegexPattern[str]) -> None:
        if isinstance(value, str):
            compiled = re.compile(value)
        elif isinstance(value, RegexPattern) and isinstance(value.pattern, str):
            compiled = value
        else:
            raise TypeError("Pattern requires a string or compiled string pattern")
        object.__setattr__(self, "pattern", compiled.pattern)
        object.__setattr__(self, "flags", compiled.flags)
        object.__setattr__(self, "_compiled", compiled)

    @property
    def compiled(self) -> RegexPattern[str]:
        """Return the single compiled expression reused by generated validators."""

        return self._compiled
