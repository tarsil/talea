from copy import replace
from typing import Annotated

import pytest

import talea
from talea import Alias, Ge, Spec, ValidationError, check, field, transform


def test_copy_replace_returns_a_validated_same_type_without_mutating_original() -> None:
    class User(Spec):
        id: int
        name: str

    original = User(id=1, name="Ada")
    replacement = replace(original, name="Grace")

    assert type(replacement) is User
    assert replacement.id == 1
    assert replacement.name == "Grace"
    assert original.name == "Ada"


def test_copy_replace_rejects_unknown_alias_and_invalid_values_atomically() -> None:
    class User(Spec):
        id: Annotated[int, Alias("identifier"), Ge(1)]

    original = User(id=1)

    with pytest.raises(TypeError, match="unexpected replacement field 'unknown'"):
        replace(original, unknown=2)
    with pytest.raises(TypeError, match="unexpected replacement field 'identifier'"):
        replace(original, identifier=2)
    with pytest.raises(ValidationError):
        replace(original, id=0)
    assert original.id == 1


def test_copy_replace_runs_changed_transforms_field_checks_and_whole_spec_checks() -> None:
    transformations = 0
    field_checks = 0
    spec_checks = 0

    class Range(Spec):
        start: int
        end: int

        @transform("start")
        def normalize(value: object) -> object:
            nonlocal transformations
            transformations += 1
            return int(value) if isinstance(value, str) else value

        @check("start")
        def nonnegative(start: int) -> None:
            nonlocal field_checks
            field_checks += 1
            if start < 0:
                raise ValueError

        @check("start", "end")
        def ordered(start: int, end: int) -> None:
            nonlocal spec_checks
            spec_checks += 1
            if start > end:
                raise ValueError

    original = Range(start=1, end=5)
    counts = (transformations, field_checks, spec_checks)
    changed = replace(original, start="2")  # type: ignore[arg-type]

    assert changed.start == 2
    assert (transformations, field_checks, spec_checks) == tuple(value + 1 for value in counts)
    with pytest.raises(ValidationError):
        replace(original, start=6)


def test_copy_replace_skips_unchanged_transforms_and_revalidates_mutable_state() -> None:
    transforms = 0
    item_checks = 0

    class Basket(Spec):
        name: str
        items: list[int]

        @transform("name")
        def normalize(value: object) -> object:
            nonlocal transforms
            transforms += 1
            return value

        @check("items")
        def populated(items: list[int]) -> None:
            nonlocal item_checks
            item_checks += 1
            if not items:
                raise ValueError

    items = [1]
    original = Basket(name="one", items=items)
    changed = replace(original, items=[2])

    assert transforms == 1
    assert item_checks == 2
    assert changed.items == [2]
    assert changed.name == "one"

    items.append("invalid")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        replace(original, name="two")


def test_copy_replace_shares_valid_unchanged_mutable_values_and_does_not_rerun_factories() -> None:
    factory_calls = 0

    def make_items() -> list[int]:
        nonlocal factory_calls
        factory_calls += 1
        return []

    class Basket(Spec):
        name: str
        items: list[int] = field(default_factory=make_items)

    original = Basket(name="one")
    changed = replace(original, name="two")

    assert changed.items is original.items
    assert factory_calls == 1


def test_copy_replace_does_not_route_through_public_mapping_or_output_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class User(Spec):
        id: int
        name: str

    original = User(id=1, name="Ada")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(User, "from_mapping", forbidden)
    monkeypatch.setattr(User, "to_dict", forbidden)

    assert replace(original, name="Grace").name == "Grace"


def test_copy_replace_supports_inherited_and_generic_specs() -> None:
    class Base(Spec):
        id: int

    class Box[T](Base):
        value: T

    original = Box[int](id=1, value=2)
    changed = replace(original, value=3)

    assert type(changed) is Box[int]
    assert changed.id == 1
    assert changed.value == 3


def test_replacement_artifact_is_lazy_and_standard_vocabulary_is_exclusive() -> None:
    class Point(Spec):
        x: int

    assert "__talea_replacer__" not in vars(Point)
    replace(Point(x=1), x=2)
    replacer = vars(Point)["__talea_replacer__"]
    replace(Point(x=2), x=3)

    assert vars(Point)["__talea_replacer__"] is replacer
    assert "replace" not in talea.__all__
    assert "evolve" not in talea.__all__
