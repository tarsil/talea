import pickle
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy, replace
from dataclasses import dataclass
from typing import Annotated, Literal, cast

import pytest
from hypothesis import given, strategies as st

from talea import (
    Alias,
    Discriminator,
    ReadOnly,
    Sensitive,
    Spec,
    ValidationError,
    WriteOnly,
    apply_patch,
    check,
    create_spec,
    derive_spec,
    field,
    serialize,
    transform,
)
from talea.introspection import inspect_spec


class DirectionalPickleSource(Spec):
    identifier: Annotated[int, ReadOnly()]
    value: str
    secret: Annotated[str, WriteOnly()]


DirectionalPickleOutput = derive_spec(
    DirectionalPickleSource,
    mode="output",
    name="DirectionalPickleOutput",
    module=__name__,
)


def field_names(spec: type[Spec]) -> tuple[str, ...]:
    return tuple(field.name for field in inspect_spec(spec).fields)


def definition(document: dict[str, object], name: str) -> dict[str, object]:
    definitions = cast(dict[str, object], document["$defs"])
    return cast(dict[str, object], definitions[name])


def test_input_and_output_modes_select_normalized_directional_metadata() -> None:
    class User(Spec):
        identifier: Annotated[int, ReadOnly()]
        email: str
        password: Annotated[str, WriteOnly()]
        internal: Annotated[str, ReadOnly(), WriteOnly()]
        readable: Annotated[int, ReadOnly(False)]
        writable: Annotated[int, WriteOnly(False)]

    UserInput = derive_spec(User, mode="input")
    UserOutput = derive_spec(User, mode="output")

    assert UserInput.__name__ == "UserInput"
    assert UserOutput.__name__ == "UserOutput"
    assert field_names(UserInput) == ("email", "password", "readable", "writable")
    assert field_names(UserOutput) == ("identifier", "email", "readable", "writable")
    assert inspect_spec(UserInput).fields[1].write_only
    assert inspect_spec(UserOutput).fields[0].read_only
    assert UserInput(email="a@example.com", password="secret", readable=2, writable=1).to_dict() == {
        "email": "a@example.com",
        "password": "secret",
        "readable": 2,
        "writable": 1,
    }
    assert UserOutput(identifier=1, email="a@example.com", readable=2, writable=3).to_dict() == {
        "identifier": 1,
        "email": "a@example.com",
        "readable": 2,
        "writable": 3,
    }


def test_direction_composes_strictly_with_include_exclude_and_partial() -> None:
    class User(Spec):
        identifier: Annotated[int, ReadOnly()]
        email: str
        password: Annotated[str, WriteOnly()]
        enabled: bool = True

    Selected = derive_spec(User, mode="input", include=("email", "enabled"))
    Excluded = derive_spec(User, mode="output", exclude=("enabled",))
    Patch = derive_spec(User, mode="input", exclude=("password",), partial=True)

    assert field_names(Selected) == ("email", "enabled")
    assert field_names(Excluded) == ("identifier", "email")
    assert field_names(Patch) == ("email", "enabled")
    assert Patch.__name__ == "UserInputPartial"
    assert Patch().present_fields == frozenset()
    assert Patch(email="a@example.com").present_fields == frozenset({"email"})
    with pytest.raises(ValueError, match="'identifier' is excluded by input mode"):
        derive_spec(User, mode="input", include=("identifier", "email"))
    with pytest.raises(ValueError, match="'password' is excluded by output mode"):
        derive_spec(User, mode="output", include=("password",))


def test_direction_rejects_invalid_modes_and_preserves_no_metadata_identity_policy() -> None:
    class Source(Spec):
        value: int

    first = derive_spec(Source, mode="input")
    second = derive_spec(Source, mode="input")

    assert first is not Source
    assert first is not second
    assert field_names(first) == ("value",)
    assert inspect_spec(first).derivation is not None
    assert inspect_spec(first).derivation.mode == "input"
    with pytest.raises(ValueError, match="mode must be"):
        derive_spec(Source, mode="request")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="mode must be"):
        derive_spec(Source, mode=1)  # type: ignore[arg-type]


def test_aliases_cannot_admit_removed_input_fields_or_leak_removed_output_fields() -> None:
    class User(Spec):
        identifier: Annotated[int, Alias("id"), ReadOnly()]
        password: Annotated[str, Alias("pass-word"), WriteOnly()]
        name: Annotated[str, Alias("displayName")]

    UserInput = derive_spec(User, mode="input")
    UserOutput = derive_spec(User, mode="output")

    assert UserInput.from_mapping({"displayName": "Ada", "pass-word": "secret"}).to_json() == (
        '{"pass-word":"secret","displayName":"Ada"}'
    )
    assert UserInput.from_json('{"pass-word":"secret","displayName":"Ada"}').to_dict() == {
        "pass-word": "secret",
        "displayName": "Ada",
    }
    with pytest.raises(ValidationError) as canonical:
        UserInput.from_mapping({"identifier": 1, "displayName": "Ada", "pass-word": "secret"})
    with pytest.raises(ValidationError) as aliased:
        UserInput.from_json('{"id":1,"displayName":"Ada","pass-word":"secret"}')
    assert canonical.value.errors()[0]["code"] == "unexpected"
    assert aliased.value.errors()[0]["code"] == "unexpected"
    with pytest.raises(TypeError):
        UserInput(identifier=1, name="Ada", password="secret")
    output = UserOutput(identifier=1, name="Ada")
    assert output.to_dict() == {"id": 1, "displayName": "Ada"}
    assert "secret" not in output.to_json()
    assert "password" not in repr(output)
    with pytest.raises(TypeError):
        replace(output, password="secret")


