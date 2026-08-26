import builtins
import inspect

import pytest

import talea.schema.resolution as annotation_resolution
import talea.spec.declaration as spec_module
import talea.validation.emission as validation_emission
from talea import Spec, field
from talea.schema import PrimitiveSchema
from talea.validation import ValidationError


def test_direct_spec_composition_is_nominal_identity_preserving_and_not_deeply_revalidated() -> None:
    class Address(Spec):
        city: str
        postcode: str

    UserWithAddress = type(
        "UserWithAddress",
        (Spec,),
        {"__annotations__": {"identifier": int, "address": Address}},
    )

    class InternationalAddress(Address):
        country: str

    class Unrelated(Spec):
        value: str

    address = Address(city="Zurich", postcode="8001")
    international = InternationalAddress(city="Geneva", postcode="1201", country="CH")
    user = UserWithAddress(identifier=1, address=address)

    assert user.address is address
    assert UserWithAddress(identifier=2, address=international).address is international
    assert "city" not in vars(UserWithAddress)["__init__"].__code__.co_names
    assert "postcode" not in vars(UserWithAddress)["__init__"].__code__.co_names
    with pytest.raises(ValidationError) as raised:
        UserWithAddress(identifier=3, address=Unrelated(value="wrong"))  # type: ignore[invalid-argument-type]

    assert raised.value.location == ("address",)
    assert raised.value.expected.endswith("Address")


def test_specs_compose_through_containers_unions_and_fixed_tuples() -> None:
    class Member(Spec):
        name: str

    class Address(Spec):
        city: str

    Team = type(
        "Team",
        (Spec,),
        {
            "__annotations__": {
                "members": list[Member],
                "lookup": dict[str, Member],
                "frozen": frozenset[Member],
                "pair": tuple[Member, Address],
                "optional": Member | None,
                "choice": Member | Address,
            }
        },
    )

    member = Member(name="Ada")
    address = Address(city="Zurich")
    team = Team(
        members=[member],
        lookup={"lead": member},
        frozen=frozenset({member}),
        pair=(member, address),
        optional=None,
        choice=address,
    )

    assert team.members[0] is member
    assert team.lookup["lead"] is member
    assert team.pair == (member, address)
    with pytest.raises(ValidationError) as raised:
        Team(
            members=[member, address],  # type: ignore[list-item]
            lookup={"lead": member},
            frozen=frozenset({member}),
            pair=(member, address),
            optional=member,
            choice=member,
        )

    assert raised.value.location == ("members", 1)
    assert raised.value.expected.endswith("Member")


def test_nested_spec_trust_boundaries_revalidate_only_nonpermanent_contracts() -> None:
    class Coordinates(Spec):
        x: int
        y: int

    Location = type(
        "Location",
        (Spec,),
        {"__annotations__": {"coordinates": Coordinates}},
    )

    class Basket(Spec):
        items: list[int]

    Order = type("Order", (Spec,), {"__annotations__": {"basket": Basket}})

    class SampledCoordinates(Coordinates):
        samples: list[int]

    assert vars(Coordinates)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert vars(Location)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert not vars(Basket)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert not vars(Order)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    basket = Basket(items=[1])
    first = Order(basket=basket)
    assert first.basket is basket
    basket.items.append("invalid")  # type: ignore[invalid-argument-type]
    with pytest.raises(ValidationError) as raised:
        Order(basket=basket)
    assert raised.value.location == ("basket", "items", 1)
    basket.items.pop()
    corrected = Order(basket=basket)
    assert corrected.basket is basket
    sampled = SampledCoordinates(x=1, y=2, samples=[3])
    sampled.samples.append("invalid")  # type: ignore[invalid-argument-type]
    assert Location(coordinates=sampled).coordinates is sampled
    assert "items" in vars(Order)["__init__"].__code__.co_names
    assert not {"x", "y", "samples"} & set(vars(Location)["__init__"].__code__.co_names)


def test_mutable_nested_revalidation_composes_deep_container_locations() -> None:
    class Basket(Spec):
        items: list[int]

    BasketShipment = type(
        "BasketShipment",
        (Spec,),
        {"__annotations__": {"basket": Basket}},
    )
    Manifest = type(
        "Manifest",
        (Spec,),
        {"__annotations__": {"shipments": list[BasketShipment]}},
    )
    valid = BasketShipment(basket=Basket(items=[1]))
    invalid_basket = Basket(items=[1, 2])
    invalid = BasketShipment(basket=invalid_basket)
    invalid_basket.items.append("invalid")  # type: ignore[invalid-argument-type]

    with pytest.raises(ValidationError) as raised:
        Manifest(shipments=[valid, invalid])

    assert raised.value.location == ("shipments", 1, "basket", "items", 2)


