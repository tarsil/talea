"""Define Talea's immutable ``Spec`` instance protocol."""

from collections.abc import Mapping
from typing import ClassVar, Protocol, Self, SupportsIndex, cast

from talea.input.json import JsonInput, JsonLoads, decode_json
from talea.serialization.api import to_dict as _to_dict, to_json as _to_json
from talea.spec.declaration import _ensure_finalized, _SpecArtifacts, _SpecDeclaration
from talea.spec.fields import field
from talea.spec.metaclass import _SpecMeta

__all__ = ["Spec", "field"]


class _Subscriptable(Protocol):
    """Describe runtime Spec objects that accept specialization."""

    def __getitem__(self, argument: object, /) -> object: ...


def _restore_spec_instance(
    origin: type[object],
    generic_arguments: tuple[object, ...],
    values: tuple[object, ...],
) -> object:
    """Restore one trusted pickle payload through canonical class artifacts."""

    if generic_arguments:
        argument = generic_arguments[0] if len(generic_arguments) == 1 else generic_arguments
        spec_type = cast(type[object], cast(_Subscriptable, origin)[argument])
    else:
        spec_type = origin
    artifacts = _ensure_finalized(spec_type)
    restored = object.__new__(spec_type)
    for value, setter in zip(values, artifacts.inputs.slot_setters, strict=True):
        setter(restored, value)
    return restored


