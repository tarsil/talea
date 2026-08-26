from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from gc import collect
from pickle import dumps, loads
from threading import Barrier
from typing import Annotated, Any, List
from weakref import ref

import pytest
from hypothesis import given, strategies as st

from talea import Alias, Ge, SerializationError, Spec, ValidationError, check, field, serialize
from talea.schema import AnnotationResolutionError, SpecReferenceSchema


class PickleModel(Spec):
    value: int


class PickleBox[T](Spec):
    value: T


def test_forward_references_finalize_once_with_owner_field_errors() -> None:
    class Employee(Spec):
        manager: "Manager | None"

    class Manager(Spec):
        name: str

    manager = Manager(name="Ada")
    employee = Employee(manager=manager)

    assert employee.manager is manager
    assert isinstance(vars(Employee)["__talea_artifacts__"].schema.fields[0].schema, SpecReferenceSchema) is False

    class Broken(Spec):
        value: "MissingContract"  # noqa: F821

    with pytest.raises(
        AnnotationResolutionError,
        match=r"cannot resolve 'MissingContract'.*Broken\.value",
    ):
        Broken(value=object())


def test_local_mutual_quoted_references_resolve_without_a_registry() -> None:
    class A(Spec):
        b: "B | None"

    class B(Spec):
        a: "A | None"

    b = B(a=None)
    a = A(b=b)

    assert a.b is b
    assert b.a is None
    assert vars(A)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert vars(B)["__talea_artifacts__"].schema.instances_are_permanently_trusted


def test_recursive_spec_construction_boundaries_round_trip_and_locate_errors() -> None:
    class Node(Spec):
        value: int
        children: list[Node]

    data = {
        "value": 1,
        "children": [
            {"value": 2, "children": []},
            {"value": 3, "children": [{"value": 4, "children": []}]},
        ],
    }
    node = Node.from_mapping(data)

    assert node.to_dict() == data
    assert Node.from_mapping(node.to_dict()).to_dict() == data
    assert Node.from_json(node.to_json()).to_dict() == data
    assert not vars(Node)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    with pytest.raises(ValidationError) as raised:
        Node.from_mapping(
            {
                "value": 1,
                "children": [{"value": 2, "children": [{"value": "bad", "children": []}]}],
            }
        )
    assert raised.value.location == ("children", 0, "children", 0, "value")


@given(
    st.recursive(
        st.integers().map(lambda value: {"value": value, "children": []}),
        lambda children: st.builds(
            lambda value, nested: {"value": value, "children": nested},
            st.integers(),
            st.lists(children, max_size=3),
        ),
        max_leaves=20,
    )
)
def test_finite_recursive_trees_round_trip(data: dict[str, object]) -> None:
    class Node(Spec):
        value: int
        children: list[Node]

    assert Node.from_json(Node.from_mapping(data).to_json()).to_dict() == data


def test_recursive_containers_and_annotated_aliases_keep_canonical_truth() -> None:
    class Link(Spec):
        name: str
        next: Annotated["Link | None", Alias("nextLink")]
        lookup: dict[str, "Link"]
        lineage: tuple["Link", ...]

    leaf = Link(name="leaf", next=None, lookup={}, lineage=())
    root = Link(name="root", next=leaf, lookup={"leaf": leaf}, lineage=(leaf,))

    assert root.to_dict()["nextLink"] == {"name": "leaf", "nextLink": None, "lookup": {}, "lineage": ()}
    restored = Link.from_mapping(root.to_dict())
    assert restored.next is not None and restored.next.name == "leaf"

    class Tagged(Spec):
        next: Annotated["Tagged | None", "recursive"]

    assert Tagged(next=None).next is None

    class ExplicitMetadata(Spec):
        value: "Annotated[int, 'metadata']"

    assert ExplicitMetadata(value=1).value == 1


def test_runtime_cycle_policy_is_safe_for_validation_input_and_output() -> None:
    class Node(Spec):
        value: int
        children: list[Node]

    class Holder(Spec):
        node: Node

    node = Node(value=1, children=[])
    node.children.append(node)

    assert Holder(node=node).node is node
    for operation in (node.to_dict, node.to_json):
        with pytest.raises(SerializationError, match="cyclic object graphs") as raised:
            operation()
        assert raised.value.location == ("children", 0)

    mapping: dict[str, object] = {"value": 1}
    mapping["children"] = [mapping]
    with pytest.raises(ValidationError) as raised_input:
        Node.from_mapping(mapping)
    assert raised_input.value.code == "cycle"
    assert raised_input.value.location == ("children", 0)
    assert "Cyclic input is not supported" in str(raised_input.value)

    assert Node(value=1, children=[]).to_dict(include={"value"}) == {"value": 1}
    assert Node(value=1, children=[]).to_json(exclude={"children"}) == '{"value":1}'


