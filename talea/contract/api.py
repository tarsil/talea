"""Expose retained arbitrary annotation contracts over canonical Talea owners."""

from collections.abc import Callable
from typing import Generic, Literal, TypeVar, cast, overload

from talea.contract.artifacts import _ContractArtifacts
from talea.declaration.policies import schema_contains_sensitive_metadata, schema_root_metadata
from talea.input.json import JsonInput, JsonLoads, decode_json
from talea.metadata import annotation_metadata
from talea.resources.policy import ResourcePolicy, resolve_policy
from talea.resources.state import resource_state
from talea.schema.resolution import resolve_annotation
from talea.serialization.json import JsonDumps, encode_json
from talea.validation.compilation import compile_validator
from talea.validation.failure_contracts import describe_schema

T = TypeVar("T")


class Contract(Generic[T]):
    """Retain Talea capabilities for one arbitrary supported annotation.

    Construction resolves the annotation and compiles strict validation once.
    External Python input, JSON input, Python projection, and JSON projection
    compile independently on first use and are retained by this Contract only.
    No process-global Contract cache or codec registry is created.

    Python 3.14 cannot express the type of every runtime type form. Class
    annotations, including stdlib dataclass classes, infer naturally;
    container, union, Literal, Annotated, alias, and TypedDict forms should use
    an explicit ``Contract[T]`` annotation when static output precision is
    required.
    """

    __slots__ = ("_annotation", "_artifacts", "_policy", "validate")

    validate: Callable[[object], T]
    """The retained strict Python validator for this Contract."""

    @overload
    def __init__(
        self,
        annotation: type[T],
        /,
        *,
        policy: ResourcePolicy | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        annotation: object,
        /,
        *,
        policy: ResourcePolicy | None = None,
    ) -> None: ...

    def __init__(
        self,
        annotation: object,
        /,
        *,
        policy: ResourcePolicy | None = None,
    ) -> None:
        """Resolve and retain one supported runtime annotation.

        Args:
            annotation: A Talea-supported Python runtime type expression.
            policy: Immutable input limits retained by this Contract. An
                explicit per-call policy replaces, rather than merges with,
                this policy.

        Raises:
            AnnotationResolutionError: If the annotation has no canonical Talea
                schema or contains an unsupported recursive alias expansion.
        """

        schema = resolve_annotation(annotation)
        metadata = schema_root_metadata(schema, annotation_metadata(annotation))
        validator = compile_validator(schema, sensitive=bool(metadata.sensitive))
        self._annotation = annotation
        self._policy = resolve_policy(policy)
        contains_sensitive = bool(metadata.sensitive) or schema_contains_sensitive_metadata(schema)
        self._artifacts = _ContractArtifacts(
            schema,
            metadata,
            metadata.title or describe_schema(schema),
            contains_sensitive,
        )
        self.validate = cast(Callable[[object], T], validator)

    def __setattr__(self, name: str, value: object) -> None:
        """Set initialization state once and reject later Contract mutation."""

        if hasattr(self, name):
            raise AttributeError("Contract attributes are read-only")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        """Reject deletion from the retained Contract identity."""

        raise AttributeError(f"Contract attribute {name!r} is read-only")

    @property
    def annotation(self) -> object:
        """Return the exact runtime annotation supplied at construction."""

        return self._annotation

    def from_python(
        self,
        value: object,
        /,
        *,
        policy: ResourcePolicy | None = None,
    ) -> T:
        """Convert and validate one untrusted external Python representation.

        Primitive values remain strict. Mappings may construct nested Specs or
        stdlib dataclasses; TypedDict boundaries accept ``Mapping`` and return
        detached exact dictionaries; containers recursively use the existing
        Talea input semantics. Dataclass construction calls the original
        constructor lifecycle once and then validates retained state.
        ``policy`` replaces the Contract's retained policy for this call; it is
        not merged with it.

        Raises:
            ResourceLimitError: If compiled input exceeds a selected depth or
                node limit.
            ValidationError: If conversion or validation fails.
        """

        compiled = self._artifacts.python_input
        if compiled is None:
            compiled = self._artifacts.input_for("mapping")
        selected_policy = self._policy if policy is None else resolve_policy(policy)
        return compiled(value, resource_state(selected_policy))  # ty: ignore[invalid-return-type]

    def from_json(
        self,
        data: JsonInput,
        /,
        *,
        loads: JsonLoads | None = None,
        policy: ResourcePolicy | None = None,
    ) -> T:
        """Decode JSON and validate it through the canonical root boundary.

        The default strict standard-library decoder is used unless ``loads`` is
        supplied for this call. Codec selection never changes Talea conversion
        or validation semantics. ``policy`` replaces the Contract's retained
        policy for this call; it is not merged with it.

        Raises:
            ResourceLimitError: If the transport or compiled input exceeds a
                selected resource limit.
            ValidationError: If JSON decoding, conversion, or validation fails.
        """

        selected_policy = self._policy if policy is None else resolve_policy(policy)
        decoded = decode_json(
            data,
            loads,
            title=self._artifacts.title,
            sensitive=self._artifacts.contains_sensitive,
            policy=selected_policy,
        )
        compiled = self._artifacts.json_input
        if compiled is None:
            compiled = self._artifacts.input_for("json")
        return compiled(decoded, resource_state(selected_policy))  # ty: ignore[invalid-return-type]

    def to_python(self, value: T, /) -> object:
        """Validate and return a detached Python representation of ``value``.

        Mutable containers and TypedDict values are rebuilt. Nested Specs and
        stdlib dataclasses become dictionaries using their declared aliases.

        Raises:
            ValidationError: If the current value violates this contract.
            SerializationError: If a valid value cannot be projected safely.
        """

        validated = self.validate(value)
        compiled = self._artifacts.python_output
        if compiled is None:
            compiled = self._artifacts.output_for("python")
        return compiled(validated, ())

    def to_json(
        self,
        value: T,
        /,
        *,
        dumps: JsonDumps | None = None,
    ) -> str:
        """Validate, project, and encode ``value`` as strict JSON text.

        ``dumps`` selects a codec for this call only and may return text or
        UTF-8 bytes. Talea's schema-aware projection always runs first.

        Raises:
            ValidationError: If the current value violates this contract.
            SerializationError: If projection or JSON encoding fails.
        """

        validated = self.validate(value)
        compiled = self._artifacts.json_output
        if compiled is None:
            compiled = self._artifacts.output_for("json")
        projected = compiled(validated, ())
        return encode_json(projected, dumps, sensitive=self._artifacts.contains_sensitive)

    def json_schema(self, *, mode: Literal["input", "output"] = "input") -> dict[str, object]:
        """Return a fresh Draft 2020-12 schema for this retained Contract.

        Projection consumes the already-resolved canonical schema. Input and
        output modes remain distinct where JSON boundary representations can
        differ; arbitrary callback domains fail with ``SchemaProjectionError``.
        """

        from talea.json_schema.api import json_schema

        return json_schema(self._artifacts.schema, self._artifacts.metadata, mode=mode)

    def openapi_schema(self, *, mode: Literal["input", "output"] = "input") -> dict[str, object]:
        """Return an OpenAPI 3.1-compatible schema/components fragment.

        Canonical aliases, requiredness, definitions, and tagged-union tags are
        reused directly. The result contains no route or operation objects.
        """

        from talea.json_schema.api import openapi_schema

        return openapi_schema(self._artifacts.schema, self._artifacts.metadata, mode=mode)
