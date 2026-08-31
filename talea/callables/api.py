"""Declare strict synchronous callable boundaries from Python annotations."""

import inspect
from annotationlib import Format, get_annotations
from collections.abc import Callable
from types import FrameType, FunctionType, MethodType, SimpleNamespace
from typing import ParamSpec, TypeVar, Unpack, cast, get_args, get_origin, get_type_hints

from talea.callables.compilation import compile_sync_wrapper
from talea.callables.models import (
    MISSING_DEFAULT,
    CallableKind,
    _CallableParameter,
    _CallableSchema,
)
from talea.declaration.policies import schema_values_are_immutable
from talea.errors import ValidationError
from talea.metadata import annotation_metadata
from talea.schema.nodes import TypedDictSchema
from talea.schema.resolution import resolve_annotation
from talea.validation.compilation import compile_validator

P = ParamSpec("P")
R = TypeVar("R")

_CONTRACT_ATTRIBUTE = "__talea_callable_contract__"


def validate_call(function: Callable[P, R], /) -> Callable[P, R]:
    """Compile strict argument and return validation for a synchronous function.

    Every user-value parameter and the return value must have a Talea-supported
    annotation. Positional-only, positional-or-keyword, keyword-only, variadic
    positional, scalar variadic keyword, and ``Unpack[TypedDict]`` parameters
    share one generated-signature compiler. Python itself owns argument binding, so
    invalid call shapes raise ``TypeError``; valid shapes with invalid values
    raise :class:`~talea.ValidationError` at the parameter or ``return``
    location. Validation never coerces, serializes, or applies external input
    policy, and the original function runs exactly once after arguments pass.

    The returned callable has the same static ``ParamSpec`` and return type as
    ``function``. Standard wrapper metadata and ``__wrapped__`` preserve normal
    Python inspection. Decorating an existing Talea wrapper returns it unchanged.

    Args:
        function: A synchronous Python function, or a ``classmethod`` or
            ``staticmethod`` descriptor when ``validate_call`` is outermost.

    Returns:
        A generated same-signature wrapper retaining one immutable contract.

    Raises:
        TypeError: If the target or callable form is outside this execution
            slice or an annotation is missing.
        AnnotationResolutionError: If an annotation is not a concrete supported
            Talea contract.
        ValidationError: If a declared default already violates its annotation.
    """

    declaration_namespace = _caller_namespace()
    target_type = type(function)
    if target_type is classmethod:
        declared = cast(FunctionType, cast(classmethod, function).__func__)
        _validate_function(declared)
        return cast(Callable[P, R], _ClassMethodCandidate(declared, declaration_namespace))
    if target_type is staticmethod:
        declared = cast(FunctionType, cast(staticmethod, function).__func__)
        _validate_function(declared)
        return cast(Callable[P, R], _StaticMethodCandidate(declared, declaration_namespace))
    if target_type is _MethodCandidate:
        return function
    _validate_function(function)
    declared = cast(FunctionType, function)
    retained = vars(declared).get(_CONTRACT_ATTRIBUTE)
    if isinstance(retained, _CallableSchema):
        return function
    if _declared_in_class_body(declared):
        return cast(Callable[P, R], _MethodCandidate(declared, declaration_namespace))
    return cast(
        Callable[P, R],
        _decorate_function(declared, "function", localns=declaration_namespace),
    )


def _caller_namespace() -> dict[str, object] | None:
    """Snapshot the direct declaration scope without retaining its frame."""

    frame = cast(FrameType, inspect.currentframe())
    try:
        caller = cast(FrameType, cast(FrameType, frame.f_back).f_back)
        outer = caller.f_back
        return {
            **(dict(outer.f_locals) if outer is not None else {}),
            **dict(caller.f_locals),
        }
    finally:
        del frame


