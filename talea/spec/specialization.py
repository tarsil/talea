"""Own generic Spec specialization and weak canonical reuse."""

from collections.abc import Mapping
from typing import TypeVar, cast

from talea.spec.declaration import _DECLARATION_LOCK, _SpecDeclaration
from talea.spec.generics import (
    normalize_specialization,
    substitute_annotation,
    type_argument_name,
)


def specialize_spec(cls: type[object], supplied: object) -> type[object]:
    """Return one class-owned concrete or partially bound specialization."""

    declaration = cast(_SpecDeclaration, vars(cls)["__talea_declaration__"])
    origin = declaration.generic_origin or cls
    origin_declaration = cast(_SpecDeclaration, vars(origin)["__talea_declaration__"])
    if not origin_declaration.type_params or (declaration.generic_origin is not None and not declaration.type_params):
        raise TypeError(f"{cls.__qualname__} is not a generic Spec")
    cache = origin_declaration.specializations
    assert cache is not None
    if declaration.generic_origin is None:
        direct_arguments = supplied if isinstance(supplied, tuple) else (supplied,)
        if len(direct_arguments) == len(origin_declaration.type_params):
            with _DECLARATION_LOCK:
                specialized = cache.get(direct_arguments)
                if specialized is not None:
                    return specialized
    if declaration.generic_origin is not None:
        supplied_arguments = supplied if isinstance(supplied, tuple) else (supplied,)
        if len(supplied_arguments) != len(declaration.type_params):
            raise TypeError(f"{cls.__qualname__} expects {len(declaration.type_params)} type arguments")
        partial_substitutions = dict(zip(declaration.type_params, supplied_arguments, strict=True))
        expanded = tuple(
            substitute_annotation(argument, partial_substitutions) for argument in declaration.generic_arguments
        )
        arguments, free_parameters = normalize_specialization(origin, expanded)
    else:
        arguments, free_parameters = normalize_specialization(origin, supplied)
    with _DECLARATION_LOCK:
        specialized = cache.get(arguments)
        if specialized is not None:
            return specialized
        substitutions: Mapping[TypeVar, object] = dict(zip(origin_declaration.type_params, arguments, strict=True))
        label = ", ".join(type_argument_name(argument) for argument in arguments)
        specialized_name = f"{origin.__name__}[{label}]"
        specialized_qualname = f"{origin.__qualname__}[{label}]"
        namespace: dict[str, object] = {
            "__module__": origin.__module__,
            "__qualname__": specialized_qualname,
            "__talea_specialization__": (origin, arguments, substitutions, free_parameters),
        }
        specialized = type(cls)(specialized_name, (origin,), namespace)
        cache[arguments] = specialized
        return specialized
