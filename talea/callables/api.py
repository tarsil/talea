"""Declare strict synchronous callable boundaries from Python annotations."""

import inspect
from collections.abc import Callable
from types import FunctionType
from typing import ParamSpec, TypeVar, cast, get_type_hints

from talea.callables.compilation import compile_sync_wrapper
from talea.callables.models import (
    MISSING_DEFAULT,
    ParameterKind,
    _CallableParameter,
    _CallableSchema,
)
from talea.declaration.policies import schema_values_are_immutable
from talea.errors import ValidationError
from talea.metadata import annotation_metadata
from talea.schema.resolution import resolve_annotation
from talea.validation.compilation import compile_validator

P = ParamSpec("P")
R = TypeVar("R")

_CONTRACT_ATTRIBUTE = "__talea_callable_contract__"
_SUPPORTED_KIND = inspect.Parameter.POSITIONAL_OR_KEYWORD


def validate_call(function: Callable[P, R], /) -> Callable[P, R]:
    """Compile strict argument and return validation for a synchronous function.

    Every parameter and the return value must have a Talea-supported annotation.
    The initial execution surface accepts ordinary Python functions containing only
    positional-or-keyword parameters. Python itself owns argument binding, so
    invalid call shapes raise ``TypeError``; valid shapes with invalid values
    raise :class:`~talea.ValidationError` at the parameter or ``return``
    location. Validation never coerces, serializes, or applies external input
    policy, and the original function runs exactly once after arguments pass.

    The returned callable has the same static ``ParamSpec`` and return type as
    ``function``. Standard wrapper metadata and ``__wrapped__`` preserve normal
    Python inspection. Decorating an existing Talea wrapper returns it unchanged.

    Args:
        function: An ordinary synchronous Python function.

    Returns:
        A generated same-signature wrapper retaining one immutable contract.

    Raises:
        TypeError: If the target or callable form is outside this execution
            slice or an annotation is missing.
        AnnotationResolutionError: If an annotation is not a concrete supported
            Talea contract.
        ValidationError: If a declared default already violates its annotation.
    """

    _validate_target(function)
    declared = cast(FunctionType, function)
    retained = vars(declared).get(_CONTRACT_ATTRIBUTE)
    if isinstance(retained, _CallableSchema):
        return function
    signature = inspect.signature(declared)
    annotations = _resolve_annotations(declared)
    parameters = tuple(
        _resolve_parameter(declared, parameter, annotations) for parameter in signature.parameters.values()
    )
    if "return" not in annotations:
        raise TypeError(f"{declared.__qualname__} requires a return annotation")
    return_annotation = annotations["return"]
    return_schema = resolve_annotation(return_annotation)
    contract = _CallableSchema(
        declared,
        signature,
        parameters,
        return_schema,
        bool(annotation_metadata(return_annotation).sensitive),
        False,
    )
    wrapper = compile_sync_wrapper(contract)
    setattr(wrapper, _CONTRACT_ATTRIBUTE, contract)
    return cast(Callable[P, R], wrapper)


def _validate_target(function: object) -> None:
    """Reject targets whose execution semantics are outside the sync kernel."""

    function_type = type(function)
    if function_type in (staticmethod, classmethod):
        raise TypeError("validate_call does not yet accept staticmethod or classmethod descriptors")
    if function_type is not FunctionType:
        raise TypeError("validate_call requires an ordinary Python function")
    if inspect.isasyncgenfunction(function):
        raise TypeError("validate_call does not support async generator functions")
    if inspect.isgeneratorfunction(function):
        raise TypeError("validate_call does not support generator functions")
    if inspect.iscoroutinefunction(function):
        raise TypeError("validate_call does not yet support async functions")
    if getattr(function, "__type_params__", ()):
        raise TypeError("validate_call requires concrete runtime annotations; generic functions are not supported")


def _resolve_annotations(function: FunctionType) -> dict[str, object]:
    """Resolve deferred annotations from globals and the live declaration scope.

    Future-annotations strings do not retain function-local aliases themselves.
    A directly applied decorator can still resolve the live defining scope
    without retaining its frame or creating a forward-reference registry.
    """

    frame = inspect.currentframe()
    try:
        declaration_frame = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
        localns = declaration_frame.f_locals if declaration_frame is not None else None
        try:
            return get_type_hints(
                function,
                globalns=function.__globals__,
                localns=localns,
                include_extras=True,
            )
        except NameError as error:
            raise TypeError(
                f"annotations for {function.__qualname__} cannot be resolved from function globals "
                "or the live declaration scope"
            ) from error
    finally:
        del frame


def _resolve_parameter(
    function: FunctionType,
    parameter: inspect.Parameter,
    annotations: dict[str, object],
) -> _CallableParameter:
    """Resolve and validate one parameter into canonical callable truth."""

    if parameter.kind is not _SUPPORTED_KIND:
        raise TypeError(f"validate_call does not yet support {parameter.kind.description} parameter {parameter.name!r}")
    if parameter.name not in annotations:
        raise TypeError(f"parameter {parameter.name!r} on {function.__qualname__} requires an annotation")
    annotation = annotations[parameter.name]
    schema = resolve_annotation(annotation)
    default = MISSING_DEFAULT if parameter.default is inspect.Parameter.empty else parameter.default
    immutable = False
    if default is not MISSING_DEFAULT:
        validator = compile_validator(schema, sensitive=bool(annotation_metadata(annotation).sensitive))
        try:
            validator(default)
        except ValidationError as error:
            located = error.prefixed((parameter.name,), title=function.__qualname__)
            raise located from located.__cause__
        immutable = schema_values_are_immutable(schema)
    return _CallableParameter(
        parameter.name,
        cast(ParameterKind, parameter.kind.name),
        schema,
        default,
        immutable,
        bool(annotation_metadata(annotation).sensitive),
    )


def _callable_schema(function: object) -> _CallableSchema:
    """Return the retained canonical schema for one Talea wrapper."""

    contract = vars(function).get(_CONTRACT_ATTRIBUTE) if type(function) is FunctionType else None
    if not isinstance(contract, _CallableSchema):
        raise TypeError("inspect_callable requires a function decorated with validate_call")
    return contract
