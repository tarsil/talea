"""Define Talea's compile-once ``Spec`` declaration lifecycle."""

import keyword
from annotationlib import Format
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FunctionType
from typing import cast, dataclass_transform, get_type_hints
from unicodedata import normalize

from talea._declaration import SpecField, SpecSchema
from talea.annotations import resolve_annotation
from talea.validation import ValidationError, Validator, compile_validator

__all__ = ["Spec"]


@dataclass(frozen=True, slots=True)
class _SpecArtifacts:
    """Retain one declaration's canonical schema and compiled validators."""

    schema: SpecSchema
    validators: tuple[Validator, ...]


class _ConstructorCompiler:
    """Compile a keyword-only initializer specialized for one Spec schema."""

    def compile(self, schema: SpecSchema, validators: tuple[Validator, ...]) -> FunctionType:
        """Return an initializer that validates, then assigns, every field."""

        field_names = tuple(field.name for field in schema.fields)
        reserved = set(field_names)
        instance_name = self.variable("instance", reserved)
        error_type_name = self.variable("validation_error", reserved)
        field_names_name = self.variable("field_names", reserved)
        validator_names = tuple(self.variable(f"validator_{index}", reserved) for index in range(len(field_names)))
        error_names = tuple(self.variable(f"error_{index}", reserved) for index in range(len(field_names)))
        if not field_names:
            source = f"def __init__({instance_name}):\n    pass"
        else:
            parameters = ", ".join(field_names)
            lines = [f"def __init__({instance_name}, *, {parameters}):"]
            for index, field_name in enumerate(field_names):
                validator_name = validator_names[index]
                error_name = error_names[index]
                lines.extend(
                    (
                        "    try:",
                        f"        {validator_name}({field_name})",
                        f"    except {error_type_name} as {error_name}:",
                        f"        raise {error_type_name}(",
                        f"            {error_name}.expected,",
                        f"            {error_name}.value,",
                        f"            ({field_names_name}[{index}], *{error_name}.location),",
                        "        ) from None",
                    )
                )
            for field_name in field_names:
                lines.append(f"    {instance_name}.{field_name} = {field_name}")
            source = "\n".join(lines)

        namespace: dict[str, object] = {
            error_type_name: ValidationError,
            field_names_name: field_names,
            "__name__": __name__,
        }
        for validator_name, validator in zip(validator_names, validators, strict=True):
            namespace[validator_name] = validator
        exec(compile(source, "<talea Spec constructor>", "exec"), namespace)
        return cast(FunctionType, namespace["__init__"])

    @staticmethod
    def variable(purpose: str, reserved: set[str]) -> str:
        """Reserve a compiler-owned identifier that cannot shadow a field."""

        candidate = f"_talea_{purpose}"
        while candidate in reserved:
            candidate += "_"
        reserved.add(candidate)
        return candidate


@dataclass_transform(kw_only_default=True)
class _SpecMeta(type):
    """Build a complete Spec declaration before its first instance exists."""

    def __new__(
        metaclass,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **kwargs: object,
    ) -> "_SpecMeta":
        if not bases:
            namespace["__slots__"] = ()
            namespace["__talea_root__"] = True
            cls = super().__new__(metaclass, name, bases, namespace, **kwargs)
            type.__setattr__(cls, "__talea_artifacts__", _SpecArtifacts(SpecSchema(()), ()))
            return cls

        if len(bases) != 1 or not getattr(bases[0], "__talea_root__", False):
            raise TypeError("Spec inheritance is not supported")

        annotations = metaclass._inspect_annotations(namespace)
        field_names = tuple(annotations)
        metaclass._validate_declaration(namespace, bases[0], field_names)

        namespace["__slots__"] = field_names
        namespace["__talea_root__"] = False
        cls = super().__new__(metaclass, name, bases, namespace, **kwargs)

        resolved_annotations = (
            get_type_hints(cls, include_extras=True)
            if any(isinstance(annotation, str) for annotation in annotations.values())
            else annotations
        )
        schema = SpecSchema(
            tuple(
                SpecField(field_name, resolve_annotation(resolved_annotations[field_name]))
                for field_name in field_names
            )
        )
        validators = tuple(compile_validator(field.schema) for field in schema.fields)
        initializer = _ConstructorCompiler().compile(schema, validators)
        initializer.__module__ = cls.__module__
        initializer.__qualname__ = f"{cls.__qualname__}.__init__"
        initializer.__doc__ = "Validate and retain every declared field."
        type.__setattr__(cls, "__init__", initializer)
        type.__setattr__(cls, "__talea_artifacts__", _SpecArtifacts(schema, validators))
        return cls

    @staticmethod
    def _inspect_annotations(namespace: dict[str, object]) -> Mapping[str, object]:
        """Evaluate one Python 3.14 annotation source before slots are fixed."""

        annotations = namespace.get("__annotations__")
        if annotations is not None:
            if not isinstance(annotations, Mapping):
                raise TypeError("a Spec declaration requires an annotations mapping")
            return annotations

        annotate = namespace.get("__annotate_func__")
        if annotate is None:
            return {}
        if not callable(annotate):
            raise TypeError("a Spec declaration requires a callable annotation function")
        evaluated = cast(Callable[[Format], object], annotate)(Format.VALUE)
        if not isinstance(evaluated, Mapping):
            raise TypeError("a Spec annotation function must return a mapping")
        return evaluated

    @staticmethod
    def _validate_declaration(
        namespace: dict[str, object],
        base: type,
        field_names: tuple[object, ...],
    ) -> None:
        """Reject declaration forms whose lifecycle is outside this campaign."""

        if "__slots__" in namespace:
            raise TypeError("Spec manages instance slots from declared fields")
        if "__init__" in namespace:
            raise TypeError("Spec manages construction from declared fields")
        for field_name in field_names:
            if (
                not isinstance(field_name, str)
                or not field_name.isidentifier()
                or keyword.iskeyword(field_name)
                or normalize("NFKC", field_name) != field_name
                or (field_name.startswith("__") and not field_name.endswith("__"))
            ):
                raise TypeError(f"invalid Spec field name: {field_name!r}")
            if field_name in namespace:
                raise TypeError(f"Spec field defaults are not supported: {field_name!r}")
            if hasattr(base, field_name):
                raise TypeError(f"Spec field conflicts with an inherited attribute: {field_name!r}")


class Spec(metaclass=_SpecMeta):
    """Declare a compact object whose annotated fields validate strictly.

    Subclasses declare required fields with supported Python annotations.  At
    class creation Talea resolves those annotations into canonical schemas,
    compiles one validator per field, and compiles a keyword-only constructor.
    Repeated construction performs no annotation reflection, schema traversal,
    or validator compilation.

    Construction accepts every declared field exactly once by keyword.  Values
    use Talea's exact-type semantics: there is no coercion, supported mutable
    containers retain their identity, missing fields and unknown keywords are
    rejected, and validation errors begin with the failing field name.

    Instances use slots derived from declaration order and retain only field
    values.  They have no instance dictionary or per-instance schema metadata.
    Equality and hashing keep ordinary Python identity semantics.  Defaults,
    positional construction, and Spec inheritance are intentionally unsupported
    in this lifecycle.
    """

    def __repr__(self) -> str:
        """Return the declaration name and current field values in order."""

        artifacts = cast(_SpecArtifacts, vars(type(self))["__talea_artifacts__"])
        values = ", ".join(f"{field.name}={getattr(self, field.name)!r}" for field in artifacts.schema.fields)
        return f"{type(self).__name__}({values})"
