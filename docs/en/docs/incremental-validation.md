# Incremental Contract validation

`Contract(list[T])` validates or converts one materialized list as a single
container boundary. It is the right contract when list identity, complete
container shape, or one aggregate input operation matters. It is not a record
processor: the complete list already exists, outputs are retained together,
and one invalid member fails that container operation.

For generators, database cursors, event iterables, and large ETL jobs, retain
`Contract(T)` and consume items lazily:

```python
contract = Contract(Trade)

for trade in contract.iter_python(database_cursor()):
    persist(trade)
```

`iter_validate()` is the strict Python operation corresponding to
`validate()`. `iter_python()` is the external structural operation
corresponding to `from_python()`. Both return `Iterator[T]`; neither creates a
result list, error list, batch envelope, stream object, or second Contract.

## Laziness and retained compilation

Calling either method validates static controls and returns an iterator. It
does not call `iter(source)`, pull the first item, validate a value, copy the
source, or compile a Contract per item. Source access begins with `next()`.

The retained Contract's strict validator is reused directly. External Python
input compiles on the first consumed item and the Contract retains that
artifact. Every pulled item enters the selected operation exactly once. There
is no pre-validation, retry, or speculative operation outside normal Contract
behavior.

Each requested result pulls only enough input to yield the next valid `T` or
terminate according to failure policy. Under continuation, several invalid
items may be pulled before the next valid result exists; no rejected item or
error is retained by Talea after its callback returns.

## Strict and external Python modes

Use `iter_validate(source)` when the iterable should already contain valid
internal Python values. Identity-preserving behavior remains the same as
`validate()`: a valid Spec, dataclass, mutable container, TypedDict, or
represented internal value is returned under its existing strict semantics.
Representation loaders do not run.

Use `iter_python(source)` for external structural values. Mappings can become
Specs or dataclasses, TypedDicts are detached, aliases and tagged unions use
their normal input rules, and Representation loaders execute once. Primitive
semantics stay strict; this API does not add implicit coercion.

The optional `policy=ResourcePolicy(...)` on `iter_python()` applies
independently to every item. Depth is per item, nodes are per item, and
`max_errors` limits details collected inside one item. A record containing ten
field failures is still one invalid source item for stream accounting. Strict
`iter_validate()` does not acquire an external-input policy merely because
values arrive through an iterator.

## Indexed errors and fail-fast

Fail-fast is the default. The first invalid item raises its ordinary
`ValidationError` facts with one zero-based integer prefix:

```text
(4,)
(4, "amount")
(4, "items", 2)
```

Existing detail ordering, codes, expected/received facts, union branches,
related locations, truncation, Sensitive redaction, and permitted causes are
preserved by the canonical error-prefix owner. The rejected item is not added
merely to locate the error or passed as a separate callback argument. The source
is not read past the failing item.

## Explicit continuation

Continuation requires `on_error(index, error)`. Returning normally says that
the application handled that invalid record operationally and permits the
iterator to seek the next valid result:

```python
def report_invalid(index: int, error: ValidationError) -> None:
    metrics.increment("invalid_trade")
    logger.warning("trade %d: %s", index, error.errors())


for trade in contract.iter_python(cursor, on_error=report_invalid):
    persist(trade)
```

The explicit index avoids parsing locations for record identity. The callback
also receives the located canonical error, but no separate rejected-value
argument. Ordinary non-sensitive `ValidationError` facts remain available;
Sensitive input remains redacted. Talea calls the callback once per invalid
source item, not once per nested detail. There is no
silent skip option, implicit collection, `Result` value, retry, predicate,
global handler, or callback registry.

Callback exceptions propagate unchanged and stop iteration. Callbacks are
trusted synchronous application code and may perform I/O, retain the supplied
error, or reenter Talea; Talea holds no lock while invoking them. An event
consumer may report and continue, while a payment service may choose
fail-fast. That recovery decision belongs to the application.

## Stream limits

`ItemPolicy` is available from `talea.contract` and is immutable:

```python
from talea.contract import ItemPolicy

policy = ItemPolicy(max_items=1_000_000, max_invalid_items=100)
```

