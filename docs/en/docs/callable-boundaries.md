# Strict callable boundaries

`validate_call` compiles Python annotations into a strict argument-and-return
boundary for synchronous and asynchronous functions and methods. Use it when
an application service, SDK entry point, callback implementation, or domain
function should reject values that do not satisfy its declared Python
contract.

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

## Async execution and validation timing

Decorating `async def` emits a real coroutine function with the same Python
signature. Argument validation runs inside that wrapper coroutine. The wrapper
then creates and awaits the application coroutine exactly once, validates the
awaited result against the declared return annotation, and returns the same
result object:

```python
@validate_call
async def authorize(payment_id: int, *, dry_run: bool = False) -> bool:
    return not dry_run


accepted: bool = await authorize(1)
```

Calling `authorize("1")` returns a normal coroutine object; no Talea value
validation or application body executes until it is awaited. If it is never
awaited, ordinary Python unawaited-coroutine behavior applies. Talea does not
add an eager synchronous adapter or custom awaitable. CPython call-shape
binding is different: a missing argument, positional-only violation,
unexpected keyword, duplicate value, or missing keyword-only argument is a
plain `TypeError` immediately when the wrapper is called.

The annotation on `async def operation() -> Result` describes the awaited
`Result`, not the coroutine object. An invalid argument starts the application
body zero times. A valid call creates, awaits, and executes the original
coroutine once. An invalid awaited result fails afterward at `("return",)`.
Application exceptions propagate unchanged; Talea does not reinterpret a
`ValueError`, `RuntimeError`, domain exception, `ExceptionGroup`, or
`asyncio.CancelledError` as validation failure.

Cancellation before the application body leaves it unstarted. Cancellation
during application work reaches that coroutine normally, including its
`finally` blocks, and is neither swallowed, wrapped, retried, nor followed by
return validation. `asyncio.timeout()` and `asyncio.wait_for()` therefore
compose normally, but Talea owns no timeout or retry policy.

The returned coroutine works directly with `asyncio.create_task()`,
`asyncio.gather()`, and `asyncio.TaskGroup`. Each concurrent invocation has
only local validation state. Gather and task groups retain their normal
failure and `ExceptionGroup` behavior; Talea adds no batch semantics.

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

## Complete Python parameter binding

The generated wrapper preserves Python's complete synchronous and asynchronous parameter
grammar: positional-only (`/`), positional-or-keyword, keyword-only (`*`),
`*args`, scalar `**kwargs`, and `**kwargs: Unpack[TypedDict]`. These are all
compiled from the same callable schema and signature emitter. There is no
`Signature.bind` path or generic fallback for complex signatures.

```python
@validate_call
def execute(
    account_id: int,
    /,
    quantity: int,
    *adjustments: int,
    dry_run: bool = False,
    timeout: float,
    **metadata: str,
) -> int:
    return quantity + sum(adjustments)
```

CPython binds `account_id`, `quantity`, `dry_run`, and `timeout` using that real
signature. A positional-only name used as a keyword, a missing keyword-only
parameter, a duplicate value, too many positional arguments, or an unexpected
keyword where no `**kwargs` exists is a plain `TypeError`. Aliases never rename
these Python call names.

For `*adjustments: int`, the annotation applies to each tuple member. Talea
validates from index zero and fails at `("adjustments", index)`. For scalar
`**metadata: str`, it applies to each value in Python's keyword dictionary and
fails at `("metadata", actual_keyword)`. The compiler iterates those
Python-created containers directly; it does not build normalized lists,
tuples, argument maps, or validation copies.

### `Unpack[TypedDict]`

`**kwargs: Unpack[Options]` has different semantics from named keyword-only
parameters. Python's runtime signature contains `**kwargs`, so it accepts the
keyword names syntactically. Talea then validates that collected dictionary as
the canonical closed `TypedDict` structure:

```python
from typing import NotRequired, TypedDict, Unpack


class Options(TypedDict):
    timeout: float
    trace_id: NotRequired[str]


@validate_call
def configure(**kwargs: Unpack[Options]) -> Options:
    return kwargs
```

A missing required `timeout`, an unknown key, or a wrong field value is a
`ValidationError` under `("kwargs", key)`, not a binding `TypeError`. Talea
validates Python's bound dictionary directly against the existing
`TypedDictSchema`; this is strict Python structure validation, not external
`Mapping` conversion. `Alias` metadata does not make an external alias a valid
Python keyword. `ReadOnly` remains structural/static metadata and does not add
runtime mutation enforcement. Concrete generic TypedDict specializations work;
open generic forms retain the normal concrete-runtime requirement.

The location policy is stable across the synchronous and asynchronous surface:

| Value | Validation location |
| --- | --- |
| fixed parameter | `("name", ...)` |
| `*args` item | `("args_name", index, ...)` |
| scalar `**kwargs` value | `("kwargs_name", actual_keyword, ...)` |
| `Unpack[TypedDict]` field | `("kwargs_name", field, ...)` |
| method user argument | the declared parameter path; receiver omitted |
| return value | `("return", ...)` |

Defaults follow the same declaration-validation and immutable/mutable policy
for positional-only, positional-or-keyword, and keyword-only parameters.

## Supported annotations and metadata

The callable owner consumes the same canonical resolver and validation emitter
as `Contract` and `Spec`. A supported parameter or return can therefore use
primitives, constraints, concrete Specs and generics, standard-library
dataclasses, TypedDicts, aliases, containers, unions, tagged unions, recursive
schemas, and `Representation` where those annotations already have executable
Talea semantics.