def test_defaults_factories_hooks_serializers_and_sensitive_metadata_follow_retained_fields() -> None:
    calls: list[str] = []

    def read_only_factory() -> list[str]:
        calls.append("read-only factory")
        return []

    def retained_factory() -> list[str]:
        calls.append("retained factory")
        return []

    class Account(Spec):
        identifier: Annotated[list[str], ReadOnly()] = field(default_factory=read_only_factory)
        server_value: Annotated[int, ReadOnly()] = 9
        password: Annotated[str, WriteOnly(), Sensitive()]
        name: str = "Ada"
        tags: list[str] = field(default_factory=retained_factory)

        @transform("name")
        def strip_name(value: object) -> object:
            calls.append("transform")
            return value.strip() if isinstance(value, str) else value

        @check("name")
        def nonempty_name(name: str) -> None:
            calls.append("check")
            if not name:
                raise ValueError

        @check("identifier", "name")
        def coupled(identifier: list[str], name: str) -> None:
            calls.append("whole check")

        @serialize("password")
        def serialize_password(value: str) -> str:
            calls.append("password serializer")
            return value.upper()

    AccountInput = derive_spec(Account, mode="input")
    AccountOutput = derive_spec(Account, mode="output")

    assert "read-only factory" not in calls
    assert "retained factory" not in calls
    assert "password serializer" not in calls
    calls.clear()
    input_value = AccountInput(password="sentinel-secret", name=" Ada ")
    assert input_value.name == "Ada"
    assert calls == ["transform", "check", "retained factory"]
    assert "sentinel-secret" not in repr(input_value)
    assert input_value.to_dict()["password"] == "SENTINEL-SECRET"
    assert calls[-1] == "password serializer"
    output_value = AccountOutput()
    assert output_value.name == "Ada"
    assert output_value.identifier == []
    assert output_value.server_value == 9
    assert calls.count("read-only factory") == 1
    assert calls.count("password serializer") == 1
    assert "whole check" in calls
    assert "server_value" not in field_names(AccountInput)
    assert "password" not in field_names(AccountOutput)
    assert inspect_spec(AccountInput).fields[0].sensitive
    with pytest.raises(ValidationError) as raised:
        AccountInput(password=object())  # type: ignore[invalid-argument-type]
    assert "object" not in repr(raised.value.value)


def test_inherited_effective_metadata_and_explicit_false_are_canonical() -> None:
    class Base(Spec):
        identifier: Annotated[int, ReadOnly()]
        secret: Annotated[str, WriteOnly()]

    class Child(Base):
        identifier: Annotated[int, ReadOnly(False)]
        secret: Annotated[str, WriteOnly(False)]
        created: Annotated[int, ReadOnly()]

    assert field_names(derive_spec(Base, mode="input")) == ("secret",)
    assert field_names(derive_spec(Base, mode="output")) == ("identifier",)
    assert field_names(derive_spec(Child, mode="input")) == ("identifier", "secret")
    assert field_names(derive_spec(Child, mode="output")) == ("identifier", "secret", "created")


def test_direction_is_shallow_for_recursive_generic_tagged_and_dataclass_fields() -> None:
    @dataclass
    class Payload:
        value: int

    class User(Spec):
        identifier: Annotated[int, ReadOnly()]
        password: Annotated[str, WriteOnly()]

    class Node(Spec):
        user: User
        children: list[Node]

    class Box[T](Spec):
        value: T
        internal: Annotated[str, WriteOnly()]

    class Add(Spec):
        kind: Literal["add"]
        value: int

    class Remove(Spec):
        kind: Literal["remove"]
        value: int

    type Action = Annotated[Add | Remove, Discriminator("kind")]

    class Envelope(Spec):
        action: Action
        payload: Payload
        secret: Annotated[str, WriteOnly()]

    NodeOutput = derive_spec(Node, mode="output")
    BoxOutput = derive_spec(Box[int], mode="output")
    EnvelopeOutput = derive_spec(Envelope, mode="output")

    nested = User(identifier=1, password="nested-secret")
    node = NodeOutput(user=nested, children=[])
    assert node.user is nested
    assert node.to_dict()["user"] == {"identifier": 1, "password": "nested-secret"}
    assert BoxOutput(value=1).value == 1
    assert EnvelopeOutput.from_mapping(
        {"action": {"kind": "add", "value": 1}, "payload": {"value": 2}}
    ).payload == Payload(2)
    with pytest.raises(TypeError, match="requires concrete specialization"):
        derive_spec(Box, mode="output")