class _MethodCandidate:
    """Delay receiver exemption until Python confirms class ownership.

    A normal ``@validate_call`` method is replaced with a generated function
    during ``__set_name__``. The resulting class therefore uses Python's native
    function descriptor for all later binding. Wrapping this temporary value in
    ``classmethod`` or ``staticmethod`` is rejected so descriptor order stays
    explicit and does not introduce a permanent adapter call.
    """

    __slots__ = ("function", "localns", "__dict__")

    def __init__(self, function: FunctionType, localns: dict[str, object] | None) -> None:
        self.function = function
        self.localns = localns
        self.__dict__.update(
            __name__=function.__name__,
            __qualname__=function.__qualname__,
            __doc__=function.__doc__,
            __module__=function.__module__,
            __annotations__=function.__annotations__,
            __wrapped__=function,
            __signature__=_declared_signature(function),
        )

    def __set_name__(self, owner: type[object], name: str) -> None:
        wrapper = _decorate_function(
            self.function,
            "instance_method",
            localns=_owner_namespace(owner, self.localns),
        )
        setattr(owner, name, wrapper)

    def __call__(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.localns = None
        raise TypeError("validate_call must be outermost for classmethod and staticmethod descriptors")


class _ClassMethodCandidate(classmethod):
    """Compile a class method only after its owning class exists."""

    def __init__(self, function: FunctionType, localns: dict[str, object] | None) -> None:
        super().__init__(function)
        self.localns = localns

    def __set_name__(self, owner: type[object], name: str) -> None:
        wrapper = _decorate_function(
            cast(FunctionType, self.__func__),
            "class_method",
            localns=_owner_namespace(owner, self.localns),
        )
        setattr(owner, name, classmethod(wrapper))


class _StaticMethodCandidate(staticmethod):
    """Compile a static method only after its owning class exists."""

    def __init__(self, function: FunctionType, localns: dict[str, object] | None) -> None:
        super().__init__(function)
        self.localns = localns

    def __set_name__(self, owner: type[object], name: str) -> None:
        wrapper = _decorate_function(
            cast(FunctionType, self.__func__),
            "static_method",
            localns=_owner_namespace(owner, self.localns),
        )
        setattr(owner, name, staticmethod(wrapper))


def _owner_namespace(
    owner: type[object],
    declaration_namespace: dict[str, object] | None,
) -> dict[str, object]:
    """Expose the completed class namespace for deferred annotation resolution."""

    return {
        **(declaration_namespace or {}),
        **vars(owner),
        owner.__name__: owner,
    }


def _declared_in_class_body(function: FunctionType) -> bool:
    """Recognize direct class-body syntax without treating nested functions as methods."""

    return "." in function.__qualname__.rsplit(".<locals>.", 1)[-1]


def _declared_signature(function: FunctionType) -> inspect.Signature:
    """Read binding shape without eagerly evaluating deferred annotation names."""

    return inspect.signature(function, annotation_format=Format.FORWARDREF)


def _decorate_function(
    function: FunctionType,
    callable_kind: CallableKind,
    *,
    localns: dict[str, object] | None = None,
) -> FunctionType:
    """Resolve and compile one already-classified function or descriptor member."""

    signature = _declared_signature(function)
    annotations = _resolve_annotations(function, localns=localns)
    receiver = callable_kind in ("instance_method", "class_method")
    parameters = tuple(
        _resolve_parameter(function, parameter, annotations, receiver=index == 0 and receiver)
        for index, parameter in enumerate(signature.parameters.values())
    )
    if "return" not in annotations:
        raise TypeError(f"{function.__qualname__} requires a return annotation")
    return_annotation = annotations["return"]
    return_schema = resolve_annotation(return_annotation)
    contract = _CallableSchema(
        function,
        signature,
        parameters,
        return_schema,
        bool(annotation_metadata(return_annotation).sensitive),
        False,
        callable_kind,
    )
    wrapper = cast(FunctionType, compile_sync_wrapper(contract))
    setattr(wrapper, _CONTRACT_ATTRIBUTE, contract)
    return wrapper


def _validate_function(function: object) -> None:
    """Reject targets whose execution semantics are outside the sync kernel."""

    function_type = type(function)
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


def _resolve_annotations(
    function: FunctionType,
    *,
    localns: dict[str, object] | None = None,
) -> dict[str, object]:
    """Resolve deferred annotations from globals and the live declaration scope.

    Future-annotations strings do not retain function-local aliases themselves.
    A directly applied decorator can still resolve the live defining scope
    without retaining its frame or creating a forward-reference registry.
    """

    try:
        raw = get_annotations(function, format=Format.FORWARDREF)
        return get_type_hints(
            SimpleNamespace(__annotations__=raw),
            globalns=function.__globals__,
            localns=localns,
            include_extras=True,
        )
    except NameError as error:
        raise TypeError(
            f"annotations for {function.__qualname__} cannot be resolved from function globals "
            "or the live declaration scope"
        ) from error


def _resolve_parameter(
    function: FunctionType,
    parameter: inspect.Parameter,
    annotations: dict[str, object],
    *,
    receiver: bool,
) -> _CallableParameter:
    """Resolve and validate one parameter into canonical callable truth."""

    kind = parameter.kind.name
    if receiver:
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise TypeError(f"method receiver {parameter.name!r} must be positional")
        if parameter.default is not inspect.Parameter.empty:
            raise TypeError(f"method receiver {parameter.name!r} cannot have a default")
        return _CallableParameter(
            parameter.name,
            kind,
            None,
            MISSING_DEFAULT,
            False,
            False,
            "receiver",
        )
    if parameter.name not in annotations:
        raise TypeError(f"parameter {parameter.name!r} on {function.__qualname__} requires an annotation")
    annotation = annotations[parameter.name]
    unpack_typed_dict = False
    if get_origin(annotation) is Unpack:
        if parameter.kind is not inspect.Parameter.VAR_KEYWORD:
            raise TypeError("Unpack is supported only for variadic keyword parameters")
        arguments = get_args(annotation)
        schema = resolve_annotation(arguments[0]) if len(arguments) == 1 else None
        if not isinstance(schema, TypedDictSchema):
            raise TypeError("variadic keyword Unpack requires a concrete TypedDict")
        unpack_typed_dict = True
    else:
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
        kind,
        schema,
        default,
        immutable,
        bool(annotation_metadata(annotation).sensitive),
        unpack_typed_dict=unpack_typed_dict,
    )


def _callable_schema(function: object) -> _CallableSchema:
    """Return the retained canonical schema for one Talea wrapper."""

    if type(function) is MethodType:
        function = function.__func__
    elif type(function) is classmethod:
        function = cast(classmethod, function).__func__
    elif type(function) is staticmethod:
        function = cast(staticmethod, function).__func__
    contract = vars(function).get(_CONTRACT_ATTRIBUTE) if type(function) is FunctionType else None
    if not isinstance(contract, _CallableSchema):
        raise TypeError("inspect_callable requires a function decorated with validate_call")
    return contract
