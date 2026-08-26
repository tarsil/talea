"""Expose retained arbitrary annotation contracts over canonical Talea owners."""

from collections.abc import Callable
from typing import Generic, TypeVar, cast, overload

from talea.contract.artifacts import _ContractArtifacts
from talea.input.json import JsonInput, JsonLoads, decode_json
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
    annotations infer naturally; container, union, Literal, Annotated, alias,
    and TypedDict forms should use an explicit ``Contract[T]`` annotation when
    static output precision is required.
    """

    __slots__ = ("_annotation", "_artifacts", "validate")

    validate: Callable[[object], T]
    """The retained strict Python validator for this Contract."""

    @overload
    def __init__(self, annotation: type[T], /) -> None: ...

    @overload
    def __init__(self, annotation: object, /) -> None: ...

    def __init__(self, annotation: object, /) -> None:
        """Resolve and retain one supported runtime annotation.

        Args:
            annotation: A Talea-supported Python runtime type expression.

        Raises:
            AnnotationResolutionError: If the annotation has no canonical Talea
                schema or contains an unsupported recursive alias expansion.
        """

        schema = resolve_annotation(annotation)
        self._annotation = annotation
        self._artifacts = _ContractArtifacts(schema, describe_schema(schema))
        self.validate = cast(Callable[[object], T], compile_validator(schema))

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

    def from_python(self, value: object, /) -> T:
        """Convert and validate one untrusted external Python representation.

        Primitive values remain strict. Mappings may construct nested Specs;
        TypedDict boundaries accept ``Mapping`` and return detached exact
        dictionaries; containers recursively use the existing Talea input
        semantics.

        Raises:
            ValidationError: If conversion or validation fails.
        """

        compiled = self._artifacts.python_input
        if compiled is None:
            compiled = self._artifacts.input_for("mapping")
        return compiled(value)  # ty: ignore[invalid-return-type]

    def from_json(
        self,
        data: JsonInput,
        /,
        *,
        loads: JsonLoads | None = None,
    ) -> T:
        """Decode JSON and validate it through the canonical root boundary.

        The default strict standard-library decoder is used unless ``loads`` is
        supplied for this call. Codec selection never changes Talea conversion
        or validation semantics.

        Raises:
            ValidationError: If JSON decoding, conversion, or validation fails.
        """

        decoded = decode_json(data, loads, title=self._artifacts.title)
        compiled = self._artifacts.json_input
        if compiled is None:
            compiled = self._artifacts.input_for("json")
        return compiled(decoded)  # ty: ignore[invalid-return-type]

    def to_python(self, value: T, /) -> object:
        """Validate and return a detached Python representation of ``value``.

        Mutable containers and TypedDict values are rebuilt. Nested Specs become
        dictionaries using their declared aliases.

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
        return encode_json(projected, dumps)