class Spec(metaclass=_SpecMeta):
    """Declare a compact object whose annotated fields validate strictly.

    Subclasses declare required fields with supported Python annotations. At
    class creation Talea resolves those annotations into canonical schemas,
    compiles one standalone validator per field, and emits those same validation
    operations directly into a keyword-only constructor. Repeated construction
    performs no annotation reflection, schema traversal, validator calls, or
    compilation.

    Construction accepts every declared field exactly once by keyword. A direct
    assignment provides a validated immutable static default;
    ``field(default_factory=...)`` produces and validates an omitted value per
    instance. Optional annotations remain required without one of those
    declarations. Values use Talea's exact-type semantics: there is no coercion,
    supplied mutable containers retain their identity, missing fields and
    unknown keywords are rejected, and validation errors begin with the failing
    field name.

    Instances use slots derived from declaration order and retain only field
    values. They have no instance dictionary or per-instance schema metadata.
    Field bindings are immutable after successful construction. Declarations
    containing only transitively immutable schemas are permanently trusted;
    declarations containing list, set, or dictionary values remain validated
    but are not eligible for Talea's no-revalidation trust path. Equality and
    hashing keep ordinary Python identity semantics. Subclasses inherit and may
    override fields while compiling one flat effective constructor. Multiple
    inheritance is supported when CPython can preserve one state-bearing slot
    lineage; additional mixins must use empty slots.

    ``transform`` callbacks explicitly prepare inbound field values before the
    emitted structural checks. ``check`` callbacks assert field or cross-field
    invariants after structure and before slot commitment. Declarations without
    hooks retain the same generated construction path.
    """

    __talea_artifacts__: ClassVar[_SpecArtifacts]

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation so a validated Spec cannot silently become invalid."""

        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __copy__(self) -> Self:
        """Return a shallow copy without repeating validation or lifecycle hooks."""

        spec_type = type(self)
        artifacts = _ensure_finalized(spec_type)
        copied = object.__new__(spec_type)
        for spec_field, setter in zip(artifacts.schema.fields, artifacts.inputs.slot_setters, strict=True):
            setter(copied, getattr(self, spec_field.name))
        return copied

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        """Return a graph-preserving deep copy of this validated instance."""

        from copy import deepcopy

        spec_type = type(self)
        artifacts = _ensure_finalized(spec_type)
        copied = object.__new__(spec_type)
        memo[id(self)] = copied
        for spec_field, setter in zip(artifacts.schema.fields, artifacts.inputs.slot_setters, strict=True):
            setter(copied, deepcopy(getattr(self, spec_field.name), memo))
        return copied

    def __replace__(self, /, **changes: object) -> Self:
        """Return a validated immutable replacement for :func:`copy.replace`.

        Keywords are canonical Python field names; aliases remain external
        boundary metadata. Changed values follow direct-construction transform,
        validation, and field-check semantics. Untouched permanently trusted
        values are reused directly, while mutable current state is revalidated.
        Whole-Spec checks always rerun before a new object is committed.

        Defaults and factories do not rerun. Unchanged mutable values are shared
        by reference, matching ordinary immutable-record replacement; no deep
        copy is implied.

        Raises:
            TypeError: If a keyword is not a canonical field name.
            ValidationError: If changed or mutable current values, field checks,
                or whole-Spec invariants fail.
        """

        from talea.spec.replacement import replacement_for

        spec_type = type(self)
        artifacts = _ensure_finalized(spec_type)
        replace = replacement_for(spec_type, artifacts)
        return replace(self, changes)  # ty: ignore[invalid-return-type]

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> tuple[object, tuple[object, ...]]:
        """Describe an acyclic instance for trusted Python pickle reconstruction."""

        del protocol
        spec_type = type(self)
        declaration = cast(_SpecDeclaration, vars(spec_type)["__talea_declaration__"])
        origin = declaration.generic_origin or spec_type
        artifacts = _ensure_finalized(spec_type)
        values = tuple(getattr(self, spec_field.name) for spec_field in artifacts.schema.fields)
        return _restore_spec_instance, (origin, declaration.generic_arguments, values)

    to_dict = _to_dict
    to_json = _to_json

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Self:
        """Construct ``cls`` from an untrusted Python mapping.

        The mapping boundary accepts any :class:`collections.abc.Mapping` with
        each field's canonical external name. Python values remain strict while
        nested mappings may construct nested Specs. Validation failures preserve
        Talea's canonical locations and declaration order.

        Args:
            data: Untrusted values keyed by canonical external names.

        Returns:
            A fully validated immutable instance of the invoked Spec subclass.

        Raises:
            ValidationError: If mapping conversion or validation fails.

        The boundary callable is compiled once on first use and retained by the
        declaration.
        """

        artifacts = _ensure_finalized(cls)
        construct = artifacts.inputs.mapping_input
        if construct is None:
            construct = artifacts.inputs.input_for(artifacts.schema, cls, "mapping")
        return construct(data)  # ty: ignore[invalid-return-type]

    @classmethod
    def from_json(
        cls,
        data: JsonInput,
        *,
        loads: JsonLoads | None = None,
    ) -> Self:
        """Decode JSON and construct ``cls`` through Talea's input contract.

        ``str``, ``bytes``, and ``bytearray`` are accepted. The default decoder
        rejects duplicate keys and non-standard numbers. A per-call decoder may
        replace JSON decoding but never Talea's compiled conversion or
        validation semantics.

        Args:
            data: Serialized JSON accepted by the selected decoder.
            loads: Optional one-argument decoder for this operation.

        Returns:
            A fully validated immutable instance of the invoked Spec subclass.

        Raises:
            ValidationError: If decoding, conversion, or validation fails.
        """

        decoded = decode_json(data, loads, title=cls.__name__)
        artifacts = _ensure_finalized(cls)
        construct = artifacts.inputs.json_input
        if construct is None:
            construct = artifacts.inputs.input_for(artifacts.schema, cls, "json")
        return construct(decoded)  # ty: ignore[invalid-return-type]

    def __delattr__(self, name: str) -> None:
        """Reject deletion so a validated Spec cannot lose a required value."""

        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __repr__(self) -> str:
        """Return the declaration name and current field values in order."""

        artifacts = _ensure_finalized(type(self))
        values = ", ".join(f"{field.name}={getattr(self, field.name)!r}" for field in artifacts.schema.fields)
        return f"{type(self).__name__}({values})"
