"""Project canonical Talea declarations into standards schema documents."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from ipaddress import (
    IPv4Address,
    IPv6Address,
)
from typing import Literal, assert_never, cast
from uuid import UUID

from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.declaration.models import MISSING_DEFAULT, SpecField, SpecSchema
from talea.declaration.policies import schema_contains_sensitive_metadata
from talea.json.representations import standard_json_representation
from talea.metadata import DeclarationMetadata, ExampleValue
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    LiteralValue,
    MappingSchema,
    NamedReferenceSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictField,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)
from talea.schema.references import NamedSchemaIdentity
from talea.serialization.emission import compile_value_projector
from talea.validation.failure_contracts import literal_key, schema_order_key

from .errors import SchemaProjectionError

type SchemaMode = Literal["input", "output"]
type ProjectionTarget = Literal["json_schema", "openapi"]
type DefinitionIdentity = type[object] | NamedSchemaIdentity

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
OPENAPI_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"

_BASE64_PATTERN = r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
_DURATION_PATTERN = (
    r"^-?P(?=.+(?:D|H|M|S)$)(?:\d+D)?"
    r"(?:T(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d{1,6})?S)?)?$"
)
_OPENAPI_COMPONENT = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True, slots=True)
class _Definition:
    """Retain one pending named body without duplicating canonical truth."""

    identity: DefinitionIdentity
    name: str
    module: str
    qualname: str
    value: Schema | type[object]


class _StandardsProjector:
    """Traverse one canonical graph and emit deterministic references once."""

    __slots__ = ("_definitions", "_keys", "_mode", "_pending", "_target", "_used_keys")

    def __init__(self, mode: SchemaMode, target: ProjectionTarget) -> None:
        if mode not in ("input", "output"):
            raise TypeError("schema mode must be 'input' or 'output'")
        self._mode = mode
        self._target = target
        self._keys: dict[DefinitionIdentity, str] = {}
        self._used_keys: set[str] = set()
        self._pending: list[_Definition] = []
        self._definitions: dict[str, dict[str, object]] = {}

    def document(self, schema: Schema, metadata: DeclarationMetadata) -> dict[str, object]:
        """Return one fresh standalone schema or OpenAPI components fragment."""

        root = self._project(schema)
        self._apply_metadata(root, metadata)
        for definition in self._pending:
            key = self._keys[definition.identity]
            self._definitions[key] = self._definition_body(definition)
        if self._target == "json_schema":
            document: dict[str, object] = {"$schema": JSON_SCHEMA_DIALECT, **root}
            if self._definitions:
                document["$defs"] = self._definitions
            return document
        fragment: dict[str, object] = {"schema": root, "components": {"schemas": self._definitions}}
        return fragment

    def _project(self, schema: Schema) -> dict[str, object]:
        if isinstance(schema, ConstrainedSchema):
            projected = self._project(schema.schema)
            self._apply_constraints(projected, schema.schema, schema.constraints)
            return projected
        if isinstance(schema, AliasSchema):
            if schema.identity is not None:
                return self._named_reference(
                    schema.identity,
                    schema.name,
                    schema.module,
                    schema.name,
                    schema,
                )
            projected = self._project(schema.schema)
            self._apply_metadata(projected, schema.metadata)
            return projected
        if isinstance(schema, NamedReferenceSchema):
            identity = schema.identity
            return self._named_reference(
                identity,
                identity.name,
                identity.module,
                identity.name,
                schema.target,
            )
        if isinstance(schema, PrimitiveSchema):
            return self._primitive(schema)
        if isinstance(schema, TypeSchema):
            return self._standard_type(schema)
        if isinstance(schema, EnumSchema):
            return self._enum(schema)
        if isinstance(schema, LiteralSchema):
            return self._literal(schema)
        if isinstance(schema, SpecReferenceSchema):
            spec_type = schema.spec_type
            return self._named_reference(
                spec_type,
                spec_type.__name__,
                spec_type.__module__,
                spec_type.__qualname__,
                spec_type,
            )
        if isinstance(schema, SequenceSchema):
            projected = {"type": "array", "items": self._project(schema.item)}
            if self._mode == "output" and schema.kind in ("set", "frozenset"):
                projected["uniqueItems"] = True
            return projected
        if isinstance(schema, MappingSchema):
            return self._mapping(schema)
        if isinstance(schema, TypedDictSchema):
            identity = schema.identity
            if identity is None:
                return self._typed_dict(schema)
            return self._named_reference(identity, schema.name, schema.module, schema.name, schema)
        if isinstance(schema, TaggedUnionSchema):
            return self._tagged_union(schema)
        if isinstance(schema, VariadicTupleSchema):
            return {"type": "array", "items": self._project(schema.item)}
        if isinstance(schema, FixedTupleSchema):
            size = len(schema.items)
            return {
                "type": "array",
                "prefixItems": [self._project(item) for item in schema.items],
                "items": False,
                "minItems": size,
                "maxItems": size,
            }
        if isinstance(schema, UnionSchema):
            return {"anyOf": [self._project(option) for option in sorted(schema.options, key=schema_order_key)]}
        assert_never(schema)

    def _definition_body(self, definition: _Definition) -> dict[str, object]:
        value = definition.value
        if isinstance(value, type):
            from talea.spec.declaration import _ensure_finalized

            try:
                schema = _ensure_finalized(value).schema
            except TypeError as error:
                raise SchemaProjectionError(str(error)) from error
            return self._spec(schema, value.__qualname__)
        if isinstance(value, AliasSchema):
            projected = self._project(value.schema)
            self._apply_metadata(projected, value.metadata)
            return projected
        if isinstance(value, TypedDictSchema):
            return self._typed_dict(value)
        return self._project(value)

    def _named_reference(
        self,
        identity: DefinitionIdentity,
        name: str,
        module: str,
        qualname: str,
        value: Schema | type[object],
    ) -> dict[str, object]:
        key = self._keys.get(identity)
        if key is None:
            key = self._definition_key(name, module, qualname)
            self._keys[identity] = key
            self._pending.append(_Definition(identity, name, module, qualname, value))
        pointer = self._pointer(key)
        return {"$ref": pointer}

    def _definition_key(self, name: str, module: str, qualname: str) -> str:
        if self._target == "openapi":
            name = _OPENAPI_COMPONENT.sub("_", name)
        candidate = name or "Schema"
        if candidate not in self._used_keys:
            self._used_keys.add(candidate)
            return candidate
        qualified = f"{module}.{qualname}"
        if self._target == "openapi":
            qualified = _OPENAPI_COMPONENT.sub("_", qualified)
        candidate = qualified
        suffix = 2
        while candidate in self._used_keys:
            candidate = f"{qualified}_{suffix}"
            suffix += 1
        self._used_keys.add(candidate)
        return candidate

    def _pointer(self, key: str) -> str:
        escaped = key.replace("~", "~0").replace("/", "~1")
        if self._target == "openapi":
            return f"#/components/schemas/{escaped}"
        return f"#/$defs/{escaped}"

    @staticmethod
    def _primitive(schema: PrimitiveSchema) -> dict[str, object]:
        if schema.kind == "int":
            return {"type": "integer"}
        if schema.kind == "float":
            return {"type": "number"}
        if schema.kind == "str":
            return {"type": "string"}
        if schema.kind == "bool":
            return {"type": "boolean"}
        if schema.kind == "bytes":
            return {"type": "string", "contentEncoding": "base64", "pattern": _BASE64_PATTERN}
        assert schema.kind == "none"
        return {"type": "null"}

    def _standard_type(self, schema: TypeSchema) -> dict[str, object]:
        python_type = schema.python_type
        representation = standard_json_representation(python_type)
        if representation == "decimal":
            if self._mode == "input":
                return {"anyOf": [{"type": "integer"}, {"type": "string"}]}
            return {"type": "string"}
        if representation == "duration":
            return {"type": "string", "format": "duration", "pattern": _DURATION_PATTERN}
        if representation is None:
            raise SchemaProjectionError(f"unsupported JSON representation for {python_type.__qualname__}")
        formats: dict[type[object], str] = {
            UUID: "uuid",
            date: "date",
            datetime: "date-time",
            time: "time",
            IPv4Address: "ipv4",
            IPv6Address: "ipv6",
        }
        projected: dict[str, object] = {"type": "string"}
        schema_format = formats.get(python_type)
        if schema_format is not None:
            projected["format"] = schema_format
        return projected

    def _enum(self, schema: EnumSchema) -> dict[str, object]:
        projector = compile_value_projector(schema, "json", True)
        values = []
        for member in schema.members:
            try:
                values.append(projector(member.value, ()))
            except TypeError as error:
                raise SchemaProjectionError(
                    f"Enum {schema.enum_type.__qualname__} has a member without a JSON representation"
                ) from error
        return self._constant_values(values)

    def _literal(self, schema: LiteralSchema) -> dict[str, object]:
        ordered = sorted(schema.values, key=literal_key)
        projector = compile_value_projector(schema, "json", True)
        values = []
        for literal in ordered:
            try:
                values.append(projector(literal.value, ()))
            except TypeError as error:
                raise SchemaProjectionError("Literal contains a value without a JSON representation") from error
        return self._constant_values(values)

    @staticmethod
    def _constant_values(values: list[object]) -> dict[str, object]:
        typed = [(_json_type(value), value) for value in values]
        kinds = {kind for kind, _ in typed}
        if len(kinds) == 1:
            kind = typed[0][0]
            if len(values) == 1:
                return {"type": kind, "const": values[0]}
            return {"type": kind, "enum": values}
        return {"anyOf": [{"type": kind, "const": value} for kind, value in typed]}

    def _mapping(self, schema: MappingSchema) -> dict[str, object]:
        property_names = self._property_names(schema.key)
        projected: dict[str, object] = {
            "type": "object",
            "additionalProperties": self._project(schema.value),
        }
        if property_names != {"type": "string"}:
            projected["propertyNames"] = property_names
        return projected

    def _property_names(self, schema: Schema) -> dict[str, object]:
        while isinstance(schema, (AliasSchema, ConstrainedSchema)):
            schema = schema.schema
        if isinstance(schema, PrimitiveSchema) and schema.kind == "str":
            return {"type": "string"}
        if isinstance(schema, LiteralSchema) and all(item.python_type is str for item in schema.values):
            values = [item.value for item in sorted(schema.values, key=literal_key)]
            return {"enum": values}
        raise SchemaProjectionError("JSON object key contracts must accept exact string keys")

    def _typed_dict(self, schema: TypedDictSchema) -> dict[str, object]:
        properties = {field.name: self._typed_dict_field(field) for field in schema.fields}
        projected: dict[str, object] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        required = [field.name for field in schema.fields if field.required]
        if required:
            projected["required"] = required
        return projected

    def _typed_dict_field(self, field: TypedDictField) -> dict[str, object]:
        projected = self._project(field.schema)
        self._apply_metadata(projected, field.metadata)
        if field.read_only:
            projected["readOnly"] = True
        return projected

    def _spec(self, schema: SpecSchema, qualname: str) -> dict[str, object]:
        if self._mode == "input":
            transforms = tuple(hook.name for hook in schema.hooks if hook.kind == "transform")
            if transforms:
                raise SchemaProjectionError(
                    f"input schema for Spec {qualname!r} is unknowable because transform {transforms[0]!r} "
                    "does not declare its accepted domain"
                )
        elif schema.serializers:
            raise SchemaProjectionError(
                f"output schema for Spec {qualname!r} is unknowable because serializer "
                f"{schema.serializers[0].name!r} does not declare its return contract"
            )
        properties = {field.external_name: self._spec_field(field) for field in schema.fields}
        projected: dict[str, object] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if self._mode == "input":
            required = [field.external_name for field in schema.fields if field.required]
        else:
            required = [field.external_name for field in schema.fields if not field.omittable]
        if required:
            projected["required"] = required
        self._apply_metadata(projected, schema.metadata)
        return projected

    def _spec_field(self, field: SpecField) -> dict[str, object]:
        projected = self._project(field.schema)
        self._apply_metadata(projected, field.metadata)
        if (
            field.default is not MISSING_DEFAULT
            and not field.metadata.sensitive
            and not schema_contains_sensitive_metadata(field.schema)
            and not self._contains_serializer(field.schema)
        ):
            default = self._project_default(field.schema, field.default)
            if default is not MISSING_DEFAULT:
                projected["default"] = default
        return projected

    @staticmethod
    def _project_default(schema: Schema, value: object) -> object:
        try:
            projected = compile_value_projector(schema, "json", True)(value, ())
            json.dumps(projected, allow_nan=False)
        except (TypeError, ValueError):
            return MISSING_DEFAULT
        return projected

    def _contains_serializer(self, schema: Schema, visiting: frozenset[object] = frozenset()) -> bool:
        if isinstance(schema, (AliasSchema, ConstrainedSchema)):
            return self._contains_serializer(schema.schema, visiting)
        if isinstance(schema, NamedReferenceSchema):
            if schema.identity in visiting:
                return False
            return self._contains_serializer(schema.target, visiting | {schema.identity})
        if isinstance(schema, SpecReferenceSchema):
            from talea.spec.declaration import _ensure_finalized

            target = _ensure_finalized(schema.spec_type).schema
            if schema.spec_type in visiting:
                return False
            return bool(target.serializers) or any(
                self._contains_serializer(field.schema, visiting | {schema.spec_type}) for field in target.fields
            )
        if isinstance(schema, SequenceSchema):
            return self._contains_serializer(schema.item, visiting)
        if isinstance(schema, MappingSchema):
            return self._contains_serializer(schema.key, visiting) or self._contains_serializer(schema.value, visiting)
        if isinstance(schema, TypedDictSchema):
            return any(self._contains_serializer(field.schema, visiting) for field in schema.fields)
        if isinstance(schema, TaggedUnionSchema):
            return any(self._contains_serializer(branch.schema, visiting) for branch in schema.branches)
        if isinstance(schema, VariadicTupleSchema):
            return self._contains_serializer(schema.item, visiting)
        if isinstance(schema, FixedTupleSchema):
            return any(self._contains_serializer(item, visiting) for item in schema.items)
        if isinstance(schema, UnionSchema):
            return any(self._contains_serializer(option, visiting) for option in schema.options)
        return False

    def _tagged_union(self, schema: TaggedUnionSchema) -> dict[str, object]:
        branches = [self._project(branch.schema) for branch in schema.branches]
        projected: dict[str, object] = {"oneOf": branches}
        if self._target == "openapi":
            mapping = {}
            for branch, branch_schema in zip(schema.branches, branches, strict=True):
                reference = branch_schema.get("$ref")
                assert isinstance(reference, str)
                mapping[_discriminator_key(branch.json_tag)] = reference
            projected["discriminator"] = {
                "propertyName": schema.external_name,
                "mapping": mapping,
            }
        return projected

    def _apply_constraints(
        self,
        projected: dict[str, object],
        schema: Schema,
        constraints: tuple[object, ...],
    ) -> None:
        base = schema
        while isinstance(base, AliasSchema):
            base = base.schema
        for constraint in constraints:
            if isinstance(constraint, Gt):
                if not _decimal_schema(base):
                    projected["exclusiveMinimum"] = constraint.value
            elif isinstance(constraint, Ge):
                if not _decimal_schema(base):
                    projected["minimum"] = constraint.value
            elif isinstance(constraint, Lt):
                if not _decimal_schema(base):
                    projected["exclusiveMaximum"] = constraint.value
            elif isinstance(constraint, Le):
                if not _decimal_schema(base):
                    projected["maximum"] = constraint.value
            elif isinstance(constraint, MultipleOf):
                if not _decimal_schema(base) and not (isinstance(base, PrimitiveSchema) and base.kind == "float"):
                    projected["multipleOf"] = constraint.value
            elif isinstance(constraint, MinLength):
                projected[_length_keyword(base, minimum=True)] = _projected_length(base, constraint.value)
            elif isinstance(constraint, MaxLength):
                projected[_length_keyword(base, minimum=False)] = _projected_length(base, constraint.value)
            elif isinstance(constraint, Pattern):
                if constraint.flags != re.UNICODE:
                    raise SchemaProjectionError("Pattern flags cannot be represented by JSON Schema")
                projected["pattern"] = constraint.pattern
            else:
                raise AssertionError("unsupported canonical constraint")

    @staticmethod
    def _apply_metadata(projected: dict[str, object], metadata: DeclarationMetadata) -> None:
        if metadata.title is not None:
            projected["title"] = metadata.title
        if metadata.description is not None:
            projected["description"] = metadata.description
        if metadata.examples is not None:
            projected["examples"] = [_thaw_example(value) for value in metadata.examples]
        if metadata.deprecated is not None:
            projected["deprecated"] = metadata.deprecated
        if metadata.read_only is not None:
            projected["readOnly"] = metadata.read_only
        if metadata.write_only is not None:
            projected["writeOnly"] = metadata.write_only


def project_schema(
    schema: Schema,
    metadata: DeclarationMetadata,
    *,
    mode: SchemaMode,
    target: ProjectionTarget,
) -> dict[str, object]:
    """Project one canonical root without reading its source annotation."""

    return _StandardsProjector(mode, target).document(schema, metadata)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    raise SchemaProjectionError(f"value {value!r} has no JSON scalar representation")


def _discriminator_key(value: LiteralValue) -> str:
    item = value.value
    if isinstance(item, Enum):
        item = item.value
    if type(item) is str:
        return cast(str, item)
    if type(item) is bool:
        return "true" if item else "false"
    if type(item) is int:
        return str(item)
    raise SchemaProjectionError("OpenAPI discriminator mappings require string, integer, or boolean tags")


def _decimal_schema(schema: Schema) -> bool:
    return isinstance(schema, TypeSchema) and schema.python_type is Decimal


def _length_keyword(schema: Schema, *, minimum: bool) -> str:
    if isinstance(schema, PrimitiveSchema) and schema.kind in ("str", "bytes"):
        return "minLength" if minimum else "maxLength"
    if isinstance(schema, MappingSchema):
        return "minProperties" if minimum else "maxProperties"
    if isinstance(schema, (SequenceSchema, VariadicTupleSchema, FixedTupleSchema)):
        return "minItems" if minimum else "maxItems"
    raise SchemaProjectionError(f"length constraint cannot be projected for {type(schema).__name__}")


def _projected_length(schema: Schema, value: int) -> int:
    if isinstance(schema, PrimitiveSchema) and schema.kind == "bytes":
        return 4 * ((value + 2) // 3)
    return value


def _thaw_example(value: ExampleValue) -> object:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, tuple):
        return [_thaw_example(item) for item in value]
    mapping = cast(Mapping[str, ExampleValue], value)
    return {key: _thaw_example(mapping[key]) for key in mapping}
