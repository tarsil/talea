# Strict callable boundaries

`validate_call` compiles Python annotations into a strict argument-and-return
boundary for an ordinary synchronous function. Use it when an application
service, SDK entry point, callback implementation, or domain function should
reject values that do not satisfy its declared Python contract.

```python
from talea import validate_call


@validate_call
def transfer(amount: int, fee: int = 0) -> int:
    return amount - fee
```

The declaration happens once. Talea resolves every parameter and return
annotation into canonical Schema truth, validates declared defaults, and emits
a specialized Python wrapper. The warm path does not interpret
`inspect.Signature`, rebuild dictionaries, walk Schema nodes, consult a
registry, or acquire a lock.

## The strict boundary

Callable validation handles values that Python code already possesses. It is
therefore the same strict operation as `Contract.validate`, not an external
conversion operation such as `Contract.from_python`.

```python
@validate_call
def count(value: int) -> int:
    return value


count(1)       # accepted
count(True)    # ValidationError at ("value",)
count(1.0)     # ValidationError at ("value",)
count("1")     # ValidationError at ("value",)
```

Mappings do not construct Specs or dataclasses. A `Representation` annotation
expects its internal Python value; it does not call `load`. Return validation
does not call `dump`, `to_dict`, or `to_json`. Parse or convert at an explicit
external boundary first, then pass the constructed value to the callable.

`ResourcePolicy` is intentionally absent. It governs hostile external input
transport and traversal. A strict callable accepts trusted, already-existing
Python values and retains normal Talea current-state checks for mutable Specs,
dataclasses, TypedDicts, and containers.

## Binding, validation, and application failures

Three failure domains remain distinct:

| Stage | Owner | Failure |
| --- | --- | --- |
| Python call shape | CPython binding | plain `TypeError` |
| parameter value | Talea contract | `ValidationError` at the parameter path |
| application function | application | original exception unchanged |
| return value | Talea contract | `ValidationError` beneath `("return",)` |

Missing required arguments, unexpected keywords, duplicate values, and too
many positional arguments are rejected by the generated wrapper's real Python
signature. Talea does not manufacture or parse CPython error text. A valid call
shape is validated in declaration order and stops at the first invalid
parameter. That fail-fast policy avoids success-path error-list allocation.

After all arguments pass, Talea invokes the original function exactly once.
Argument failure invokes it zero times. Return failure occurs after the one
application call. `ValueError`, `TypeError`, `RuntimeError`, and application
exception subclasses raised by the function propagate unchanged; catch
`ValidationError` before a broad `TypeError` because Talea validation errors
are also type errors.

Successful strict validation preserves argument identity. Talea does not copy
lists, dictionaries, dataclasses, Specs, or represented internal values merely
to validate them. The application may mutate an argument after entry; Talea
does not monitor it. Only an object returned from the function is checked again
under the return contract.

## Defaults

Every declared default is validated while the decorator is applied. An invalid
default prevents publication of the wrapper and reports the parameter
location. Defaults whose canonical Schema proves immutable are then trusted
when Python supplies that same omitted default; Talea does not repeat a
redundant validation on every call. An explicitly supplied different value is
validated normally.

Defaults with mutable current state are validated on every invocation,
including omission. Mutating a list, mutable dataclass, mutable Spec graph, or
TypedDict default into an invalid state therefore causes the next call to fail
before application code runs. Talea does not alter Python's ordinary
shared-mutable-default behavior.

## Supported annotations and metadata

The callable owner consumes the same canonical resolver and validation emitter
as `Contract` and `Spec`. A supported parameter or return can therefore use
primitives, constraints, concrete Specs and generics, standard-library
dataclasses, TypedDicts, aliases, containers, unions, tagged unions, recursive
schemas, and `Representation` where those annotations already have executable
Talea semantics.

All parameters and the return require annotations. Talea does not turn a
missing annotation, `Any`, or `object` into an unchecked hole. Generic function
declarations with unresolved runtime type parameters are rejected; static
generic typing is not runtime specialization. `typing.overload` declarations
remain static-only, while the concrete runtime implementation supplies the
executable contract.

`Alias` changes external representation names, not Python keyword binding. A
parameter named `amount` is still called as `amount=...`, regardless of an
alias inside its annotation. `Sensitive` redacts Talea-owned parameter and
return failures, including nested details. It cannot redact exceptions, logs,
side effects, or messages produced by application function code.

