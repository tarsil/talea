"""Compile canonical callable contracts into strict synchronous wrappers."""

from collections.abc import Callable
from functools import update_wrapper
from types import FunctionType
from typing import cast

from talea.callables.models import MISSING_DEFAULT, _CallableSchema
from talea.codegen import _GeneratedNames
from talea.schema.nodes import Schema
from talea.validation.emission import _ValidationEmitter


def compile_sync_wrapper(contract: _CallableSchema) -> Callable[..., object]:
    """Compile one native-binding wrapper with inline strict validation.

    Parameter names are Python identifiers supplied by ``inspect.Signature``;
    every other runtime object, including defaults and error-location labels,
    is retained behind a compiler-owned global. Generated source therefore
    never contains annotations, metadata, defaults, or callable names.
    """

    parameter_names = tuple(parameter.name for parameter in contract.parameters)
    names = _GeneratedNames(parameter_names)
    wrapper_name = names.allocate("callable")
    namespace: dict[str, object] = {"__name__": __name__}
    declarations: list[str] = []
    default_names: dict[str, str] = {}
    for parameter in contract.parameters:
        declaration = parameter.name
        if parameter.default is not MISSING_DEFAULT:
            default_name = names.allocate("default")
            namespace[default_name] = parameter.default
            default_names[parameter.name] = default_name
            declaration = f"{declaration}={default_name}"
        declarations.append(declaration)

    lines = [f"def {wrapper_name}({', '.join(declarations)}):"]
    emitter = _ValidationEmitter(
        lines,
        names,
        namespace,
        title=contract.function.__qualname__,
    )
    for parameter in contract.parameters:
        indentation = 1
        if parameter.default is not MISSING_DEFAULT and parameter.default_is_immutable:
            emitter.emit(1, f"if {parameter.name} is not {default_names[parameter.name]}:")
            indentation = 2
        location = emitter.bind("parameter_name", parameter.name)
        emitter.emit_schema(
            cast(Schema, parameter.schema),
            parameter.name,
            (location,),
            indentation,
            sensitive=parameter.sensitive,
        )

    original = emitter.bind("original_callable", contract.function)
    result = emitter.variable("result")
    emitter.emit(1, f"{result} = {original}({', '.join(parameter_names)})")
    return_location = emitter.bind("return_location", "return")
    emitter.emit_schema(
        contract.return_schema,
        result,
        (return_location,),
        1,
        sensitive=contract.return_sensitive,
    )
    emitter.emit(1, f"return {result}")

    source = "\n".join(lines)
    exec(compile(source, "<talea callable>", "exec"), namespace)
    wrapper = cast(FunctionType, namespace[wrapper_name])
    wrapper.__code__ = wrapper.__code__.replace(
        co_name=contract.function.__name__,
        co_qualname=contract.function.__qualname__,
    )
    update_wrapper(wrapper, contract.function)
    return wrapper
