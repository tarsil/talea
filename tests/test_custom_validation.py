import dis
import inspect
from dataclasses import FrozenInstanceError
from typing import Annotated, cast

import pytest

import talea
from talea import Ge, Spec, check, field, transform
from talea.declaration import SpecField, SpecSchema, ValidationHook
from talea.schema import PrimitiveSchema, SpecReferenceSchema
from talea.spec.construction import _ConstructorCompiler
from talea.validation import CustomValidationError, ValidationError, compile_validator


def test_public_vocabulary_preserves_strict_construction_without_a_transform() -> None:
    class StrictProduct(Spec):
        quantity: int

    class Product(Spec):
        quantity: int

        @transform("quantity")
        def parse_quantity(value: object) -> object:
            return int(value) if isinstance(value, str) else value

    with pytest.raises(ValidationError):
        StrictProduct(quantity="10")  # type: ignore[invalid-argument-type]

    product = Product(quantity="10")  # type: ignore[invalid-argument-type]
    assert product.quantity == 10
    assert Product.parse_quantity("11") == 11
    assert product.parse_quantity("12") == 12


def test_field_lifecycle_orders_transforms_structure_constraints_and_checks() -> None:
    events: list[str] = []

    class Quantity(Spec):
        value: Annotated[int, Ge(0)]

        @check("value")
        def positive(value: int) -> None:
            events.append(f"check:{value}")

        @transform("value")
        def strip(value: object) -> object:
            events.append(f"strip:{value}")
            return value.strip() if isinstance(value, str) else value

        @transform("value")
        def parse(value: object) -> object:
            events.append(f"parse:{value}")
            return int(value) if isinstance(value, str) else value

        @check("value")
        def even(value: int) -> None:
            events.append(f"even:{value}")
            if value % 2:
                raise ValueError("must be even")

    assert Quantity(value=" 4 ").value == 4  # type: ignore[invalid-argument-type]
    assert events == ["strip: 4 ", "parse:4", "check:4", "even:4"]

    events.clear()
    with pytest.raises(ValidationError) as structural:
        Quantity(value="-1")  # type: ignore[invalid-argument-type]
    assert structural.value.code == "greater_than_or_equal"
    assert events == ["strip:-1", "parse:-1"]


def test_transform_output_must_pass_the_shared_structural_validator() -> None:
    class BrokenTransform(Spec):
        value: int

        @transform("value")
        def broken(value: object) -> object:
            return str(value)

    with pytest.raises(ValidationError) as raised:
        BrokenTransform(value=1)

    assert raised.value.location == ("value",)
    assert raised.value.expected == "int"


def test_checks_are_exception_only_and_cannot_return_replacement_values() -> None:
    class InvalidCheck(Spec):
        value: int

        @check("value")
        def replace(value: int) -> None:
            return cast(None, value + 1)

    with pytest.raises(TypeError, match="validation check 'replace' must return None"):
        InvalidCheck(value=1)


def test_whole_spec_checks_receive_typed_locals_before_atomic_commit() -> None:
    observations: list[tuple[int, int]] = []

    class Interval(Spec):
        start: int
        end: int

        @check("start", "end")
        def ordered(start: int, end: int) -> None:
            observations.append((start, end))
            if end < start:
                raise ValueError("end precedes start")

    assert Interval(start=1, end=2).end == 2
    with pytest.raises(CustomValidationError) as raised:
        Interval(start=2, end=1)

    error = raised.value
    assert observations == [(1, 2), (2, 1)]
    assert error.stage == error.code == "spec_check"
    assert error.hook == "ordered"
    assert error.location == ()
    assert error.locations == (("start",), ("end",))
    assert isinstance(error.__cause__, ValueError)


def test_custom_failure_transport_is_field_located_and_preserves_value_error() -> None:
    transform_failure = ValueError("cannot parse")
    check_failure = ValueError("not accepted")

    class Payload(Spec):
        value: int

        @transform("value")
        def parse(value: object) -> object:
            if value == "bad":
                raise transform_failure
            return value

        @check("value")
        def accepted(value: int) -> None:
            if value == 0:
                raise check_failure

    with pytest.raises(CustomValidationError) as transformed:
        Payload(value="bad")  # type: ignore[invalid-argument-type]
    with pytest.raises(CustomValidationError) as checked:
        Payload(value=0)

    assert transformed.value.stage == transformed.value.code == "transform"
    assert transformed.value.hook == "parse"
    assert transformed.value.value == "bad"
    assert transformed.value.location == ("value",)
    assert transformed.value.locations == (("value",),)
    assert transformed.value.__cause__ is transform_failure
    assert checked.value.stage == checked.value.code == "field_check"
    assert checked.value.hook == "accepted"
    assert checked.value.__cause__ is check_failure