def test_directional_schema_projection_and_introspection_are_independent_dimensions() -> None:
    class User(Spec):
        identifier: Annotated[int, ReadOnly()]
        name: str
        password: Annotated[str, WriteOnly(), Sensitive()]

    UserInput = derive_spec(User, mode="input")
    UserOutput = derive_spec(User, mode="output")

    input_info = inspect_spec(UserInput)
    output_info = inspect_spec(UserOutput)
    assert input_info.derivation is not None
    assert input_info.derivation.mode == "input"
    assert input_info.derivation.retained_fields == ("name", "password")
    assert output_info.derivation is not None
    assert output_info.derivation.mode == "output"
    assert output_info.derivation.omitted_fields == ("password",)
    for schema_mode in ("input", "output"):
        input_schema = definition(UserInput.json_schema(mode=schema_mode), "UserInput")
        output_schema = definition(UserOutput.json_schema(mode=schema_mode), "UserOutput")
        assert tuple(cast(dict[str, object], input_schema["properties"])) == ("name", "password")
        assert tuple(cast(dict[str, object], output_schema["properties"])) == ("identifier", "name")
    openapi = UserOutput.openapi_schema(mode="output")
    output_component = cast(dict[str, object], openapi["components"])["schemas"]
    assert "password" not in str(output_component)


def test_only_input_directional_partials_are_compatible_source_patches() -> None:
    class User(Spec):
        identifier: Annotated[int, ReadOnly()]
        name: str
        password: Annotated[str, WriteOnly()]

    UserPatch = derive_spec(User, mode="input", partial=True)
    UserOutputPatch = derive_spec(User, mode="output", partial=True)
    LegacyPatch = derive_spec(User, partial=True)
    source = User(identifier=1, name="Ada", password="secret")

    updated = apply_patch(source, UserPatch.from_json('{"name":"Grace"}'))
    assert updated.to_dict() == {"identifier": 1, "name": "Grace", "password": "secret"}
    assert UserPatch().present_fields == frozenset()
    with pytest.raises(ValidationError):
        UserPatch.from_mapping({"identifier": 2})
    with pytest.raises(TypeError, match="rejects output-derived"):
        apply_patch(source, UserOutputPatch(identifier=2))
    assert apply_patch(source, LegacyPatch(identifier=2)).identifier == 2


def test_copy_deepcopy_pickle_and_large_concurrent_directional_derivation() -> None:
    value = DirectionalPickleOutput(identifier=1, value="safe")
    for candidate in (copy(value), deepcopy(value), replace(value, value="changed"), pickle.loads(pickle.dumps(value))):
        assert type(candidate) is DirectionalPickleOutput
        assert candidate.to_dict()["identifier"] == 1
        assert candidate.to_dict()["value"] in ("safe", "changed")

    fields = {f"field_{index}": Annotated[int, WriteOnly()] if index % 2 else int for index in range(1_000)}
    Large = create_spec("LargeDirectional", fields)
    LargeOutput = derive_spec(Large, mode="output")
    assert len(inspect_spec(LargeOutput).fields) == 500
    assert all(int(field.name.removeprefix("field_")) % 2 == 0 for field in inspect_spec(LargeOutput).fields)

    with ThreadPoolExecutor(max_workers=8) as executor:
        derived = tuple(executor.map(lambda _: derive_spec(Large, mode="output"), range(24)))
    assert len({id(spec) for spec in derived}) == 24
    assert all(len(inspect_spec(spec).fields) == 500 for spec in derived)


def test_source_runtime_semantics_and_layout_remain_unchanged() -> None:
    class User(Spec):
        identifier: Annotated[int, ReadOnly()]
        password: Annotated[str, WriteOnly()]

    source = User.from_json('{"identifier":1,"password":"secret"}')
    assert source.to_dict() == {"identifier": 1, "password": "secret"}
    assert source.to_json() == '{"identifier":1,"password":"secret"}'
    assert User.__slots__ == ("identifier", "password")
    assert not hasattr(source, "__talea_presence__")


@given(
    st.lists(
        st.sampled_from(("ordinary", "read", "write", "both", "read_false", "write_false")),
        min_size=1,
        max_size=12,
    )
)
def test_directional_field_selection_property(markers: list[str]) -> None:
    annotations: dict[str, object] = {}
    expected_input: list[str] = []
    expected_output: list[str] = []
    for index, marker in enumerate(markers):
        name = f"field_{index}"
        metadata = {
            "ordinary": (),
            "read": (ReadOnly(),),
            "write": (WriteOnly(),),
            "both": (ReadOnly(), WriteOnly()),
            "read_false": (ReadOnly(False),),
            "write_false": (WriteOnly(False),),
        }[marker]
        annotations[name] = Annotated[(int, *metadata)] if metadata else int
        if marker not in ("read", "both"):
            expected_input.append(name)
        if marker not in ("write", "both"):
            expected_output.append(name)
    Source = create_spec("PropertyDirectional", annotations)

    assert field_names(derive_spec(Source, mode="input")) == tuple(expected_input)
    assert field_names(derive_spec(Source, mode="output")) == tuple(expected_output)
