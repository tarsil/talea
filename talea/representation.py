"""Declare reusable external representations for Python domain values.

The declaration is immutable metadata for :class:`typing.Annotated`. Talea
resolves it into one canonical schema node; callbacks are retained by that
node and compiled directional artifacts rather than a registry.
"""

from collections.abc import Callable
from functools import partial
from inspect import getattr_static, iscoroutinefunction, isgeneratorfunction
from types import BuiltinFunctionType, FunctionType
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    import sys

    if sys.version_info >= (3, 15):
        from typing import TypeForm as _TypeForm
    else:
        type _TypeForm[T] = object

__all__ = ["Representation"]


class _MissingDirection:
    __slots__ = ()


_MISSING = _MissingDirection()


def _validate_callback(callback: object, direction: str) -> None:
    """Reject callback forms that cannot provide one synchronous value."""

    if isinstance(callback, (staticmethod, classmethod)):
        raise TypeError(f"Representation {direction} callback cannot be a descriptor")
    if not callable(callback):
        raise TypeError(f"Representation {direction} callback must be callable")
    target = callback
    if not isinstance(callback, (FunctionType, BuiltinFunctionType, partial)):
        target = getattr_static(type(callback), "__call__", callback)
    if iscoroutinefunction(callback) or iscoroutinefunction(target):
        raise TypeError(f"Representation {direction} callback must be synchronous")
    if isgeneratorfunction(callback) or isgeneratorfunction(target):
        raise TypeError(f"Representation {direction} callback must return one value")


class Representation[InputT, InternalT, OutputT]:
    """Bind optional external input and output contracts to an internal value.

    ``input`` and ``load`` form one direction; ``output`` and ``dump`` form the
    other. At least one complete pair is required. Callbacks are trusted,
    synchronous application code. Load results are validated against the
    ``Annotated`` base type, and dump results are validated against ``output``
    before Talea projects or returns them.

    Python 3.15 ``TypeForm`` relates ``input`` and ``output`` type expressions
    to the callback parameter and result types. Python 3.14 uses an honest
    ``object`` fallback while retaining the callback generic relationships.
    Talea validates the supplied declarations during schema resolution on both
    versions; these annotations add no runtime branch or schema node.
    """

    __slots__ = ("_dump", "_input", "_load", "_output", "_sealed")

    _input: object
    _load: Callable[[InputT], InternalT] | None
    _output: object
    _dump: Callable[[InternalT], OutputT] | None
    _sealed: bool

    @overload
    def __init__(
        self,
        *,
        input: _TypeForm[InputT],
        load: Callable[[InputT], InternalT],
        output: _TypeForm[OutputT],
        dump: Callable[[InternalT], OutputT],
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        input: _TypeForm[InputT],
        load: Callable[[InputT], InternalT],
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        output: _TypeForm[OutputT],
        dump: Callable[[InternalT], OutputT],
    ) -> None: ...

    def __init__(
        self,
        *,
        input: object = _MISSING,
        load: Callable[[InputT], InternalT] | _MissingDirection = _MISSING,
        output: object = _MISSING,
        dump: Callable[[InternalT], OutputT] | _MissingDirection = _MISSING,
    ) -> None:
        """Validate and retain one immutable directional declaration."""

        input_declared = input is not _MISSING
        load_declared = load is not _MISSING
        output_declared = output is not _MISSING
        dump_declared = dump is not _MISSING
        if input_declared != load_declared:
            raise TypeError("Representation input and load must be declared together")
        if output_declared != dump_declared:
            raise TypeError("Representation output and dump must be declared together")
        if not input_declared and not output_declared:
            raise TypeError("Representation requires an input or output direction")
        if load_declared:
            _validate_callback(load, "load")
        if dump_declared:
            _validate_callback(dump, "dump")
        object.__setattr__(self, "_input", input)
        object.__setattr__(self, "_load", None if load is _MISSING else load)
        object.__setattr__(self, "_output", output)
        object.__setattr__(self, "_dump", None if dump is _MISSING else dump)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation after construction."""

        raise AttributeError("Representation declarations are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject deletion from an immutable declaration."""

        raise AttributeError("Representation declarations are immutable")

    @property
    def input(self) -> object | None:
        """Return the declared input type form, or ``None`` when absent."""

        return None if self._input is _MISSING else self._input

    @property
    def load(self) -> Callable[[InputT], InternalT] | None:
        """Return the trusted input callback, or ``None`` when absent."""

        return self._load

    @property
    def output(self) -> object | None:
        """Return the declared output type form, or ``None`` when absent."""

        return None if self._output is _MISSING else self._output

    @property
    def dump(self) -> Callable[[InternalT], OutputT] | None:
        """Return the trusted output callback, or ``None`` when absent."""

        return self._dump

    @property
    def _has_input(self) -> bool:
        return self._input is not _MISSING

    @property
    def _has_output(self) -> bool:
        return self._output is not _MISSING

    def __repr__(self) -> str:
        """Describe declared directions without invoking callback representations."""

        directions = []
        if self._has_input:
            directions.append("input=<type form>, load=<callback>")
        if self._has_output:
            directions.append("output=<type form>, dump=<callback>")
        return f"Representation({', '.join(directions)})"
