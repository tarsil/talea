"""Normalize and specialize Talea generic annotations safely."""

import ast
from annotationlib import Format, ForwardRef
from collections.abc import Mapping
from types import GenericAlias, UnionType
from typing import (
    Annotated,
    Literal,
    NoDefault,
    TypeVar,
    Union,
    cast,
    evaluate_forward_ref,
    get_args,
    get_origin,
)

from talea.declaration.policies import schema_is_covariant_override
from talea.schema.resolution import AnnotationResolutionError, resolve_annotation


def contains_type_parameter(value: object) -> bool:
    """Return whether a runtime annotation contains a free type parameter."""

    return bool(type_parameters_in(value))


def type_parameters_in(value: object) -> tuple[TypeVar, ...]:
    """Return free type parameters nested in an annotation or Talea binding."""

    if isinstance(value, TypeVar):
        return (value,)
    binding = vars(value) if isinstance(value, type) else {}
    if "__talea_generic_arguments__" in binding:
        arguments = binding["__talea_generic_arguments__"]
    else:
        arguments = get_args(value)
    return tuple(parameter for item in arguments for parameter in type_parameters_in(item))


def substitute_annotation(annotation: object, substitutions: Mapping[TypeVar, object]) -> object:
    """Replace type parameters structurally before canonical schema resolution."""

    if isinstance(annotation, TypeVar):
        return substitutions.get(annotation, annotation)
    if isinstance(annotation, ForwardRef):
        validate_annotation_expression(annotation.__forward_arg__)
        evaluated = evaluate_forward_ref(annotation, format=Format.VALUE)
        return substitute_annotation(evaluated, substitutions)
    if isinstance(annotation, type):
        namespace = vars(annotation)
        origin = namespace.get("__talea_generic_origin__")
        arguments = namespace.get("__talea_generic_arguments__")
        if origin is not None and arguments is not None:
            specialized = tuple(substitute_annotation(item, substitutions) for item in arguments)
            return origin[specialized[0] if len(specialized) == 1 else specialized]
        return annotation
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if not arguments:
        return annotation
    if origin is Annotated:
        return Annotated[substitute_annotation(arguments[0], substitutions), *arguments[1:]]
    if origin in (UnionType, Union):
        substituted = tuple(substitute_annotation(item, substitutions) for item in arguments)
        return Union[substituted]
    substituted = tuple(substitute_annotation(item, substitutions) for item in arguments)
    if isinstance(annotation, GenericAlias):
        return GenericAlias(cast(type, origin), substituted[0] if len(substituted) == 1 else substituted)
    copier = getattr(annotation, "copy_with", None)
    assert copier is not None
    return copier(substituted)


def type_argument_name(argument: object) -> str:
    """Return a stable human-readable specialization argument label."""

    if isinstance(argument, type):
        return argument.__qualname__
    return str(argument).replace("typing.", "")


def normalize_specialization(
    origin: type[object],
    supplied: object,
) -> tuple[tuple[object, ...], tuple[TypeVar, ...]]:
    """Validate and normalize one complete origin specialization."""

    declaration = vars(origin)["__talea_declaration__"]
    parameters = declaration.type_params
    arguments = supplied if isinstance(supplied, tuple) else (supplied,)
    if len(arguments) < len(parameters):
        defaults = []
        for parameter in parameters[len(arguments) :]:
            default = parameter.__default__
            if default is NoDefault:
                raise TypeError(f"{origin.__qualname__} expects {len(parameters)} type arguments")
            defaults.append(default)
        arguments = (*arguments, *defaults)
    if len(arguments) != len(parameters):
        raise TypeError(f"{origin.__qualname__} expects {len(parameters)} type arguments")
    substitutions = dict(zip(parameters, arguments, strict=True))
    for parameter, argument in substitutions.items():
        if contains_type_parameter(argument):
            continue
        candidate = resolve_annotation(argument)
        bound = parameter.__bound__
        if bound is not None:
            resolved_bound = resolve_annotation(substitute_annotation(bound, substitutions))
            if not schema_is_covariant_override(candidate, resolved_bound):
                raise TypeError(f"type argument {argument!r} violates bound for {parameter.__name__}")
        constraints = parameter.__constraints__
        if constraints and not any(
            schema_is_covariant_override(
                candidate,
                resolve_annotation(substitute_annotation(constraint, substitutions)),
            )
            for constraint in constraints
        ):
            raise TypeError(f"type argument {argument!r} violates constraints for {parameter.__name__}")
    free = tuple(parameter for argument in arguments for parameter in type_parameters_in(argument))
    return tuple(arguments), tuple(dict.fromkeys(free))


def validate_annotation_strings(annotation: object) -> None:
    """Reject executable syntax in explicit string forward references."""

    if isinstance(annotation, ForwardRef):
        validate_annotation_expression(annotation.__forward_arg__)
        return
    if isinstance(annotation, str):
        validate_annotation_expression(annotation)
        return
    if get_origin(annotation) is Literal:
        return
    for argument in get_args(annotation):
        validate_annotation_strings(argument)


def needs_local_namespace(annotations: Mapping[str, object]) -> bool:
    """Return whether deferred annotations may need their defining local scope."""

    return any(_contains_forward_reference(annotation) for annotation in annotations.values())


def retain_referenced_namespace(
    annotations: Mapping[str, object], namespace: Mapping[str, object] | None
) -> Mapping[str, object] | None:
    """Retain only local names an explicit deferred annotation still requires."""

    if namespace is None:
        return None
    names = set[str]()
    for annotation in annotations.values():
        _collect_annotation_names(annotation, names)
    retained = {name: namespace[name] for name in names if name in namespace}
    return retained or None


def _collect_annotation_names(annotation: object, names: set[str]) -> None:
    if isinstance(annotation, ForwardRef):
        expression = annotation.__forward_arg__
    elif isinstance(annotation, str):
        expression = annotation
    else:
        if get_origin(annotation) is Literal:
            return
        for argument in get_args(annotation):
            _collect_annotation_names(argument, names)
        return
    tree = ast.parse(expression, mode="eval")
    names.update(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))


def _contains_forward_reference(annotation: object) -> bool:
    if isinstance(annotation, (str, ForwardRef)):
        return True
    if get_origin(annotation) is Literal:
        return False
    return any(_contains_forward_reference(argument) for argument in get_args(annotation))


def validate_annotation_expression(expression: str) -> None:
    """Accept only structural annotation syntax before Python evaluates a string."""

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise AnnotationResolutionError(expression) from None
    allowed = (
        ast.Expression,
        ast.Name,
        ast.Attribute,
        ast.Subscript,
        ast.BinOp,
        ast.BitOr,
        ast.Tuple,
        ast.Load,
        ast.Constant,
        ast.Call,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise AnnotationResolutionError(expression)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            validate_annotation_expression(node.value)
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name)
            and node.func.id == "Discriminator"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and type(node.args[0].value) is str
            and not node.keywords
        ):
            raise AnnotationResolutionError(expression)