Direct decoration can resolve module names and live function-local aliases,
including deferred annotations. If a function-local name has already gone out
of scope before `validate_call(function)` runs, Python has not retained enough
truth to recover it; Talea rejects the declaration rather than inspecting
arbitrary historical frames or creating a forward-reference registry.

## Function identity and typing

The wrapper follows standard Python conventions. `__name__`, `__qualname__`,
`__doc__`, `__module__`, and `__annotations__` are copied, `__wrapped__` points
to the original function, and `inspect.signature()` reports the original
public signature. Decorating the same Talea wrapper again is idempotent, so
validation is not silently stacked.

The decorator is typed with `ParamSpec` and a return `TypeVar`. Static tooling
therefore retains positional and keyword parameter names and types, defaults,
and the return type:

```python
@validate_call
def settle(amount: int, reference: str) -> bool:
    return True


accepted: bool = settle(amount=1, reference="invoice-1843")
```

ParamSpec preserves the static callable shape. It is not interpreted at
runtime and does not infer concrete TypeVar substitutions for each invocation.

## Introspection

`inspect_callable()` is the single public projection of callable truth:

```python
from talea.introspection import inspect_callable

info = inspect_callable(settle)
assert tuple(parameter.name for parameter in info.parameters) == (
    "amount",
    "reference",
)
```

`CallableInfo` and `ParameterInfo` are frozen and slotted. They expose the
original `inspect.Signature`, ordered names and parameter kinds, canonical
parameter and return schemas, required/default state, and sync classification.
They do not expose generated source, compiled validators, the original
callable, globals, locks, caches, or binding instructions. Standard
`__wrapped__` remains the way ordinary Python tooling reaches the original.

Callable introspection deliberately provides no JSON Schema or OpenAPI
operation document. A callable is not an HTTP or RPC route; framework tooling
may consume its parameter Schemas without making Talea own requests,
responses, routing, or serialization.

## Concurrency, reentrancy, and security

Compilation is eager at decoration time, so concurrent calls perform no lazy
publication and acquire no Talea lock. A validated function can call another
validated function or itself recursively; every invocation validates its own
boundary, and no global validation state suppresses recursion.

Generated identifiers are compiler-owned. Annotations, defaults, metadata,
function names, and callback objects are retained as values in the generated
function namespace rather than interpolated into source. Hostile reprs,
quotes, newlines, Unicode, and unusual qualified names cannot become code. A
wrapper naturally owns its contract and compiled globals for its own lifetime;
there is no process-global callable registry or cache.

Talea owns binding shape, generated-source safety, argument and return
enforcement, metadata, and Sensitive redaction. The application owns function
CPU, memory, I/O, locks, side effects, mutation, recursion depth, exceptions,
and thread safety.

## Current callable forms

The current execution surface supports ordinary synchronous functions whose
parameters are positional-or-keyword, including keyword invocation and static
defaults. Positional-only parameters, keyword-only parameters, `*args`,
`**kwargs`, `Unpack[TypedDict]`, ordinary methods, classmethods, staticmethods,
and async functions are not yet part of the public execution contract.
Generators, async generators, and arbitrary callable instances are outside the
callable-boundary scope and are rejected explicitly.

Decorator ordering matters because an unrelated decorator can change the
object Talea receives. In particular, do not rely on either ordering with
`staticmethod` or `classmethod` until descriptor integration is documented.

## Complete executable example

The payment-service example separates JSON construction from strict callable
validation and demonstrates constraints, nested Specs, defaults, Python
binding errors, invalid parameter and return values, application exceptions,
Sensitive redaction, `__wrapped__`, and immutable introspection.

{!> ../../../docs_src/tutorials/callable_boundaries.py !}

## Performance evidence

`task benchmark_callables` compares direct calls, equivalent handwritten
strict wrappers, Talea wrappers, and `inspect.Signature.bind`; it also measures
one, two, and five primitive arguments, structured values, defaults, failures,
cold compilation, success/failure allocations, retained memory, and bytecode.
The fixed warm wrapper contains no generic binder, parameter loop, Schema walk,
registry lookup, or lock. Callable support adds no execution path to Specs or
Contracts that do not use the decorator.