@pytest.mark.parametrize(
    "exception",
    [TypeError("bug"), RuntimeError("bug"), KeyboardInterrupt(), SystemExit()],
)
def test_unexpected_hook_exceptions_propagate_unchanged(exception: BaseException) -> None:
    class Broken(Spec):
        value: int

        @check("value")
        def fail(value: int) -> None:
            raise exception

    with pytest.raises(type(exception)) as raised:
        Broken(value=1)
    assert raised.value is exception


def test_static_defaults_skip_transforms_but_run_field_checks_at_declaration() -> None:
    calls: list[object] = []

    class Defaulted(Spec):
        value: int = 1

        @transform("value")
        def parse(value: object) -> object:
            calls.append(value)
            return int(value)

        @check("value")
        def positive(value: int) -> None:
            if value <= 0:
                raise ValueError("positive")

    assert calls == []
    declared_default = vars(Defaulted)["__talea_artifacts__"].schema.fields[0].default
    generated_default = vars(Defaulted)["__init__"].__kwdefaults__["value"]
    assert str(inspect.signature(Defaulted)) == "(*, value=1)"
    assert inspect.signature(Defaulted).parameters["value"].default is declared_default
    assert repr(generated_default) == "1"
    assert Defaulted().value == 1
    assert calls == []
    assert Defaulted(value="2").value == 2  # type: ignore[invalid-argument-type]
    assert calls == ["2"]

    with pytest.raises(CustomValidationError) as raised:

        class InvalidDefault(Spec):
            value: int = 0

            @check("value")
            def positive(value: int) -> None:
                raise ValueError("positive")

    assert raised.value.location == ("value",)


def test_default_factory_outputs_run_the_complete_field_pipeline() -> None:
    events: list[str] = []

    def quantity() -> object:
        events.append("factory")
        return "3"

    class Product(Spec):
        value: int = field(default_factory=quantity)  # type: ignore[invalid-assignment]

        @transform("value")
        def parse(value: object) -> object:
            events.append("transform")
            return int(value)

        @check("value")
        def positive(value: int) -> None:
            events.append("check")

    assert Product().value == 3
    assert events == ["factory", "transform", "check"]


def test_hook_inheritance_uses_method_identity_and_deterministic_order() -> None:
    events: list[str] = []

    class Base(Spec):
        value: int | str

        @transform("value")
        def normalize(value: object) -> object:
            events.append("base normalize")
            return value.strip() if isinstance(value, str) else value

        @check("value")
        def base_check(value: int | str) -> None:
            events.append("base check")

    class Child(Base):
        value: int

        @transform("value")
        def normalize(value: object) -> object:
            events.append("child normalize")
            return int(value)

        @check("value")
        def child_check(value: int) -> None:
            events.append("child check")

    assert Child(value=" 4 ").value == 4  # type: ignore[invalid-argument-type]
    assert events == ["child normalize", "base check", "child check"]

    class Removed(Child):
        def base_check(value: int) -> None:
            events.append("ordinary method")

    events.clear()
    assert Removed(value="5").value == 5  # type: ignore[invalid-argument-type]
    assert events == ["child normalize", "child check"]


def test_diamond_inheritance_executes_each_hook_once() -> None:
    calls = 0

    class Root(Spec):
        value: int

        @check("value")
        def count(value: int) -> None:
            nonlocal calls
            calls += 1

    class Left(Root):
        pass

    class Right(Root):
        pass

    class Diamond(Left, Right):
        pass

    assert Diamond(value=1).value == 1
    assert calls == 1


def test_non_spec_mixin_shadowing_follows_python_mro() -> None:
    events: list[str] = []

    class Base(Spec):
        value: int

        @check("value")
        def validate(value: int) -> None:
            events.append("hook")

    class Mixin:
        __slots__ = ()

        @staticmethod
        def validate(value: int) -> None:
            events.append("mixin")

    class Shadowed(Mixin, Base):
        pass

    class Retained(Base, Mixin):
        pass

    assert Shadowed(value=1).value == 1
    assert events == []
    assert Retained(value=1).value == 1
    assert events == ["hook"]

    class Root(Spec):
        value: int

    class HookedBranch(Root):
        @check("value")
        def validate(value: int) -> None:
            events.append("branch hook")

    class OrdinaryBranch(Root):
        @staticmethod
        def validate(value: int) -> None:
            events.append("ordinary branch")

    class SpecShadowed(OrdinaryBranch, HookedBranch):
        pass

    assert SpecShadowed(value=1).value == 1
    assert events == ["hook"]


