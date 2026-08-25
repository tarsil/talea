from enum import Enum
from typing import Literal

import pytest

from talea import Spec
from talea.schema import AnnotationResolutionError, resolve_annotation
from talea.validation import ValidationError, compile_validator


class Mode(Enum):
    READ = "read"
    WRITE = "write"

    def __repr__(self) -> str:
        return "__import__('os').system('echo unsafe')"


@pytest.mark.parametrize(
    ("annotation", "accepted", "rejected"),
    [
        (Literal["open", "closed"], ("open", "closed"), ("other", b"open")),
        (Literal[b"ok", b"error"], (b"ok", b"error"), ("ok", b"other")),
        (Literal[1, 2, 3], (1, 2, 3), (True, 4)),
        (Literal[True], (True,), (1, False)),
        (Literal[None], (None,), (0, "None")),
        (Literal[Mode.READ, Mode.WRITE], (Mode.READ, Mode.WRITE), ("read",)),
    ],
)
def test_literal_values_use_strict_type_sensitive_semantics(
    annotation: object,
    accepted: tuple[object, ...],
    rejected: tuple[object, ...],
) -> None:
    validator = compile_validator(resolve_annotation(annotation))

    for value in accepted:
        assert validator(value) is value
    for value in rejected:
        with pytest.raises(ValidationError) as raised:
            validator(value)
        assert raised.value.code == "literal"


def test_literal_union_members_remain_canonical_and_validate_each_alternative() -> None:
    schema = resolve_annotation(Literal["left"] | Literal["right"])
    validator = compile_validator(schema)

    assert validator("left") == "left"
    assert validator("right") == "right"


def test_literal_composes_through_specs_containers_and_unions() -> None:
    class Command(Spec):
        operation: Literal["create", "delete"]
        flags: list[Literal[1, 2]]
        mode: Literal[Mode.READ] | None

    command = Command(operation="create", flags=[1, 2], mode=Mode.READ)

    assert command.operation == "create"
    with pytest.raises(ValidationError) as raised:
        Command(operation="create", flags=[True], mode=None)
    assert raised.value.location == ("flags", 0)


def test_literal_source_like_strings_and_unusual_enum_repr_are_bound_safely() -> None:
    source_like = "'); raise RuntimeError('unsafe') #"

    string_validator = compile_validator(resolve_annotation(Literal[source_like]))
    enum_validator = compile_validator(resolve_annotation(Literal[Mode.READ]))

    assert string_validator(source_like) == source_like
    assert enum_validator(Mode.READ) is Mode.READ
    assert source_like not in string_validator.__code__.co_names
    assert "__import__" not in enum_validator.__code__.co_names


@pytest.mark.parametrize("annotation", [Literal[1.5], Literal[()], Literal[object()]])
def test_unsupported_literal_categories_fail_during_resolution(annotation: object) -> None:
    with pytest.raises(AnnotationResolutionError):
        resolve_annotation(annotation)
