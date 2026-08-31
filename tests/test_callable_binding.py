"""Complete Python binding semantics for synchronous callable boundaries."""

from __future__ import annotations

import dis
import inspect
from typing import Annotated, NotRequired, ReadOnly, TypedDict, Unpack

import pytest

from talea import Alias, Representation, Sensitive, Spec, ValidationError, validate_call
from talea.introspection import ParameterInfo, inspect_callable
from talea.schema import PrimitiveSchema, TypedDictSchema


def test_fixed_parameter_kinds_and_defaults_share_native_binding() -> None:
    @validate_call
    def execute(
        account_id: int,
        /,
        quantity: int = 1,
        *,
        dry_run: bool = False,
        timeout: float,
    ) -> tuple[int, int, bool, float]:
        return account_id, quantity, dry_run, timeout

    assert execute(7, timeout=1.5) == (7, 1, False, 1.5)
    assert execute(7, 2, dry_run=True, timeout=2.0) == (7, 2, True, 2.0)
    assert inspect.signature(execute, follow_wrapped=False) == inspect.signature(execute)

    invalid_shapes = (
        lambda: execute(account_id=7, timeout=1.0),
        lambda: execute(timeout=1.0),
        lambda: execute(7),
        lambda: execute(7, 2, 3, timeout=1.0),
        lambda: execute(7, 2, quantity=3, timeout=1.0),
        lambda: execute(7, timeout=1.0, unknown=True),
    )
    for call in invalid_shapes:
        with pytest.raises(TypeError) as captured:
            call()
        assert type(captured.value) is TypeError

    with pytest.raises(ValidationError) as positional:
        execute("7", timeout=1.0)  # type: ignore[arg-type]
    assert positional.value.location == ("account_id",)

    with pytest.raises(ValidationError) as keyword:
        execute(7, timeout=1)  # type: ignore[arg-type]
    assert keyword.value.location == ("timeout",)


def test_keyword_only_mutable_defaults_revalidate_current_state() -> None:
    @validate_call
    def collect(*, values: list[int] = []) -> list[int]:  # noqa: B006
        return values

    assert collect() == []
    default = collect.__kwdefaults__["values"]
    default.append("bad")
    with pytest.raises(ValidationError) as captured:
        collect()
    assert captured.value.location == ("values", 0)


def test_variadic_positional_validates_every_item_in_order_without_normalization() -> None:
    seen: tuple[int, ...] | None = None

    @validate_call
    def total(*values: int) -> int:
        nonlocal seen
        seen = values
        return sum(values)

    assert total() == 0
    assert total(1) == 1
    assert total(*range(20)) == sum(range(20))
    assert seen == tuple(range(20))

    with pytest.raises(ValidationError) as first:
        total("bad", 1)  # type: ignore[arg-type]
    assert first.value.location == ("values", 0)
    with pytest.raises(ValidationError) as late:
        total(1, 2, "bad")  # type: ignore[arg-type]
    assert late.value.location == ("values", 2)