def test_subclass_added_check_revalidates_an_inherited_static_default() -> None:
    class Base(Spec):
        value: int = 0

    with pytest.raises(CustomValidationError):

        class Child(Base):
            @check("value")
            def positive(value: int) -> None:
                raise ValueError("invalid inherited default")


def test_nested_custom_static_default_failure_retains_prefixed_transport() -> None:
    class Basket(Spec):
        items: list[int]

        @check("items")
        def nonempty(items: list[int]) -> None:
            if not items:
                raise ValueError("empty")

    basket = Basket(items=[1])
    basket.items.clear()

    with pytest.raises(CustomValidationError) as raised:
        type(
            "InvalidDefault",
            (Spec,),
            {"__annotations__": {"basket": Basket}, "basket": basket},
        )

    assert raised.value.hook == "nonempty"
    assert raised.value.locations == (("basket", "items"),)
    assert isinstance(raised.value.__cause__, ValueError)


def test_mutable_nested_current_state_revalidation_runs_field_and_spec_checks() -> None:
    transform_calls = 0

    class Range(Spec):
        start: list[int]
        end: list[int]

        @transform("start")
        def retain(value: object) -> object:
            nonlocal transform_calls
            transform_calls += 1
            return value

        @check("start")
        def nonempty(start: list[int]) -> None:
            if not start:
                raise ValueError("empty")

        @check("start", "end")
        def ordered(start: list[int], end: list[int]) -> None:
            if start and end and end[0] < start[0]:
                raise ValueError("unordered")

    class Container(Spec):
        value: Range

    value = Range(start=[1], end=[2])
    assert transform_calls == 1
    assert Container(value=value).value is value
    assert transform_calls == 1

    value.start.clear()
    with pytest.raises(CustomValidationError) as field_failure:
        Container(value=value)
    assert field_failure.value.locations == (("value", "start"),)

    value.start.append(3)
    with pytest.raises(CustomValidationError) as spec_failure:
        Container(value=value)
    assert spec_failure.value.locations == (("value", "start"), ("value", "end"))


def test_immutable_hooked_specs_remain_permanently_trusted() -> None:
    calls = 0

    class Positive(Spec):
        value: int

        @check("value")
        def positive(value: int) -> None:
            nonlocal calls
            calls += 1

    class Container(Spec):
        value: Positive

    positive = Positive(value=1)
    assert vars(Positive)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert Container(value=positive).value is positive
    assert calls == 1


def test_standalone_nested_validator_enforces_current_custom_contract() -> None:
    class Basket(Spec):
        items: list[int]

        @check("items")
        def nonempty(items: list[int]) -> None:
            if not items:
                raise ValueError("empty")

    basket = Basket(items=[1])
    basket.items.clear()
    validator = compile_validator(SpecReferenceSchema(Basket))

    with pytest.raises(CustomValidationError) as raised:
        validator(basket)
    assert raised.value.locations == (("items",),)


@pytest.mark.parametrize(
    "declaration",
    [
        lambda: transform(1),
        lambda: transform(""),
        lambda: check(),
        lambda: check("value", "value"),
        lambda: check("value", 1),
    ],
)
def test_decorator_targets_are_validated_immediately(declaration: object) -> None:
    with pytest.raises(TypeError):
        cast(object, declaration)()  # type: ignore[call-non-callable]


def test_invalid_hook_targets_and_callable_shapes_fail_at_declaration() -> None:
    with pytest.raises(TypeError, match="unknown field"):

        class Unknown(Spec):
            value: int

            @check("missing")
            def missing(missing: int) -> None:
                pass

    with pytest.raises(TypeError, match="requires exactly 1 positional parameter"):

        class InstanceMethod(Spec):
            value: int

            @check("value")
            def invalid(self, value: int) -> None:
                pass

    with pytest.raises(TypeError, match="requires exactly 2 positional parameters"):

        class WrongWholeSpecSignature(Spec):
            start: int
            end: int

            @check("start", "end")
            def invalid(start: int) -> None:
                pass

    with pytest.raises(TypeError, match="parameters must match"):

        class WrongParameterNames(Spec):
            start: int
            end: int

            @check("start", "end")
            def invalid(left: int, right: int) -> None:
                pass

    with pytest.raises(TypeError, match="conflicts with Spec field"):

        class FieldCollision(Spec):
            value: int

            @check("value")
            def value(value: int) -> None:
                pass

    def malformed(value: int) -> None:
        pass

    malformed.__talea_validation_hook__ = object()  # type: ignore[attr-defined]
    with pytest.raises(TypeError, match="metadata requires a plain function"):
        type("Malformed", (Spec,), {"__annotations__": {"value": int}, "malformed": malformed})