def test_mutable_nested_revalidation_uses_only_precompiled_canonical_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Basket(Spec):
        items: list[int]

    Order = type("Order", (Spec,), {"__annotations__": {"basket": Basket}})
    basket = Basket(items=[1])

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"runtime declaration work: {args!r}, {kwargs!r}")

    monkeypatch.setattr(Basket, "__init__", forbidden)
    monkeypatch.setattr(spec_module, "resolve_annotation", forbidden)
    monkeypatch.setattr(spec_module, "compile_validator", forbidden)
    monkeypatch.setattr(spec_module._ConstructorCompiler, "compile", forbidden)
    monkeypatch.setattr(spec_module, "get_type_hints", forbidden)
    monkeypatch.setattr(annotation_resolution, "get_origin", forbidden)
    monkeypatch.setattr(annotation_resolution, "get_args", forbidden)
    monkeypatch.setattr(validation_emission._ValidationEmitter, "emit_schema", forbidden)
    monkeypatch.setattr(builtins, "compile", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)

    order = Order(basket=basket)
    initializer = vars(Order)["__init__"]

    assert order.basket is basket
    assert initializer.__closure__ is None
    assert not any(
        getattr(type(value), "__module__", "").startswith("talea.schema") for value in initializer.__globals__.values()
    )


def test_single_inheritance_builds_one_ordered_effective_declaration_and_flat_constructor() -> None:
    class Person(Spec):
        name: str

        def describe(self) -> str:
            return self.name

    class Employee(Person):
        employee_id: int

        def describe(self) -> str:
            return f"{self.name}:{self.employee_id}"

        @property
        def label(self) -> str:
            return self.describe()

        @classmethod
        def kind(cls) -> str:
            return cls.__name__

        @staticmethod
        def category() -> str:
            return "staff"

    employee = Employee(name="Ada", employee_id=7)
    artifacts = vars(Employee)["__talea_artifacts__"]
    initializer = vars(Employee)["__init__"]

    assert tuple(field.name for field in artifacts.schema.fields) == ("name", "employee_id")
    assert str(inspect.signature(Employee)) == "(*, name, employee_id)"
    assert Employee.__slots__ == ("employee_id",)
    assert not hasattr(employee, "__dict__")
    assert (employee.name, employee.employee_id) == ("Ada", 7)
    assert employee.label == "Ada:7"
    assert Employee.kind() == "Employee"
    assert Employee.category() == "staff"
    assert vars(Person)["__init__"] not in initializer.__globals__.values()
    assert "__init__" not in initializer.__code__.co_names
    with pytest.raises(AttributeError, match="Employee instances are immutable"):
        employee.name = "Grace"
    with pytest.raises(AttributeError, match="Employee instances are immutable"):
        employee.employee_id = 8


def test_field_overrides_replace_semantics_in_place_across_all_default_states() -> None:
    def parent_factory() -> list[int]:
        return [1]

    def child_factory() -> tuple[int, ...]:
        return (2,)

    class Base(Spec):
        required: int | str
        defaulted: int | str = 1
        produced: tuple[int, ...] = field(default_factory=lambda: tuple(parent_factory()))
        becomes_default: str

    class Child(Base):
        required: str
        defaulted: str
        produced: tuple[int, ...]
        becomes_default: str = "child"

    class Defaults(Child):
        required: str = "default"
        defaulted: str = "new"
        produced: tuple[int, ...] = field(default_factory=child_factory)

    class StaticFactoryOverride(Defaults):
        produced: tuple[int, ...] = (3,)

    child_fields = vars(Child)["__talea_artifacts__"].schema.fields
    defaults_fields = vars(Defaults)["__talea_artifacts__"].schema.fields

    assert tuple(item.name for item in child_fields) == (
        "required",
        "defaulted",
        "produced",
        "becomes_default",
    )
    assert all(item.required for item in child_fields[:3])
    assert child_fields[0].schema == PrimitiveSchema("str")
    assert child_fields[1].schema == PrimitiveSchema("str")
    assert defaults_fields[0].default == "default"
    assert defaults_fields[1].default == "new"
    assert defaults_fields[2].default_factory is child_factory
    assert Defaults().produced == (2,)
    assert StaticFactoryOverride().produced == (3,)
    with pytest.raises(TypeError, match="required keyword-only argument: 'defaulted'"):
        Child(required="value", produced=(1,))  # type: ignore[missing-argument]


def test_inherited_defaults_and_factories_preserve_lifecycle_semantics() -> None:
    calls = 0

    def make_items() -> list[int]:
        nonlocal calls
        calls += 1
        return []

    class Base(Spec):
        active: bool = True
        items: list[int] = field(default_factory=make_items)

    class Child(Base):
        name: str

    assert calls == 0
    first = Child(name="Ada")
    second = Child(name="Grace", active=False)
    explicit: list[int] = [1]
    third = Child(name="Lin", items=explicit)
    first.items.append(2)

    assert calls == 2
    assert first.items == [2]
    assert second.items == []
    assert third.items is explicit
    assert str(inspect.signature(Child)) == "(*, active=True, items=<factory>, name)"


