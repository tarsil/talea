"""Create normal Talea Spec classes from trusted runtime declarations."""

import keyword
from collections.abc import Callable, Mapping
from sys import _getframe
from typing import TypeVar, cast, overload
from unicodedata import normalize

from talea.spec.fields import field
from talea.spec.lifecycle import Spec

BaseSpec = TypeVar("BaseSpec", bound=Spec)

_IDENTITY_KEYS = frozenset({"__annotations__", "__module__", "__qualname__", "__doc__"})


@overload
def create_spec(
    name: str,
    fields: Mapping[str, object],
    *,
    defaults: Mapping[str, object] | None = None,
    factories: Mapping[str, Callable[[], object]] | None = None,
    base: type[BaseSpec],
    module: str | None = None,
    qualname: str | None = None,
    doc: str | None = None,
    namespace: Mapping[str, object] | None = None,
) -> type[BaseSpec]: ...


@overload
def create_spec(
    name: str,
    fields: Mapping[str, object],
    *,
    defaults: Mapping[str, object] | None = None,
    factories: Mapping[str, Callable[[], object]] | None = None,
    base: type[Spec] = Spec,
    module: str | None = None,
    qualname: str | None = None,
    doc: str | None = None,
    namespace: Mapping[str, object] | None = None,
) -> type[Spec]: ...


def create_spec(
    name: str,
    fields: Mapping[str, object],
    *,
    defaults: Mapping[str, object] | None = None,
    factories: Mapping[str, Callable[[], object]] | None = None,
    base: type[Spec] = Spec,
    module: str | None = None,
    qualname: str | None = None,
    doc: str | None = None,
    namespace: Mapping[str, object] | None = None,
) -> type[Spec]:
    """Create a normal Spec subclass through Talea's canonical metaclass.

    ``fields`` is ordered and maps canonical Python field names to evaluated
    annotations. ``defaults`` and ``factories`` name subsets of those fields and
    are mutually exclusive. Constraints and aliases remain part of annotations;
    decorated hooks, serializers, descriptors, and ordinary methods may be
    supplied through the trusted ``namespace`` mapping.

    Args:
        name: Exact Python class name for the returned Spec.
        fields: Ordered mapping of field names to evaluated annotations.
        defaults: Optional static defaults keyed by field name.
        factories: Optional zero-argument default factories keyed by field name.
        base: Existing Spec class or concrete generic specialization to extend.
        module: Import module identity; defaults to the caller's module.
        qualname: Qualified class identity; defaults to ``name``.
        doc: Class docstring, or ``None``.
        namespace: Trusted ordinary class-body entries such as methods and
            decorated Talea hooks.

    Returns:
        A normal Spec subclass using the same schema, constructor, input,
        serialization, generic, copy, and pickle lifecycle as class syntax.

    Raises:
        TypeError: If identity metadata, mappings, fields, defaults, factories,
            namespace entries, annotations, or the base are invalid.

    Dynamic fields cannot be inferred statically on Python 3.14. Pickling follows
    normal Python rules: the caller must bind the result at its declared module
    and qualified name. Talea never mutates a module to install the class.
    """

    _validate_class_name(name)
    if not isinstance(fields, Mapping):
        raise TypeError("create_spec fields must be a mapping")
    if not isinstance(base, type) or not getattr(base, "__talea_spec__", False):
        raise TypeError("create_spec base must be a Spec class")
    if any(isinstance(annotation, str) for annotation in fields.values()):
        raise TypeError("create_spec requires evaluated field annotations")
    if (
        defaults is None
        and factories is None
        and module is None
        and qualname is None
        and doc is None
        and namespace is None
    ):
        caller_module = str(_getframe(1).f_globals.get("__name__", "__main__"))
        _validate_module(caller_module)
        return cast(
            type[Spec],
            type(base)(
                name,
                (base,),
                {
                    "__annotations__": dict(fields),
                    "__module__": caller_module,
                    "__qualname__": name,
                },
            ),
        )
    default_values = _normalize_field_values(defaults, "defaults")
    factory_values = _normalize_field_values(factories, "factories")
    field_names = frozenset(fields)
    unknown = (default_values.keys() | factory_values.keys()) - field_names
    if unknown:
        raise TypeError(f"create_spec declaration names unknown field {min(unknown)!r}")
    overlap = default_values.keys() & factory_values.keys()
    if overlap:
        raise TypeError(f"create_spec field {min(overlap)!r} has both a default and factory")
    for field_name, factory in factory_values.items():
        if not callable(factory):
            raise TypeError(f"create_spec factory for {field_name!r} must be callable")

    class_body = _normalize_namespace(namespace)
    collisions = field_names & class_body.keys()
    if collisions:
        raise TypeError(f"create_spec field {min(collisions)!r} conflicts with namespace")
    identity_collisions = _IDENTITY_KEYS & class_body.keys()
    if identity_collisions:
        raise TypeError(f"create_spec manages namespace key {min(identity_collisions)!r}")

    if module is None:
        module = str(_getframe(1).f_globals.get("__name__", "__main__"))
    _validate_module(module)
    resolved_qualname = name if qualname is None else qualname
    _validate_qualname(resolved_qualname)
    if doc is not None and type(doc) is not str:
        raise TypeError("create_spec doc must be str or None")

    class_body["__annotations__"] = dict(fields)
    class_body["__module__"] = module
    class_body["__qualname__"] = resolved_qualname
    class_body["__doc__"] = doc
    for field_name, value in default_values.items():
        class_body[field_name] = value
    for field_name, factory in factory_values.items():
        class_body[field_name] = field(default_factory=cast(Callable[[], object], factory))
    return cast(type[Spec], type(base)(name, (base,), class_body))


def _normalize_field_values(values: Mapping[str, object] | None, parameter: str) -> dict[str, object]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TypeError(f"create_spec {parameter} must be a mapping")
    return dict(values)


def _normalize_namespace(namespace: Mapping[str, object] | None) -> dict[str, object]:
    if namespace is None:
        return {}
    if not isinstance(namespace, Mapping):
        raise TypeError("create_spec namespace must be a mapping")
    return dict(namespace)


def _validate_class_name(name: object) -> None:
    if type(name) is not str or not name.isidentifier() or keyword.iskeyword(name) or normalize("NFKC", name) != name:
        raise TypeError(f"invalid create_spec class name: {name!r}")


def _validate_module(module: object) -> None:
    if (
        type(module) is not str
        or not module
        or any(
            not part.isidentifier() or keyword.iskeyword(part) or normalize("NFKC", part) != part
            for part in module.split(".")
        )
    ):
        raise TypeError("create_spec module must be a dotted Python name")


def _validate_qualname(qualname: object) -> None:
    if (
        type(qualname) is not str
        or not qualname
        or any(
            not part.isidentifier() or keyword.iskeyword(part) or normalize("NFKC", part) != part
            for part in qualname.split(".")
        )
    ):
        raise TypeError("create_spec qualname must be a dotted Python name")