The defaults are finite and deliberately generous. `max_items` counts every
source item pulled, valid or invalid. The count is checked before validation
work starts for that item; the first item over the limit raises
`ResourceLimitError(code="items", limit=N, observed=N + 1)`. Talea must pull
that item to know the source continued, but does not validate it.

`max_invalid_items` counts invalid source items admitted to continuation. At
the first item over the limit, Talea raises
`ResourceLimitError(code="invalid_items", ...)` before calling `on_error`.
Resource failures are control failures, not ordinary item validation failures,
so continuation cannot suppress them.

Setting either dimension to `None` explicitly opts out of that bound. For an
infinite iterable, keep a finite `max_items` or guarantee consumer termination.
An invalid infinite iterable with continuation also needs a finite
`max_invalid_items`. Stream policy does not bound source I/O time, callback
work, or arbitrary application code.

## Source ownership and early termination

The caller owns iterable, generator, cursor, file, transaction, retry, and
lifetime policy. Talea owns item validation/conversion, index location,
failure policy, and its finite limits. Source `RuntimeError`, `OSError`, and
other application exceptions propagate unchanged because no value was
validated at that point.

Talea performs no lookahead, pre-count with `len()`, drain, retry, or automatic
close of an arbitrary source. Closing the returned generator stops Talea
consumption and does not swallow `GeneratorExit`; it does not proactively close
the caller's underlying generator. Use the cursor or transaction's context
manager when cleanup must be deterministic.

A re-iterable collection creates a fresh iterator on each invocation. Passing
the same one-shot iterator to two operations naturally shares caller-owned
state. Multiple operations from one Contract have independent indexes and
budgets. One iterator is not made thread-safe; separate iterators may be
consumed concurrently under normal Python rules.

## Memory, mutation, and performance

The iterator keeps its active source and current yielded value as ordinary
generator state. It does not retain prior successful outputs after advancement,
prior continued errors after callback return, or an accumulating history. The
source itself may retain values according to its implementation. If the
application stores outputs or errors, that retention is application-owned.

Strict mode validates mutable values in their current state. External mode
uses normal conversion and yields mutable outputs normally; neither snapshots
arbitrary items merely because consumption is incremental.

The permanent `benchmark_incremental` task measures 1/100/10,000-item
primitive paths, Specs, dataclasses, TypedDicts, Representations, tagged and
recursive values, indexed failures, sparse/dense continuation, limits,
infinite-source stopping, allocation, retention, concurrency, and ordinary
Contract/Spec canaries. Incremental overhead consists of generator iteration,
`enumerate`, counter checks, and the retained operation. External mode also
creates normal independent `ResourcePolicy` state per item. Results depend on
Python, hardware, source behavior, and item shape.

## Security and failure ownership

| Failure | Owner and behavior |
| --- | --- |
| invalid strict or converted item | located `ValidationError`; fail or explicit callback |
| per-item traversal/error budget | terminal `ResourceLimitError` from `ResourcePolicy` |
| stream item/invalid-item budget | terminal `ResourceLimitError` from `ItemPolicy` |
| source iterator failure | original application exception |
| callback failure | original application exception |
| Contract declaration/compilation | existing Contract declaration behavior |

`Sensitive` protects Talea-owned failure detail after index prefixing. It
cannot prevent the source or callback from logging raw application values.
Limits protect Talea-owned counts and validation traversal; they do not provide
a wall-clock timeout, source/callback sandbox, database cancellation, or async
cancellation boundary.

## Framing and current limitations

This owner consumes synchronous Python `Iterable` items. It does not decode
JSON strings or bytes and does not own UTF-8, BOM handling, blank lines,
malformed syntax, file framing, or line numbers. JSONL is a separate transport
and framing concern that can feed decoded Python values into this item owner;
no `iter_jsonl` placeholder or framing-error contract is implied here.

There is no `AsyncIterable` or async callback support, streaming
serialization, JSONL output, automatic retry, silent ignore mode, `Result`
API, wall-clock timeout, or source sandbox. Generic item indexes are always
zero-based.

## Complete executable trade workflow

This program covers strict and external modes, a generator cursor, fail-fast,
continuation, indexed and Sensitive errors, both stream limits, early stop,
source failure, and a memory-friendly persistence loop:

{!> ../../../docs_src/tutorials/incremental_validation.py !}
