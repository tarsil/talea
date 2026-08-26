import re
from types import NoneType

import pytest

from talea import Spec
from talea.schema import (
    AnnotationResolutionError,
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    SequenceSchema,
    SpecReferenceSchema,
    UnionSchema,
    VariadicTupleSchema,
    resolve_annotation,
)


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (int, PrimitiveSchema("int")),
        (float, PrimitiveSchema("float")),
        (str, PrimitiveSchema("str")),
        (bool, PrimitiveSchema("bool")),
        (bytes, PrimitiveSchema("bytes")),
        (None, PrimitiveSchema("none")),
        (NoneType, PrimitiveSchema("none")),
    ],
)
def test_resolves_primitives(annotation: object, expected: PrimitiveSchema) -> None:
    assert resolve_annotation(annotation) == expected


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (list[int], SequenceSchema("list", PrimitiveSchema("int"))),
        (set[str], SequenceSchema("set", PrimitiveSchema("str"))),
        (
            frozenset[bytes],
            SequenceSchema("frozenset", PrimitiveSchema("bytes")),
        ),
        (
            dict[str, int],
            MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
        ),
    ],
)
def test_resolves_homogeneous_containers(annotation: object, expected: object) -> None:
    assert resolve_annotation(annotation) == expected


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (tuple[int, ...], VariadicTupleSchema(PrimitiveSchema("int"))),
        (
            tuple[int, str],
            FixedTupleSchema((PrimitiveSchema("int"), PrimitiveSchema("str"))),
        ),
        (
            tuple[tuple[int, ...], tuple[str, bytes]],
            FixedTupleSchema(
                (
                    VariadicTupleSchema(PrimitiveSchema("int")),
                    FixedTupleSchema((PrimitiveSchema("str"), PrimitiveSchema("bytes"))),
                )
            ),
        ),
    ],
)
def test_resolves_tuples(annotation: object, expected: object) -> None:
    assert resolve_annotation(annotation) == expected


@pytest.mark.parametrize(
    ("annotation", "options"),
    [
        (int | None, frozenset({PrimitiveSchema("int"), PrimitiveSchema("none")})),
        (int | str, frozenset({PrimitiveSchema("int"), PrimitiveSchema("str")})),
        (
            int | str | None,
            frozenset({PrimitiveSchema("int"), PrimitiveSchema("str"), PrimitiveSchema("none")}),
        ),
    ],
)
def test_resolves_unions(annotation: object, options: frozenset[object]) -> None:
    assert resolve_annotation(annotation) == UnionSchema(options)


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (
            list[dict[str, int]],
            SequenceSchema(
                "list",
                MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
            ),
        ),
        (
            dict[str, list[int]],
            MappingSchema(
                PrimitiveSchema("str"),
                SequenceSchema("list", PrimitiveSchema("int")),
            ),
        ),
        (
            list[int | None],
            SequenceSchema(
                "list",
                UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("none")})),
            ),
        ),
        (
            tuple[list[int], dict[str, bytes]],
            FixedTupleSchema(
                (
                    SequenceSchema("list", PrimitiveSchema("int")),
                    MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("bytes")),
                )
            ),
        ),
    ],
)
def test_normalizes_nested_annotations(annotation: object, expected: object) -> None:
    assert resolve_annotation(annotation) == expected


def test_equivalent_annotations_have_equal_canonical_values() -> None:
    assert resolve_annotation(None) == resolve_annotation(NoneType)
    assert resolve_annotation(int | str) == resolve_annotation(str | int)
    assert resolve_annotation(list[int | None]) == resolve_annotation(list[None | int])


def test_resolves_spec_references_through_supported_compositions() -> None:
    class Address(Spec):
        city: str

    class User(Spec):
        address: Address

    address = SpecReferenceSchema(Address)
    user = SpecReferenceSchema(User)

    assert resolve_annotation(Address) == address
    assert resolve_annotation(list[User]) == SequenceSchema("list", user)
    assert resolve_annotation(User | None) == UnionSchema(frozenset({user, PrimitiveSchema("none")}))
    assert resolve_annotation(tuple[User, Address]) == FixedTupleSchema((user, address))


@pytest.mark.parametrize(
    ("annotation", "unresolved"),
    [
        (complex, complex),
        (list, list),
        (list[complex], complex),
        (dict[str], dict[str]),
        (tuple[()], tuple[()]),
        (tuple[int, str, ...], tuple[int, str, ...]),
    ],
)
def test_rejects_unsupported_annotations(annotation: object, unresolved: object) -> None:
    with pytest.raises(
        AnnotationResolutionError,
        match=f"^{re.escape(f'Unsupported annotation: {unresolved!r}')}$",
    ) as raised:
        resolve_annotation(annotation)

    assert raised.value.annotation == unresolved


def test_nested_failure_identifies_the_unsupported_leaf() -> None:
    with pytest.raises(AnnotationResolutionError) as raised:
        resolve_annotation(list[dict[str, complex]])

    assert raised.value.annotation is complex
    assert str(raised.value) == "Unsupported annotation: <class 'complex'>"