def test_descriptors_async_generators_and_duplicate_declarations_are_rejected() -> None:
    with pytest.raises(TypeError, match="plain function"):

        class DescriptorOutside(Spec):
            value: int

            @transform("value")
            @staticmethod
            def invalid(value: object) -> object:
                return value

    with pytest.raises(TypeError, match="cannot combine"):

        class DescriptorInside(Spec):
            value: int

            @staticmethod
            @transform("value")
            def invalid(value: object) -> object:
                return value

    with pytest.raises(TypeError, match="must be synchronous"):

        class AsyncHook(Spec):
            value: int

            @check("value")
            async def invalid(value: int) -> None:
                pass

    with pytest.raises(TypeError, match="cannot be a generator"):

        class GeneratorHook(Spec):
            value: int

            @check("value")
            def invalid(value: int):
                yield value

    with pytest.raises(TypeError, match="only one"):

        class Duplicate(Spec):
            value: int

            @check("value")
            @transform("value")
            def invalid(value: object) -> None:
                pass


def test_hook_declaration_truth_is_immutable_compact_and_canonical() -> None:
    def callback(value: int) -> None:
        pass

    hook = ValidationHook("accepted", "check", ("value",), callback)
    schema = SpecSchema((SpecField("value", PrimitiveSchema("int")),), (hook,))

    assert schema.hooks == (hook,)
    assert not hasattr(hook, "__dict__")
    with pytest.raises(FrozenInstanceError):
        hook.name = "other"
    with pytest.raises(ValueError, match="unique hook names"):
        SpecSchema(schema.fields, (hook, hook))
    with pytest.raises(ValueError, match="unique field targets"):
        ValidationHook("invalid", "check", ("value", "value"), callback)
    with pytest.raises(ValueError, match="exactly one"):
        ValidationHook("invalid", "transform", ("value", "other"), callback)
    with pytest.raises(ValueError, match="transform or check"):
        ValidationHook("invalid", cast(object, "other"), ("value",), callback)  # type: ignore[arg-type]

    class Canonical(Spec):
        value: int

        @check("value")
        def accepted(value: int) -> None:
            pass

    assert not hasattr(Canonical.accepted, "__talea_validation_hook__")


def test_static_default_check_must_return_none() -> None:
    with pytest.raises(TypeError, match="validation check 'invalid' must return None"):

        class Invalid(Spec):
            value: int = 1

            @check("value")
            def invalid(value: int) -> None:
                return cast(None, value)


def test_unhooked_constructor_pays_no_campaign_7_runtime_tax() -> None:
    class Point(Spec):
        x: int
        y: int

    initializer = vars(Point)["__init__"]
    names = set(initializer.__code__.co_names)
    opnames = {instruction.opname for instruction in dis.get_instructions(initializer)}

    assert names.isdisjoint({"hooks", "transform", "check", "registry", "dispatcher"})
    assert CustomValidationError not in initializer.__globals__.values()
    assert ValueError not in initializer.__globals__.values()
    assert "FOR_ITER" not in opnames
    assert Point(x=1, y=2).x == 1


def test_constructor_compiler_commits_no_slots_before_every_check_succeeds() -> None:
    writes: list[tuple[str, object]] = []

    def ordered(start: int, end: int) -> None:
        raise ValueError("invalid")

    schema = SpecSchema(
        (
            SpecField("start", PrimitiveSchema("int")),
            SpecField("end", PrimitiveSchema("int")),
        ),
        (ValidationHook("ordered", "check", ("start", "end"), ordered),),
    )
    initializer = _ConstructorCompiler().compile(
        schema,
        (
            lambda instance, value: writes.append(("start", value)),
            lambda instance, value: writes.append(("end", value)),
        ),
    )

    with pytest.raises(CustomValidationError):
        initializer(object(), start=1, end=2)
    assert writes == []


def test_root_package_exports_campaign_7_vocabulary() -> None:
    assert talea.check is check
    assert talea.transform is transform
