"""Hostile boundary examples: redaction, size, depth, work, and error budgets."""

from collections.abc import Callable, Mapping
from typing import Annotated

from talea import Contract, ResourceLimitError, ResourcePolicy, Sensitive, Spec, ValidationError


class Credentials(Spec):
    username: str
    token: Annotated[str, Sensitive()]


class SessionRequest(Spec):
    credentials: Credentials
    scopes: list[str]


secret = "never-log-this-token"
try:
    SessionRequest.from_mapping({"credentials": {"username": "ada", "token": 42}, "scopes": ["read"]})
except ValidationError as error:
    detail = error.errors()[0]
    assert detail["location"] == ["credentials", "token"]
    assert detail["input"] == "<redacted>"
    assert secret not in str(error)
else:
    raise AssertionError("invalid secret-bearing input must fail")


def expect_resource_error(operation: Callable[[], object], code: str) -> None:
    try:
        operation()
    except ResourceLimitError as error:
        assert error.code == code
        assert error.observed > error.limit
        assert not hasattr(error, "input")
    else:
        raise AssertionError(f"expected {code!r} resource rejection")


expect_resource_error(
    lambda: SessionRequest.from_json(
        '{"credentials":{"username":"ada","token":"' + secret + '"},"scopes":[]}',
        policy=ResourcePolicy(max_input_bytes=32),
    ),
    "input_size",
)

type Nested = int | list[Nested]
nested: Contract[Nested] = Contract(Nested)
expect_resource_error(
    lambda: nested.from_json("[[[[1]]]]", policy=ResourcePolicy(max_depth=3)),
    "depth",
)
expect_resource_error(
    lambda: Contract(list[int]).from_python(
        list(range(20)),
        policy=ResourcePolicy(max_nodes=8),
    ),
    "nodes",
)


class Batch(Spec):
    primary_id: int
    secondary_id: int
    revision: int
    attempts: int


try:
    Batch.from_mapping(
        {
            "primary_id": "bad",
            "secondary_id": "bad",
            "revision": "bad",
            "attempts": "bad",
        },
        policy=ResourcePolicy(max_errors=3),
    )
except ValidationError as error:
    assert error.truncated is True
    assert len(error.errors()) == 3
    assert [item["location"] for item in error.errors()] == [
        ["primary_id"],
        ["secondary_id"],
        ["revision"],
    ]
else:
    raise AssertionError("the error budget must terminate broad aggregation")


class HostileMapping(Mapping[str, object]):
    """Show that custom Mapping behavior is application code, not sandboxed input."""

    def __getitem__(self, key: str) -> object:
        raise RuntimeError(f"application mapping callback for {key}")

    def __iter__(self):
        return iter(("credentials",))

    def __len__(self) -> int:
        return 1


try:
    SessionRequest.from_mapping(HostileMapping())
except RuntimeError as error:
    assert "application mapping callback" in str(error)
else:
    raise AssertionError("Talea does not claim to sandbox hostile Python callbacks")

# Custom JSON decoders, transforms, checks, serializers, regular expressions,
# and Mapping methods execute with the embedding process's authority. Resource
# policy bounds Talea-owned transport and traversal work, not arbitrary Python.