def test_mutable_nested_specs_revalidate_current_fields_and_hooks() -> None:
    class Ledger(Spec):
        entries: list[int]
        total: int
        children: list[Ledger]

        @check("entries")
        def nonempty(entries: list[int]) -> None:
            if not entries:
                raise ValueError("entries cannot be empty")

        @check("entries", "total")
        def matches(entries: list[int], total: int) -> None:
            if sum(entries) != total:
                raise ValueError("total does not match")

    class Account(Spec):
        ledger: Ledger

    ledger = Ledger(entries=[1], total=1, children=[])
    ledger.entries.append(2)
    with pytest.raises(ValidationError, match="matches"):
        Account(ledger=ledger)
    ledger.entries.clear()
    with pytest.raises(ValidationError, match="nonempty"):
        Account(ledger=ledger)


def test_generic_specialization_is_concrete_cached_and_strict() -> None:
    class Box[T](Spec):
        value: T

    integer_box = Box[int]
    string_box = Box[str]

    assert integer_box is Box[int]
    assert string_box is Box[str]
    assert integer_box is not string_box
    assert integer_box(value=1).value == 1
    assert string_box(value="x").value == "x"
    assert integer_box.from_mapping({"value": 2}).value == 2
    assert string_box.from_json('{"value":"y"}').value == "y"
    with pytest.raises(ValidationError):
        integer_box(value="1")
    with pytest.raises(TypeError, match="requires concrete specialization"):
        Box(value=1)


def test_nested_generic_specs_and_recursive_generic_specs_compile_concretely() -> None:
    class Page[T](Spec):
        items: list[T]

    class Response[T](Spec):
        page: Page[T]

    class Tree[T](Spec):
        value: T
        children: list[Tree[T]]

    response = Response[int].from_mapping({"page": {"items": [1, 2]}})
    tree = Tree[str].from_mapping({"value": "root", "children": [{"value": "leaf", "children": []}]})

    assert type(response.page) is Page[int]
    assert response.to_dict() == {"page": {"items": [1, 2]}}
    assert tree.to_dict() == {
        "value": "root",
        "children": [{"value": "leaf", "children": []}],
    }
    assert Tree[str] is type(tree)


def test_local_generic_forward_references_resolve_and_release_broad_scope() -> None:
    unrelated = object()

    class Envelope[T](Spec):
        value: "Payload[T]"

    class Payload[T](Spec):
        item: T

    envelope = Envelope[int](value=Payload[int](item=1))
    retained = vars(Envelope)["__talea_declaration__"].local_namespace

    assert envelope.to_dict() == {"value": {"item": 1}}
    assert retained is not None and retained["Payload"] is Payload
    assert unrelated not in retained.values()


def test_generic_inheritance_bounds_constraints_and_parameter_defaults() -> None:
    class Entity(Spec):
        identifier: int

    class User(Entity):
        name: str

    class Other(Spec):
        value: int

    class Bounded[T: Entity](Spec):
        value: T

    class Base[T](Spec):
        value: T

    class Child[T](Base[T]):
        label: str

    class IntegerChild(Base[int]):
        label: str

    class Defaulted[T = int](Spec):
        value: T

    assert Bounded[User](value=User(identifier=1, name="Ada")).value.name == "Ada"
    with pytest.raises(TypeError, match="violates bound"):
        Bounded[Other]
    assert Child[str](value="x", label="label").value == "x"
    assert IntegerChild(value=1, label="label").value == 1
    assert Defaulted[()](value=1).value == 1
    assert Defaulted[()] is Defaulted[int]


def test_partial_generic_binding_and_specialization_arity_are_explicit() -> None:
    class Pair[Left, Right](Spec):
        left: Left
        right: Right

    left_parameter = Pair.__type_params__[0]
    partial = Pair[left_parameter, int]

    assert partial[str] is Pair[str, int]
    assert partial[str](left="x", right=1).right == 1
    with pytest.raises(TypeError, match="expects 2 type arguments"):
        Pair[int]
    with pytest.raises(TypeError, match="expects 2 type arguments"):
        Pair[int, str, bytes]
    with pytest.raises(TypeError, match="expects 1 type arguments"):
        partial[str, bytes]
    with pytest.raises(TypeError, match="not a generic Spec"):
        Pair[int, str][bytes]

    class Box[Value](Spec):
        value: Value

    class Wrapper[Value](Spec):
        value: Value

    wrapper_parameter = Wrapper.__type_params__[0]
    nested_partial = Wrapper[Box[wrapper_parameter]]
    nested = nested_partial[int](value=Box[int](value=1))
    assert type(nested.value) is Box[int]


def test_generic_constraints_and_annotation_forms_substitute_before_resolution() -> None:
    class Choice[T: (int, str)](Spec):
        value: T

    class Positive[T](Spec):
        value: Annotated[T, Ge(0)]

    class OptionalValue[T](Spec):
        value: T | None

    assert Choice[int](value=1).value == 1
    with pytest.raises(TypeError, match="violates constraints"):
        Choice[bytes]
    with pytest.raises(ValidationError):
        Positive[int](value=-1)
    assert OptionalValue[str](value=None).value is None

    class Legacy[T](Spec):
        value: List[T]

    with pytest.raises(AnnotationResolutionError):
        Legacy[int]

    class Unsupported[T](Spec):
        value: tuple[T, Any]

    with pytest.raises(AnnotationResolutionError):
        Unsupported[int]