All user-value parameters and the return require annotations. Talea does not turn a
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

For `async def`, the original function's static return is already a coroutine
whose awaited value is its declared return annotation. `validate_call`
preserves that type unchanged: calling the wrapper remains awaitable and
awaiting it produces the declared result. Talea does not wrap the return type
in a second `Coroutine`.

Positional-only markers, keyword-only requirements, variadic item/value types,
`Unpack` required and optional keys, method binding, and return types remain
visible to static tooling. Runtime validation remains authoritative.

## Methods and descriptors

Ordinary synchronous and asynchronous instance methods use Python's normal
function descriptor. Talea waits
until class ownership is established, classifies the first parameter as the
receiver, compiles the method, and leaves the class with a normal generated
function descriptor. Python supplies `self` exactly once. The receiver is
binding infrastructure, so it does not require or receive Talea value
validation; every other parameter and the return still do. This exemption
cannot make an unannotated first parameter on an ordinary function valid.

Class and static methods require `validate_call` as the outer decorator:

```python
class Service:
    @validate_call
    @classmethod
    async def create(cls, value: int) -> int:
        return value

    @validate_call
    @staticmethod
    async def normalize(value: int) -> int:
        return value
```

| Form | Policy |
| --- | --- |
| ordinary `@validate_call` instance method | supported; `self` is receiver |
| `@validate_call` outside `@classmethod` | supported; `cls` is receiver |
| `@classmethod` outside `@validate_call` | rejected; Talea must be outermost |
| `@validate_call` outside `@staticmethod` | supported; every parameter validates |
| `@staticmethod` outside `@validate_call` | rejected; Talea must be outermost |

This policy lets the descriptor itself establish class/static truth and avoids
a permanent method adapter or registry. Inheritance, overrides, `super()`, and
binding to an instance or subclass remain ordinary Python attribute
resolution. Validated methods can call each other or recurse, and argument
failure/body success/return failure retain the zero/one/one invocation rule.

`inspect.signature(Class.method)` reports the unbound instance signature,
including `self`; `inspect.signature(instance.method)` reflects Python's bound
signature without it. Classmethod access similarly removes `cls`, while a
staticmethod has no receiver to remove. Wrapper names, qualified names,
documentation, annotations, and `__wrapped__` remain available.

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
parameter and return schemas, required/default state, receiver flags, variadic
semantics (`items`, `values`, or `unpack_typed_dict`), and callable kind
(`function`, `instance_method`, `class_method`, or `static_method`).
`CallableInfo.is_async` projects the decoration-time coroutine-function
classification for the same contract; there is no async-specific inspection
API or projection type.
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

Async wrappers follow the same rule across concurrent tasks, reentrant awaits,
and recursive async calls. A validated async function may call a validated
sync function and vice versa using ordinary Python execution and event-loop
rules. Talea creates no task, `ContextVar`, coroutine registry, or shared
per-call state.

Generated identifiers are compiler-owned. Annotations, defaults, metadata,
function names, and callback objects are retained as values in the generated
function namespace rather than interpolated into source. Hostile reprs,
quotes, newlines, Unicode, and unusual qualified names cannot become code. A
wrapper naturally owns its contract and compiled globals for its own lifetime;
there is no process-global callable registry or cache.

Talea owns binding shape, generated-source safety, argument and return
enforcement, metadata, and Sensitive redaction. The application owns function
CPU, memory, I/O, locks, side effects, mutation, recursion depth, exceptions,
thread safety, tasks, cancellation policy, timeouts, and retries. `Sensitive`
redacts Talea-owned async argument and return failures, but cannot sanitize an
exception message emitted by application coroutine code.

## Complete callable surface and remaining limits

The complete synchronous and asynchronous Python binding surface is supported:
every fixed parameter kind, defaults, variadics, `Unpack[TypedDict]`, ordinary
methods, classmethods, staticmethods, strict argument and return validation,
typing, cancellation transparency, and immutable introspection. Generators,
async generators, and arbitrary callable objects are unsupported. Runtime
generic-function specialization remains unsupported, and a deferred annotation
name that was local to a scope already lost before decoration may be
unrecoverable. Callable boundaries remain strict: there is no coercion,
`ResourcePolicy`, function sandbox, framework/RPC adapter, timeout manager, or
streaming execution mode.

## Complete executable example

The payment-service example separates external construction from strict
callable validation and demonstrates synchronous binding plus an async
authorization service, constraints, nested Specs, keyword-only options, task
composition, cancellation, invalid parameter and awaited return values,
application exceptions, Sensitive redaction, `__wrapped__`, and immutable
introspection.

{!> ../../../docs_src/tutorials/callable_boundaries.py !}

## Performance evidence

`task benchmark_callables` compares direct calls, equivalent handwritten
strict wrappers, Talea wrappers, and `inspect.Signature.bind`; async timings
run repeated operations inside one already-running event loop. It measures
one, two, and five primitive arguments, every fixed binding form, variadic
0/1/5/20 scaling, scalar keyword scaling, small and larger `Unpack` structures,
instance/class/static methods, async task/gather/cancellation behavior,
failures, cold complex compilation, success/failure allocations, retained
memory, call counts, and bytecode. Sync wrappers contain no async branch; async
wrappers contain a direct application call and await with no execution-mode
dispatcher. Neither fixed warm path contains a generic binder, parameter loop,
Schema walk, registry lookup, or lock. Callable support adds no execution path
to Specs or Contracts that do not use the decorator.
