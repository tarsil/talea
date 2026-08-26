from dataclasses import FrozenInstanceError
from typing import Annotated

import pytest

from talea import Alias, Contract, Ge, Spec, check, field, serialize
from talea.introspection import ContractInfo, FieldInfo, SpecInfo, inspect_contract, inspect_spec
from talea.schema import AliasSchema, ConstrainedSchema, PrimitiveSchema


def test_spec_introspection_projects_effective_immutable_field_truth() -> None:
    type Positive = Annotated[int, Ge(1)]

    class Base(Spec):
        id: Annotated[Positive, Alias("identifier")]
        active: bool = True
        tags: list[str] = field(default_factory=list)

    class User(Base):
        name: str

    info = inspect_spec(User)

    assert isinstance(info, SpecInfo)
    assert info.spec is User
    assert tuple(field.name for field in info.fields) == ("id", "active", "tags", "name")
    identifier = info.fields[0]
    assert isinstance(identifier, FieldInfo)
    assert identifier.required is True
    assert identifier.alias == "identifier"
    assert identifier.constraints == (Ge(1),)
    assert isinstance(identifier.schema, AliasSchema)
    assert isinstance(identifier.schema.schema, ConstrainedSchema)
    assert info.fields[1].has_static_default is True
    assert info.fields[1].default is True
    assert info.fields[2].default_factory is list
    assert info.fields[3].required is True
    assert info.permanently_trusted is False
    assert info.operations == (
        "strict_python",
        "external_python",
        "json_input",
        "python_output",
        "json_output",
    )


def test_spec_introspection_is_cached_and_cannot_mutate_canonical_truth() -> None:
    class Point(Spec):
        x: int

    first = inspect_spec(Point)
    second = inspect_spec(Point)

    assert first is second
    with pytest.raises(FrozenInstanceError):
        first.fields = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        first.fields[0].schema.kind = "str"  # type: ignore[misc]
    assert Point(x=1).x == 1


def test_spec_introspection_reports_hooks_serializers_generics_and_recursion() -> None:
    class Box[T](Spec):
        value: T

    class Node(Spec):
        value: int
        children: list["Node"]

        @check("value")
        def positive(value: int) -> None:
            if value < 0:
                raise ValueError

        @serialize("value")
        def text(value: int) -> str:
            return str(value)

    generic = inspect_spec(Box)
    concrete = inspect_spec(Box[int])
    recursive = inspect_spec(Node)

    assert len(generic.generic_parameters) == 1
    assert generic.fields[0].annotation is Box.__type_params__[0]
    assert generic.fields[0].schema is None
    assert generic.recursive is None
    assert concrete.generic_origin is Box
    assert concrete.generic_arguments == (int,)
    assert concrete.fields[0].schema == PrimitiveSchema("int")
    assert recursive.recursive is True
    assert recursive.hook_names == ("positive",)
    assert recursive.serializer_names == ("text",)


def test_contract_introspection_retains_annotation_and_canonical_schema_only() -> None:
    contract = Contract[list[int]](list[int])
    info = inspect_contract(contract)

    assert isinstance(info, ContractInfo)
    assert info.annotation == list[int]
    assert "strict_python" in info.operations
    assert not hasattr(info, "validator")
    assert not hasattr(info, "lock")

    type Positive = Annotated[int, Ge(1)]

    alias_info = inspect_contract(Contract[Positive](Positive))
    assert isinstance(alias_info.schema, AliasSchema)
    assert alias_info.schema.name == "Positive"


@pytest.mark.parametrize("value", [int, object(), 1])
def test_inspection_rejects_non_talea_values(value: object) -> None:
    with pytest.raises(TypeError, match="requires a Spec class"):
        inspect_spec(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="requires a Contract instance"):
        inspect_contract(value)  # type: ignore[arg-type]
