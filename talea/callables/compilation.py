"""Compile canonical callable contracts into strict sync and async wrappers."""

from collections.abc import Callable
from functools import update_wrapper
from types import FunctionType
from typing import cast

from talea.callables.models import MISSING_DEFAULT, _CallableSchema
from talea.codegen import _GeneratedNames
from talea.schema.nodes import Schema
from talea.validation.emission import _ValidationEmitter


def compile_sync_wrapper(contract: _CallableSchema) -> Callable[..., object]:
    """Compile one synchronous native-binding wrapper."""

    return _compile_wrapper(contract, asynchronous=False)


def compile_async_wrapper(contract: _CallableSchema) -> Callable[..., object]:
    """Compile one coroutine-function native-binding wrapper."""

    return _compile_wrapper(contract, asynchronous=True)


def _compile_wrapper(contract: _CallableSchema, *, asynchronous: bool) -> Callable[..., object]:
    """Emit one native-binding wrapper with inline strict validation.

    Parameter names are Python identifiers supplied by ``inspect.Signature``;
    every other runtime object, including defaults and error-location labels,
    is retained behind a compiler-owned global. Generated source therefore
    never contains annotations, metadata, defaults, or callable names.
    """

    parameter_names = tuple(parameter.name for parameter in contract.parameters)
    names = _GeneratedNames(parameter_names)
    wrapper_name = names.allocate("callable")
    namespace: dict[str, object] = {"__name__": __name__}
    default_names: dict[str, str] = {}
    for parameter in contract.parameters:
        if parameter.default is not MISSING_DEFAULT:
            default_name = names.allocate("default")
            namespace[default_name] = parameter.default
            default_names[parameter.name] = default_name

    declaration = _signature_declaration(contract, default_names)
    declaration_prefix = "async def" if asynchronous else "def"
    lines = [f"{declaration_prefix} {wrapper_name}({declaration}):"]
    emitter = _ValidationEmitter(
        lines,
        names,
        namespace,
        title=contract.function.__qualname__,
    )
    for parameter in contract.parameters:
        if parameter.role == "receiver":
            continue
        indentation = 1
        if parameter.default is not MISSING_DEFAULT and parameter.default_is_immutable:
            emitter.emit(1, f"if {parameter.name} is not {default_names[parameter.name]}:")
            indentation = 2
        location = emitter.bind("parameter_name", parameter.name)
        schema = cast(Schema, parameter.schema)
        if parameter.kind == "VAR_POSITIONAL":
            index = emitter.variable("variadic_index")
            item = emitter.variable("variadic_item")
            enumerate_name = emitter.runtime("enumerate", enumerate)
            emitter.emit(indentation, f"for {index}, {item} in {enumerate_name}({parameter.name}):")
            emitter.emit_schema(
                schema,
                item,
                (location, index),
                indentation + 1,
                sensitive=parameter.sensitive,
            )
        elif parameter.kind == "VAR_KEYWORD" and not parameter.unpack_typed_dict:
            key = emitter.variable("variadic_key")
            item = emitter.variable("variadic_item")
            emitter.emit(indentation, f"for {key}, {item} in {parameter.name}.items():")
            emitter.emit_schema(
                schema,
                item,
                (location, key),
                indentation + 1,
                sensitive=parameter.sensitive,
            )
        else:
            emitter.emit_schema(
                schema,
                parameter.name,
                (location,),
                indentation,
                sensitive=parameter.sensitive,
            )

    original = emitter.bind("original_callable", contract.function)
    result = emitter.variable("result")
    await_prefix = "await " if asynchronous else ""
    emitter.emit(1, f"{result} = {await_prefix}{original}({_call_arguments(contract)})")
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


def _signature_declaration(contract: _CallableSchema, default_names: dict[str, str]) -> str:
    """Emit Python's full parameter grammar from the canonical callable IR."""

    declarations: list[str] = []
    parameters = contract.parameters
    positional_only = sum(parameter.kind == "POSITIONAL_ONLY" for parameter in parameters)
    has_variadic = any(parameter.kind == "VAR_POSITIONAL" for parameter in parameters)
    keyword_boundary_emitted = False
    for index, parameter in enumerate(parameters):
        if parameter.kind == "VAR_POSITIONAL":
            declaration = f"*{parameter.name}"
            keyword_boundary_emitted = True
        elif parameter.kind == "VAR_KEYWORD":
            declaration = f"**{parameter.name}"
        else:
            if parameter.kind == "KEYWORD_ONLY" and not has_variadic and not keyword_boundary_emitted:
                declarations.append("*")
                keyword_boundary_emitted = True
            declaration = parameter.name
            if parameter.default is not MISSING_DEFAULT:
                declaration = f"{declaration}={default_names[parameter.name]}"
        declarations.append(declaration)
        if positional_only and index + 1 == positional_only:
            declarations.append("/")
    return ", ".join(declarations)


def _call_arguments(contract: _CallableSchema) -> str:
    """Forward already-bound values to the original callable without normalization."""

    arguments: list[str] = []
    for parameter in contract.parameters:
        if parameter.kind == "VAR_POSITIONAL":
            arguments.append(f"*{parameter.name}")
        elif parameter.kind == "KEYWORD_ONLY":
            arguments.append(f"{parameter.name}={parameter.name}")
        elif parameter.kind == "VAR_KEYWORD":
            arguments.append(f"**{parameter.name}")
        else:
            arguments.append(parameter.name)
    return ", ".join(arguments)
