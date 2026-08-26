"""Derive projected and presence-aware Specs from canonical declarations."""

from collections.abc import Iterable
from copy import replace
from sys import _getframe
from typing import TypeVar, cast

from talea.declaration.models import MISSING_DEFAULT, DerivationSelection, SpecDerivation
from talea.spec.declaration import _DerivedSpecPlan, _SpecDeclaration
from talea.spec.lifecycle import Spec

SourceSpec = TypeVar("SourceSpec", bound=Spec)

__all__ = ["apply_patch", "derive_spec"]


def derive_spec(
    source: type[SourceSpec],
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    partial: bool = False,
    name: str | None = None,
    module: str | None = None,
    qualname: str | None = None,
) -> type[Spec]:
    """Create a normal Spec projection from one concrete source declaration.

    ``include`` and ``exclude`` are mutually exclusive canonical field-name
    selections.  Source order, field schemas, aliases, metadata, field-local
    validation hooks, and serializers are retained.  ``partial=True`` makes
    every retained field omittable without admitting ``None`` and without
    running source defaults or factories for absent fields.

    Dynamic field changes cannot be represented precisely by Python's static
    type system, so the result is typed as ``type[Spec]``.  Repeated calls
    intentionally create distinct classes and no process-global cache.
    """

    if not isinstance(source, type) or not getattr(source, "__talea_spec__", False):
        raise TypeError("derive_spec source must be a Spec class")
    if type(partial) is not bool:
        raise TypeError("derive_spec partial must be bool")
    if include is not None and exclude is not None:
        raise TypeError("derive_spec include and exclude are mutually exclusive")
    declaration = cast(_SpecDeclaration, vars(source)["__talea_declaration__"])
    artifacts = declaration.artifacts()
    source_fields = artifacts.schema.fields
    source_names = tuple(field.name for field in source_fields)
    selected, selection = _selection(source_names, include, exclude)
    retained = tuple(field for field in source_fields if field.name in selected)
    if partial:
        retained = tuple(
            replace(
                field,
                default=MISSING_DEFAULT,
                default_factory=None,
                omittable=True,
            )
            for field in retained
        )
    retained_names = tuple(field.name for field in retained)
    retained_set = frozenset(retained_names)
    omitted_names = tuple(field_name for field_name in source_names if field_name not in retained_set)
    hooks = tuple(
        hook
        for hook in artifacts.schema.hooks
        if all(field in retained_set for field in hook.fields) and (not partial or len(hook.fields) == 1)
    )
    serializers = tuple(serializer for serializer in artifacts.schema.serializers if serializer.field in retained_set)
    source_name = (declaration.generic_origin or source).__name__
    resolved_name = name or f"{source_name}{'Partial' if partial else 'Projection'}"
    from talea.spec.dynamic import _validate_class_name, _validate_module, _validate_qualname

    _validate_class_name(resolved_name)
    resolved_module = module or str(_getframe(1).f_globals.get("__name__", source.__module__))
    _validate_module(resolved_module)
    resolved_qualname = qualname or resolved_name
    _validate_qualname(resolved_qualname)
    annotations = _effective_annotations(source, retained_names)
    provenance = SpecDerivation(
        source,
        retained_names,
        omitted_names,
        selection,
        partial,
        name,
    )
    plan = _DerivedSpecPlan(
        annotations,
        retained,
        hooks,
        serializers,
        artifacts.schema.metadata,
        provenance,
    )
    namespace: dict[str, object] = {
        "__annotations__": dict(annotations),
        "__module__": resolved_module,
        "__qualname__": resolved_qualname,
        "__doc__": f"Derived data contract projected from {source.__qualname__}.",
    }
    return cast(
        type[Spec],
        type(source)(resolved_name, (Spec,), namespace, _talea_derived_plan=plan),
    )


def apply_patch[SourceSpecT: Spec](instance: SourceSpecT, patch: Spec) -> SourceSpecT:
    """Apply present fields from a compatible partial contract via ``copy.replace``."""

    if not isinstance(instance, Spec) or not isinstance(patch, Spec):
        raise TypeError("apply_patch requires Spec instances")
    patch_artifacts = vars(type(patch))["__talea_declaration__"].artifacts()
    provenance = patch_artifacts.schema.derivation
    if provenance is None or not provenance.partial:
        raise TypeError("apply_patch requires a partial derived Spec")
    if provenance.source is not type(instance):
        raise TypeError(f"patch for {provenance.source.__qualname__} cannot apply to {type(instance).__qualname__}")
    from copy import replace as copy_replace

    from talea.spec.presence import presence_mask

    mask = presence_mask(patch)
    assert mask is not None
    changes = {
        field.name: getattr(patch, field.name)
        for index, field in enumerate(patch_artifacts.schema.fields)
        if mask & (1 << index)
    }
    return copy_replace(instance, **changes)


def _selection(
    source_names: tuple[str, ...],
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
) -> tuple[frozenset[str], DerivationSelection]:
    if include is None and exclude is None:
        return frozenset(source_names), "all"
    supplied = include if include is not None else exclude
    parameter: DerivationSelection = "include" if include is not None else "exclude"
    assert supplied is not None
    if isinstance(supplied, (str, bytes)):
        raise TypeError(f"derive_spec {parameter} must be an iterable of field names")
    values = tuple(supplied)
    if any(type(value) is not str for value in values):
        raise TypeError(f"derive_spec {parameter} must contain str field names")
    if len(values) != len(set(values)):
        raise ValueError(f"derive_spec {parameter} contains duplicate field names")
    unknown = frozenset(values) - frozenset(source_names)
    if unknown:
        raise ValueError(f"derive_spec {parameter} contains unknown field {min(unknown)!r}")
    selected = frozenset(values)
    if exclude is not None:
        selected = frozenset(source_names) - selected
    return selected, parameter


def _effective_annotations(source: type[Spec], retained: tuple[str, ...]) -> dict[str, object]:
    annotations: dict[str, object] = {}
    for field_name in retained:
        annotations[field_name] = next(
            declaration.annotations[field_name]
            for owner in source.__mro__
            if (declaration := vars(owner).get("__talea_declaration__")) is not None
            if field_name in declaration.annotations
        )
    return annotations