def test_variadic_positional_nested_strict_and_representation_semantics() -> None:
    class Item(Spec):
        values: list[int]

    class Money:
        pass

    loaded = False

    def load(value: str) -> Money:
        nonlocal loaded
        loaded = True
        return Money()

    type MoneyValue = Annotated[Money, Representation(input=str, load=load)]

    @validate_call
    def accept(*items: Item) -> int:
        return len(items)

    @validate_call
    def accept_money(*values: MoneyValue) -> int:
        return len(values)

    item = Item(values=[1])
    assert accept(item) == 1
    item.values.append("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as nested:
        accept(item)
    assert nested.value.location == ("items", 0, "values", 1)
    with pytest.raises(ValidationError):
        accept_money("1")  # type: ignore[arg-type]
    assert loaded is False


def test_scalar_variadic_keywords_validate_values_and_actual_names() -> None:
    seen: dict[str, int] | None = None

    @validate_call
    def priorities(**values: int) -> int:
        nonlocal seen
        seen = values
        return sum(values.values())

    assert priorities() == 0
    assert priorities(low=1) == 1
    assert priorities(**{f"k{index}": index for index in range(20)}) == sum(range(20))
    assert seen == {f"k{index}": index for index in range(20)}
    with pytest.raises(ValidationError) as first:
        priorities(bad="x", later=1)  # type: ignore[arg-type]
    assert first.value.location == ("values", "bad")
    with pytest.raises(ValidationError) as late:
        priorities(first=1, bad="x")  # type: ignore[arg-type]
    assert late.value.location == ("values", "bad")


def test_scalar_variadic_keywords_validate_nested_contracts() -> None:
    class Payload(TypedDict):
        count: int

    @validate_call
    def accept(**values: Payload) -> int:
        return len(values)

    assert accept(job={"count": 1}) == 1
    with pytest.raises(ValidationError) as captured:
        accept(job={"count": "bad"})  # type: ignore[typeddict-item]
    assert captured.value.location == ("values", "job", "count")


class Options(TypedDict):
    timeout: float
    trace_id: NotRequired[str]
    label: ReadOnly[NotRequired[str]]


class AliasOptions(TypedDict):
    internal: Annotated[int, Alias("external")]


class SecretOptions(TypedDict):
    token: Annotated[str, Sensitive()]


class GenericOptions[T](TypedDict):
    value: T
    note: NotRequired[str]


def test_unpack_typed_dict_uses_canonical_closed_shape_without_copy() -> None:
    received: dict[str, object] | None = None

    @validate_call
    def configure(**kwargs: Unpack[Options]) -> Options:
        nonlocal received
        received = kwargs
        return kwargs

    assert configure(timeout=1.5) == {"timeout": 1.5}
    assert configure(timeout=1.5, trace_id="abc", label="read-only-metadata") == {
        "timeout": 1.5,
        "trace_id": "abc",
        "label": "read-only-metadata",
    }
    assert received is not None

    with pytest.raises(ValidationError) as missing:
        configure()
    assert missing.value.location == ("kwargs", "timeout")
    with pytest.raises(ValidationError) as unexpected:
        configure(timeout=1.0, unknown=True)  # type: ignore[call-arg]
    assert unexpected.value.location == ("kwargs", "unknown")
    with pytest.raises(ValidationError) as wrong:
        configure(timeout=1)  # type: ignore[typeddict-item]
    assert wrong.value.location == ("kwargs", "timeout")


def test_unpack_generic_alias_and_sensitive_policy() -> None:
    @validate_call
    def generic(**kwargs: Unpack[GenericOptions[int]]) -> GenericOptions[int]:
        return kwargs

    @validate_call
    def aliased(**kwargs: Unpack[AliasOptions]) -> AliasOptions:
        return kwargs

    @validate_call
    def secret(**kwargs: Unpack[SecretOptions]) -> SecretOptions:
        return kwargs

    assert generic(value=1) == {"value": 1}
    assert aliased(internal=1) == {"internal": 1}
    with pytest.raises(ValidationError) as alias:
        aliased(internal=1, external=1)  # type: ignore[call-arg]
    assert alias.value.location == ("kwargs", "external")
    with pytest.raises(ValidationError) as sensitive:
        secret(token=123)  # type: ignore[typeddict-item]
    assert sensitive.value.errors()[0]["input"] == "<redacted>"
    assert sensitive.value.location == ("kwargs", "token")


def test_unpack_rejects_non_typed_dict_and_non_keyword_use() -> None:
    def scalar(**kwargs: Unpack[dict[str, int]]) -> int:
        return len(kwargs)

    def positional(value: Unpack[Options]) -> int:
        del value
        return 1

    with pytest.raises(TypeError, match="concrete TypedDict"):
        validate_call(scalar)
    with pytest.raises(TypeError, match="only for variadic keyword"):
        validate_call(positional)


def test_complete_binding_introspection_is_frozen_canonical_truth() -> None:
    @validate_call
    def boundary(head: int, /, *items: str, flag: bool = True, **options: int) -> int:
        return head + len(items) + flag + len(options)

    info = inspect_callable(boundary)
    assert info.callable_kind == "function"
    assert info.parameters == (
        ParameterInfo("head", "POSITIONAL_ONLY", PrimitiveSchema("int"), True, False),
        ParameterInfo("items", "VAR_POSITIONAL", PrimitiveSchema("str"), False, False, False, "items"),
        ParameterInfo("flag", "KEYWORD_ONLY", PrimitiveSchema("bool"), False, True),
        ParameterInfo("options", "VAR_KEYWORD", PrimitiveSchema("int"), False, False, False, "values"),
    )

    @validate_call
    def unpacked(**kwargs: Unpack[Options]) -> Options:
        return kwargs

    unpack_info = inspect_callable(unpacked).parameters[0]
    assert isinstance(unpack_info.schema, TypedDictSchema)
    assert unpack_info.variadic_semantics == "unpack_typed_dict"


def test_generated_complex_paths_have_no_runtime_binder_or_schema_walk() -> None:
    @validate_call
    def boundary(head: int, /, *items: int, flag: bool, **values: int) -> int:
        return head + sum(items) + flag + sum(values.values())

    instructions = tuple(dis.get_instructions(boundary))
    loaded = {
        instruction.argval
        for instruction in instructions
        if instruction.opname in {"LOAD_GLOBAL", "LOAD_ATTR", "LOAD_METHOD"}
    }
    assert "bind" not in loaded
    assert "Signature" not in loaded
    assert "Parameter" not in loaded
    assert "BoundArguments" not in loaded
    assert all("schema" not in str(name).lower() for name in loaded)
    assert sum(instruction.opname == "FOR_ITER" for instruction in instructions) == 2


def test_variadic_adversarial_scale_sensitive_values_and_hostile_nested_mapping() -> None:
    type SecretInt = Annotated[int, Sensitive()]

    @validate_call
    def positional(*values: SecretInt) -> int:
        return len(values)

    @validate_call
    def keywords(**values: SecretInt) -> int:
        return len(values)

    @validate_call
    def nested(**values: dict[str, int]) -> int:
        return len(values)

    class HostileMapping:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

    assert positional(*range(10_000)) == 10_000
    assert keywords(**{f"k{index}": index for index in range(2_000)}) == 2_000
    with pytest.raises(ValidationError) as positional_secret:
        positional("secret")  # type: ignore[arg-type]
    assert positional_secret.value.errors()[0]["input"] == "<redacted>"
    with pytest.raises(ValidationError) as keyword_secret:
        keywords(token="secret")  # type: ignore[arg-type]
    assert keyword_secret.value.errors()[0]["input"] == "<redacted>"
    with pytest.raises(ValidationError) as hostile:
        nested(payload=HostileMapping())  # type: ignore[arg-type]
    assert hostile.value.location == ("values", "payload")


def test_generated_signature_names_cannot_collide_with_compiler_identifiers() -> None:
    namespace: dict[str, object] = {"__name__": __name__}
    exec(
        "def operation(π: int, _talea_type_1: int, /) -> int:\n    return π + _talea_type_1",
        namespace,
    )
    operation = validate_call(namespace["operation"])  # type: ignore[arg-type]

    assert operation(1, 2) == 3
    with pytest.raises(ValidationError) as captured:
        operation("bad", 2)  # type: ignore[call-non-callable]
    assert captured.value.location == ("π",)