def test_incompatible_field_annotation_overrides_are_rejected() -> None:
    class PrimitiveBase(Spec):
        value: int

    class Person(Spec):
        name: str

    class Address(Spec):
        city: str

    for base, annotation in ((PrimitiveBase, str), (Person, Address)):
        with pytest.raises(TypeError, match="override is not type-compatible"):
            type(
                "InvalidOverride",
                (base,),
                {"__annotations__": {"value" if base is PrimitiveBase else "name": annotation}},
            )


def test_multiple_inheritance_supports_one_storage_lineage_diamonds_and_empty_mixins() -> None:
    class Root(Spec):
        root: int

    class Left(Root):
        left: str

    class Right(Root):
        def side(self) -> str:
            return "right"

    class Diamond(Left, Right):
        leaf: bool

    class Mixin:
        __slots__ = ()

        def mixed(self) -> str:
            return "mixed"

    class Mixed(Mixin, Diamond):
        note: str

    diamond = Diamond(root=1, left="left", leaf=True)
    mixed = Mixed(root=1, left="left", leaf=True, note="note")

    assert tuple(field.name for field in vars(Diamond)["__talea_artifacts__"].schema.fields) == (
        "root",
        "left",
        "leaf",
    )
    assert diamond.side() == "right"
    assert mixed.mixed() == "mixed"
    assert not hasattr(mixed, "__dict__")


def test_multiple_inheritance_rejects_incomparable_storage_and_stateful_mixins() -> None:
    class A(Spec):
        a: int

    class B(Spec):
        b: str

    with pytest.raises(TypeError, match="one state-bearing slot lineage"):
        type("Conflicted", (A, B), {"__annotations__": {"c": bool}})

    class Root(Spec):
        root: int

    class Left(Root):
        left: str

    class Right(Root):
        right: str

    with pytest.raises(TypeError, match="one state-bearing slot lineage"):
        type("ConflictedDiamond", (Left, Right), {})

    class DictMixin:
        pass

    class SlotMixin:
        __slots__ = ("state",)

    for mixin in (DictMixin, SlotMixin):
        with pytest.raises(TypeError, match="mixins must define empty slots"):
            type("StatefulMixed", (A, mixin), {})


def test_multiple_fieldless_spec_bases_and_nonconflicting_methods_remain_pythonic() -> None:
    class First(Spec):
        def first(self) -> int:
            return 1

    class Second(Spec):
        def second(self) -> int:
            return 2

    class Combined(First, Second):
        value: int

    combined = Combined(value=3)

    assert (combined.first(), combined.second(), combined.value) == (1, 2, 3)
    assert not hasattr(combined, "__dict__")


def test_inherited_field_and_lifecycle_collisions_are_rejected_deterministically() -> None:
    class Parent(Spec):
        value: int

    with pytest.raises(TypeError, match="cannot be replaced by a non-field attribute"):
        type("MethodCollision", (Parent,), {"value": lambda self: 1})

    class ShadowMixin:
        __slots__ = ()

        @property
        def value(self) -> int:
            return 1

    with pytest.raises(TypeError, match="conflicts with an inherited attribute"):
        type("MroCollision", (ShadowMixin, Parent), {})

    for lifecycle_name in ("__setattr__", "__delattr__"):
        with pytest.raises(TypeError, match="immutable field bindings"):
            type("LifecycleCollision", (Spec,), {lifecycle_name: lambda *args: None})
    for internal_name in ("__talea_artifacts__", "__talea_spec__"):
        with pytest.raises(TypeError, match="internal declaration state"):
            type("InternalCollision", (Spec,), {internal_name: object()})


def test_mutable_nested_spec_cannot_be_reused_as_a_static_default() -> None:
    class Basket(Spec):
        items: list[int]

    basket = Basket(items=[])

    with pytest.raises(TypeError, match="mutable static default"):
        type(
            "Order",
            (Spec,),
            {"__annotations__": {"basket": Basket}, "basket": basket},
        )


def test_unusual_spec_type_names_are_bound_safely_in_generated_constructors() -> None:
    unusual_name = "Quoted'\nSpec"
    unusual = type(unusual_name, (Spec,), {"__annotations__": {"value": int}})
    container = type("Container", (Spec,), {"__annotations__": {"nested": unusual}})
    value = unusual(value=1)

    assert container(nested=value).nested is value
    with pytest.raises(ValidationError) as raised:
        container(nested=object())

    assert raised.value.expected == unusual_name