def test_generic_declaration_rejections_and_pending_base_resolution() -> None:
    class Concrete(Spec):
        value: int

    with pytest.raises(TypeError, match="not a generic Spec"):
        Concrete[int]

    with pytest.raises(TypeError, match="TypeVar parameters only"):

        class Variadic[*Items](Spec):
            value: int

    with pytest.raises(TypeError, match="TypeVar parameters only"):

        class Parameters[**Arguments](Spec):
            value: int

    class Base[T](Spec):
        value: T

    with pytest.raises(TypeError, match="unspecialized generic base"):

        class InvalidConcrete(Base):
            label: str

    class Pending(Spec):
        later: "Later"

    class Later(Spec):
        value: int

    class ResolvedChild(Pending):
        label: str

    assert ResolvedChild(later=Later(value=1), label="ok").later.value == 1


def test_generic_defaults_factories_hooks_and_serializers_bind_after_specialization() -> None:
    class Default[T](Spec):
        value: T = 1

    class Produced[T](Spec):
        value: T = field(default_factory=lambda: 1)

        @check("value")
        def positive(value: T) -> None:
            if value <= 0:  # type: ignore[operator]
                raise ValueError("positive")

        @serialize("value")
        def output(value: T) -> str:
            return str(value)

    assert Default[int]().value == 1
    with pytest.raises(ValidationError):
        Default[str]
    produced = Produced[int]()
    assert produced.value == 1
    assert produced.to_dict() == {"value": "1"}


def test_concurrent_first_resolution_and_specialization_publish_once() -> None:
    class Node(Spec):
        value: int
        children: list[Node]

    class Box[T](Spec):
        value: T

    class Deferred(Spec):
        later: "Later"

    class Later(Spec):
        value: int

    workers = 8
    barrier = Barrier(workers)

    def construct(index: int) -> tuple[type[object], int, int]:
        barrier.wait()
        specialized = Box[int]
        node = Node(value=index, children=[])
        deferred = Deferred(later=Later(value=index))
        return specialized, node.value, deferred.later.value

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(construct, range(workers)))

    assert {specialized for specialized, _, _ in results} == {Box[int]}
    assert sorted(value for _, value, _ in results) == list(range(workers))
    assert sorted(value for _, _, value in results) == list(range(workers))


def test_explicit_forward_strings_cannot_execute_annotation_calls() -> None:
    with pytest.raises(AnnotationResolutionError):

        class Unsafe(Spec):
            value: "__import__('os').system('echo unsafe')"

    with pytest.raises(AnnotationResolutionError):

        class InvalidSyntax(Spec):
            value: "list["  # noqa: F722

    effects: list[str] = []

    def mark() -> type[int]:
        effects.append("called")
        return int

    class GenericUnsafe[T](Spec):
        value: "mark()"

    with pytest.raises(AnnotationResolutionError):
        GenericUnsafe[int]
    assert effects == []

    class GenericBroken[T](Spec):
        value: "StillMissing"  # noqa: F821

    with pytest.raises(AnnotationResolutionError):
        GenericBroken[int]


def test_recursive_and_generic_instances_preserve_normal_copy_behavior() -> None:
    class Box[T](Spec):
        value: T

    class Node(Spec):
        value: int
        children: list[Node]

    box = Box[int](value=1)
    node = Node(value=1, children=[Node(value=2, children=[])])

    assert copy(box) is not box and copy(box).value == 1
    cloned = deepcopy(node)
    assert cloned is not node and cloned.to_dict() == node.to_dict()
    node.children.append(node)
    cyclic_clone = deepcopy(node)
    assert cyclic_clone.children[-1] is cyclic_clone

    restored_model = loads(dumps(PickleModel(value=1)))
    restored_box = loads(dumps(PickleBox[int](value=1)))
    assert type(restored_model) is PickleModel and restored_model.value == 1
    assert type(restored_box) is PickleBox[int] and restored_box.value == 1


def test_unused_generic_specializations_are_weakly_retained() -> None:
    class Box[T](Spec):
        value: T

    specialized = Box[bytes]
    specialized_reference = ref(specialized)
    del specialized
    collect()

    assert specialized_reference() is None


def test_ordinary_specs_retain_no_recursive_or_generic_execution_tax() -> None:
    class Point(Spec):
        x: int
        y: int

    artifacts = vars(Point)["__talea_artifacts__"]

    assert artifacts.current_validator is None
    assert not artifacts.inputs.recursive
    assert not artifacts.outputs.recursive
    assert not vars(Point)["__talea_declaration__"].type_params
    assert Point(x=1, y=2).x == 1
