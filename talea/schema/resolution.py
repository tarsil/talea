"""Resolve Python annotations into compact canonical Talea schema values."""

from dataclasses import MISSING, Field, InitVar, fields as dataclass_fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from inspect import Parameter, getattr_static, signature
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from opcode import opmap
from pathlib import Path, PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
from types import FunctionType, GenericAlias, MemberDescriptorType, NoneType, UnionType
from typing import (
    Annotated,
    ClassVar,
    Literal,
    NewType,
    NotRequired,
    Protocol,
    ReadOnly,
    Required,
    TypeAliasType,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)
from uuid import UUID

from talea.constraints import Constraint, Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.declaration.metadata import Alias
from talea.declaration.models import SerializationHook, SpecField, SpecSchema
from talea.metadata import (
    EMPTY_METADATA,
    DeclarationMetadata,
    annotation_metadata,
    normalize_metadata,
)
from talea.schema.nodes import (
    DATACLASS_MISSING,
    AliasSchema,
    ConstrainedSchema,
    DataclassField,
    DataclassSchema,
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
    TaggedUnionBranch,
    TaggedUnionSchema,
    TypedDictField,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)
from talea.schema.references import NamedSchemaIdentity, _NamedSchemaTarget
from talea.tagged import Discriminator

__all__ = [
    "AnnotationResolutionError",
    "ConstraintDeclarationError",
    "TaggedUnionDeclarationError",
    "resolve_annotation",
]

_CONSTRAINT_TYPES = (Gt, Ge, Lt, Le, MultipleOf, MinLength, MaxLength, Pattern)
_ABSENT_ATTRIBUTE = object()
_EXACT_STANDARD_TYPES = frozenset(
    {
        date,
        IPv4Address,
        IPv6Address,
        IPv4Network,
        IPv6Network,
        IPv4Interface,
        IPv6Interface,
    }
)
_NOMINAL_STANDARD_TYPES = frozenset(
    {
        UUID,
        time,
        datetime,
        timedelta,
        Decimal,
        PurePath,
        Path,
        PurePosixPath,
        PureWindowsPath,
        PosixPath,
        WindowsPath,
    }
)


class _DataclassParams(Protocol):
    """Static view of the stdlib dataclass options consumed by resolution."""

    frozen: bool


class _DataclassInstance(Protocol):
    """Static bridge for a runtime type proven by ``is_dataclass``."""

    __dataclass_fields__: ClassVar[dict[str, Field[object]]]
    __dataclass_params__: ClassVar[_DataclassParams]


class AnnotationResolutionError(TypeError):
    """Report an annotation that has no canonical Talea schema representation.

    ``annotation`` is the exact unsupported leaf, allowing nested failures to
    identify where resolution stopped without retaining the broader typing
    graph.
    """

    def __init__(self, annotation: object) -> None:
        self.annotation = annotation
        super().__init__(f"Unsupported annotation: {annotation!r}")


class ConstraintDeclarationError(TypeError):
    """Report incompatible or contradictory Talea constraint metadata."""


class TaggedUnionDeclarationError(TypeError):
    """Report tagged-union truth that cannot produce deterministic dispatch."""


def resolve_annotation(annotation: object) -> Schema:
    """Normalize one supported Python annotation into immutable schema truth.

    Resolution removes ``typing`` wrappers once, canonicalizes Talea-owned
    ``Annotated`` constraints, and ignores unknown metadata without executing
    or retaining it. Strict primitive, standard-library, Enum, Literal,
    container, union, and Spec-reference semantics are represented entirely by
    Talea schema nodes before runtime validators are compiled.

    Args:
        annotation: A Python annotation to normalize.

    Returns:
        A compact immutable schema containing all validation-relevant truth.

    Raises:
        AnnotationResolutionError: If the annotation or a nested leaf is not
            supported.
        ConstraintDeclarationError: If Talea constraints are inapplicable or
            form an obvious contradiction.
    """

    return _resolve_annotation(annotation, {})


def _resolve_annotation(
    annotation: object,
    targets: dict[NamedSchemaIdentity, _NamedSchemaTarget],
) -> Schema:
    """Resolve one annotation through a finite declaration-identity graph."""

    if annotation is int:
        return PrimitiveSchema("int")
    if annotation is float:
        return PrimitiveSchema("float")
    if annotation is str:
        return PrimitiveSchema("str")
    if annotation is bool:
        return PrimitiveSchema("bool")
    if annotation is bytes:
        return PrimitiveSchema("bytes")
    if annotation is None or annotation is NoneType:
        return PrimitiveSchema("none")
    if isinstance(annotation, type):
        if issubclass(annotation, Enum) and annotation not in (Enum, IntEnum, StrEnum):
            return EnumSchema(
                annotation,
                tuple(LiteralValue(type(member), member) for member in annotation),
            )
        if annotation in _EXACT_STANDARD_TYPES:
            return TypeSchema(cast(type[object], annotation), "exact")
        if annotation in _NOMINAL_STANDARD_TYPES:
            return TypeSchema(cast(type[object], annotation), "nominal")
        if getattr(annotation, "__talea_spec__", False) is True and "__talea_declaration__" in vars(annotation):
            return SpecReferenceSchema(annotation)

    dataclass_type = annotation if isinstance(annotation, type) and is_dataclass(annotation) else None

    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if dataclass_type is not None:
        return _resolve_dataclass(dataclass_type, arguments, targets)
    if isinstance(origin, type) and is_dataclass(origin):
        return _resolve_dataclass(origin, arguments, targets)

    if isinstance(annotation, TypeAliasType):
        return _resolve_alias(annotation, (), targets)
    if isinstance(origin, TypeAliasType):
        return _resolve_alias(origin, arguments, targets)
    if isinstance(annotation, NewType):
        return AliasSchema(
            annotation.__name__,
            annotation.__module__,
            _resolve_annotation(annotation.__supertype__, targets),
        )
    typed_dict = annotation if is_typeddict(annotation) else origin
    if typed_dict is not None and is_typeddict(typed_dict):
        return _resolve_typed_dict(cast(type[object], typed_dict), arguments, targets)

    if origin is Annotated:
        discriminators = tuple(item for item in arguments[1:] if isinstance(item, Discriminator))
        if len(discriminators) > 1:
            raise TaggedUnionDeclarationError("an annotation can declare only one Discriminator")
        if discriminators:
            schema = _resolve_tagged_union(arguments[0], discriminators[0], targets)
            constraints = tuple(item for item in arguments[1:] if isinstance(item, _CONSTRAINT_TYPES))
            return _apply_constraints(schema, constraints)
        schema = _resolve_annotation(arguments[0], targets)
        constraints = tuple(item for item in arguments[1:] if isinstance(item, _CONSTRAINT_TYPES))
        return _apply_constraints(schema, constraints)
    if origin is Literal:
        if not arguments:
            raise AnnotationResolutionError(annotation)
        return _resolve_literal(arguments)

    if origin in (UnionType, Union):
        options = frozenset(_resolve_annotation(argument, targets) for argument in arguments)
        if len(options) == 1:
            return next(iter(options))
        if any(_is_tagged_option(option) for option in options) and any(
            not _is_tagged_option(option) and not (isinstance(option, PrimitiveSchema) and option.kind == "none")
            for option in options
        ):
            raise TaggedUnionDeclarationError(
                "a tagged union may combine with None, but not unrelated outer alternatives"
            )
        return UnionSchema(options)

    if not isinstance(annotation, GenericAlias):
        raise AnnotationResolutionError(annotation)

    if origin is list and len(arguments) == 1:
        return SequenceSchema("list", _resolve_annotation(arguments[0], targets))
    if origin is set and len(arguments) == 1:
        return SequenceSchema("set", _resolve_annotation(arguments[0], targets))
    if origin is frozenset and len(arguments) == 1:
        return SequenceSchema("frozenset", _resolve_annotation(arguments[0], targets))
    if origin is dict and len(arguments) == 2:
        return MappingSchema(
            _resolve_annotation(arguments[0], targets),
            _resolve_annotation(arguments[1], targets),
        )
    if origin is tuple:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return VariadicTupleSchema(_resolve_annotation(arguments[0], targets))
        if arguments and Ellipsis not in arguments:
            return FixedTupleSchema(tuple(_resolve_annotation(item, targets) for item in arguments))

    raise AnnotationResolutionError(annotation)


def _resolve_alias(
    alias: TypeAliasType,
    arguments: tuple[object, ...],
    targets: dict[NamedSchemaIdentity, _NamedSchemaTarget],
) -> Schema:
    parameters = cast(tuple[TypeVar, ...], alias.__type_params__)
    if len(parameters) != len(arguments):
        raise AnnotationResolutionError(alias)
    identity = NamedSchemaIdentity(
        "alias",
        alias.__name__,
        alias.__module__ or "__main__",
        alias,
        arguments,
    )
    target = targets.get(identity)
    if target is not None:
        return target.schema if target.finalized else NamedReferenceSchema(target.identity, target)
    target = _NamedSchemaTarget(identity)
    targets[identity] = target
    substitutions = dict(zip(parameters, arguments, strict=True))
    value = _substitute_alias(alias.__value__, substitutions)
    metadata = annotation_metadata(value)
    try:
        schema = _resolve_annotation(value, targets)
        resolved = AliasSchema(alias.__name__, alias.__module__ or "__main__", schema, metadata, identity)
        target.finalize(resolved)
        return resolved
    except BaseException:
        targets.pop(identity, None)
        raise


def _resolve_typed_dict(
    typed_dict: type[object],
    arguments: tuple[object, ...],
    targets: dict[NamedSchemaIdentity, _NamedSchemaTarget],
) -> Schema:
    parameters = tuple(getattr(typed_dict, "__type_params__", ()))
    if parameters and len(parameters) != len(arguments):
        raise AnnotationResolutionError(typed_dict)
    identity = NamedSchemaIdentity(
        "typed_dict",
        typed_dict.__name__,
        typed_dict.__module__,
        typed_dict,
        arguments,
    )
    target = targets.get(identity)
    if target is not None:
        return target.schema if target.finalized else NamedReferenceSchema(target.identity, target)
    target = _NamedSchemaTarget(identity)
    targets[identity] = target
    substitutions = dict(zip(parameters, arguments, strict=True))
    annotations = get_type_hints(typed_dict, include_extras=True)
    required_keys = cast(frozenset[str], vars(typed_dict)["__required_keys__"])
    readonly_keys = cast(frozenset[str], getattr(typed_dict, "__readonly_keys__", frozenset()))
    try:
        fields = []
        for name, annotation in annotations.items():
            if substitutions:
                annotation = _substitute_alias(annotation, substitutions)
            annotation, required, read_only = _unwrap_typed_dict_qualifiers(
                annotation,
                name in required_keys,
                name in readonly_keys,
            )
            fields.append(
                TypedDictField(
                    name,
                    _resolve_annotation(annotation, targets),
                    required,
                    read_only,
                    annotation_metadata(annotation),
                )
            )
        resolved = TypedDictSchema(typed_dict.__name__, typed_dict.__module__, tuple(fields), identity)
        target.finalize(resolved)
        return resolved
    except BaseException:
        targets.pop(identity, None)
        raise


def _resolve_dataclass(
    dataclass_type: type[object],
    arguments: tuple[object, ...],
    targets: dict[NamedSchemaIdentity, _NamedSchemaTarget],
) -> Schema:
    """Resolve one stdlib dataclass through its effective stored fields."""

    parameters = tuple(getattr(dataclass_type, "__type_params__", ()))
    if len(parameters) != len(arguments):
        raise AnnotationResolutionError(dataclass_type)
    identity = NamedSchemaIdentity(
        "dataclass",
        dataclass_type.__name__,
        dataclass_type.__module__,
        dataclass_type,
        arguments,
    )
    target = targets.get(identity)
    if target is not None:
        return target.schema if target.finalized else NamedReferenceSchema(target.identity, target)

    declared_type = cast(type[_DataclassInstance], dataclass_type)
    declared_fields = cast(tuple[Field[object], ...], dataclass_fields(declared_type))
    hints = get_type_hints(dataclass_type, include_extras=True)
    if any(isinstance(annotation, InitVar) for annotation in hints.values()):
        raise TypeError(f"dataclass {dataclass_type.__qualname__!r} contains unsupported InitVar fields")
    _validate_dataclass_constructor(dataclass_type, declared_fields)
    target = _NamedSchemaTarget(identity)
    targets[identity] = target
    substitutions = dict(zip(parameters, arguments, strict=True))
    params = declared_type.__dataclass_params__
    try:
        resolved_fields = []
        for declared in declared_fields:
            annotation = hints[declared.name]
            if substitutions:
                annotation = _substitute_alias(annotation, substitutions)
            alias, metadata = _dataclass_field_metadata(annotation)
            kw_only = cast(bool, declared.kw_only)
            resolved_fields.append(
                DataclassField(
                    declared.name,
                    _resolve_annotation(annotation, targets),
                    declared.init,
                    kw_only,
                    DATACLASS_MISSING if declared.default is MISSING else declared.default,
                    DATACLASS_MISSING if declared.default_factory is MISSING else declared.default_factory,
                    alias,
                    metadata,
                )
            )
        resolved = DataclassSchema(
            dataclass_type,
            tuple(resolved_fields),
            params.frozen,
            identity,
            _dataclass_constructor_preserves_validated_fields(
                dataclass_type,
                declared_fields,
                params.frozen,
            ),
        )
        target.finalize(resolved)
        return resolved
    except BaseException:
        targets.pop(identity, None)
        raise


def _validate_dataclass_constructor(
    dataclass_type: type[object],
    fields: tuple[Field[object], ...],
) -> None:
    """Reject constructors that cannot implement canonical named field input."""

    parameters = signature(dataclass_type).parameters
    init_fields = tuple(field for field in fields if field.init)
    expected_names = frozenset(field.name for field in init_fields)
    if frozenset(parameters) != expected_names:
        raise TypeError(f"dataclass {dataclass_type.__qualname__!r} has an incompatible constructor signature")
    for field in init_fields:
        parameter = parameters[field.name]
        expected_kind = Parameter.KEYWORD_ONLY if field.kw_only else Parameter.POSITIONAL_OR_KEYWORD
        has_default = field.default is not MISSING or field.default_factory is not MISSING
        if parameter.kind is not expected_kind or (parameter.default is not Parameter.empty) is not has_default:
            raise TypeError(f"dataclass {dataclass_type.__qualname__!r} has an incompatible constructor signature")


def _dataclass_constructor_preserves_validated_fields(
    dataclass_type: type[object],
    fields: tuple[Field[object], ...],
    frozen: bool,
) -> bool:
    """Prove that construction only stores already-validated required fields."""

    if any(
        not field.init or field.default is not MISSING or field.default_factory is not MISSING
        for field in fields
    ):
        return False
    initializer = dataclass_type.__init__
    if not isinstance(initializer, FunctionType) or initializer.__code__.co_filename != "<string>":
        return False
    if (
        dataclass_type.__new__ is not object.__new__
        or type(dataclass_type).__call__ is not type.__call__
    ):
        return False
    if dataclass_type.__getattribute__ is not object.__getattribute__:
        return False
    if not frozen and dataclass_type.__setattr__ is not object.__setattr__:
        return False
    for item in fields:
        storage = getattr_static(dataclass_type, item.name, _ABSENT_ATTRIBUTE)
        if storage is not _ABSENT_ATTRIBUTE and not isinstance(storage, MemberDescriptorType):
            return False
    return _matches_direct_dataclass_initializer(initializer, fields, frozen)


def _matches_direct_dataclass_initializer(
    initializer: FunctionType,
    fields: tuple[Field[object], ...],
    frozen: bool,
) -> bool:
    """Match the Python 3.14 dataclass initializer's direct-storage bytecode."""

    code = initializer.__code__
    cache = opmap["CACHE"]
    instructions = tuple(
        (code.co_code[offset], code.co_code[offset + 1])
        for offset in range(0, len(code.co_code), 2)
        if code.co_code[offset] != cache
    )
    index = 0

    def consume(opname: str, argument: int) -> bool:
        nonlocal index
        if index == len(instructions):
            return False
        if instructions[index] != (opmap[opname], argument):
            return False
        index += 1
        return True

    if frozen:
        if code.co_freevars != ("__dataclass_builtins_object__",) or code.co_names != (
            "__setattr__",
        ):
            return False
        closure = initializer.__closure__
        if closure is None or len(closure) != 1 or closure[0].cell_contents is not object:
            return False
        try:
            none_constant = code.co_consts.index(None)
        except ValueError:
            return False
        if not consume("COPY_FREE_VARS", 1):
            return False
    else:
        if (
            code.co_freevars
            or code.co_names != tuple(item.name for item in fields)
            or code.co_consts != (None,)
        ):
            return False
    if not consume("RESUME", 0):
        return False
    for item in fields:
        try:
            field_local = code.co_varnames.index(item.name)
        except ValueError:
            return False
        if frozen:
            try:
                field_constant = code.co_consts.index(item.name)
            except ValueError:
                return False
            matched = (
                consume("LOAD_DEREF", code.co_nlocals)
                and consume("LOAD_ATTR", 1)
                and consume("LOAD_FAST_BORROW", 0)
                and consume("LOAD_CONST", field_constant)
                and consume("LOAD_FAST_BORROW", field_local)
                and consume("CALL", 3)
                and consume("POP_TOP", 0)
            )
        else:
            if field_local > 15:
                return False
            matched = consume(
                "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
                field_local << 4,
            ) and consume("STORE_ATTR", code.co_names.index(item.name))
        if not matched:
            return False
    if frozen:
        return (
            consume("LOAD_CONST", none_constant)
            and consume("RETURN_VALUE", 0)
            and index == len(instructions)
        )
    return consume("LOAD_CONST", 0) and consume("RETURN_VALUE", 0) and index == len(instructions)


def _dataclass_field_metadata(annotation: object) -> tuple[str | None, DeclarationMetadata]:
    """Extract Talea annotation metadata without reading dataclass metadata."""

    if get_origin(annotation) is not Annotated:
        return None, EMPTY_METADATA
    extras = get_args(annotation)[1:]
    aliases = tuple(item for item in extras if isinstance(item, Alias))
    if len(aliases) > 1:
        raise TypeError("a dataclass field can declare only one Alias")
    return (aliases[0].name if aliases else None), normalize_metadata(extras)


def _unwrap_typed_dict_qualifiers(
    annotation: object,
    required: bool,
    read_only: bool,
) -> tuple[object, bool, bool]:
    while (origin := get_origin(annotation)) in (Required, NotRequired, ReadOnly):
        if origin is Required:
            required = True
        elif origin is NotRequired:
            required = False
        else:
            read_only = True
        annotation = get_args(annotation)[0]
    return annotation, required, read_only


def _substitute_alias(annotation: object, substitutions: dict[TypeVar, object]) -> object:
    if isinstance(annotation, TypeVar):
        return substitutions.get(annotation, annotation)
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if not arguments:
        return annotation
    if origin is Annotated:
        return Annotated[_substitute_alias(arguments[0], substitutions), *arguments[1:]]
    if origin in (UnionType, Union):
        return Union[tuple(_substitute_alias(item, substitutions) for item in arguments)]
    substituted = tuple(_substitute_alias(item, substitutions) for item in arguments)
    if isinstance(annotation, GenericAlias):
        return GenericAlias(cast(type, origin), substituted[0] if len(substituted) == 1 else substituted)
    copier = getattr(annotation, "copy_with", None)
    assert copier is not None
    return copier(substituted)


def _resolve_literal(arguments: tuple[object, ...]) -> LiteralSchema:
    values: set[LiteralValue] = set()
    for value in arguments:
        if value is None or type(value) in (str, bytes, int, bool) or isinstance(value, Enum):
            values.add(LiteralValue(type(value), value))
            continue
        raise AnnotationResolutionError(value)
    return LiteralSchema(frozenset(values))


def _is_tagged_option(schema: Schema) -> bool:
    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    return isinstance(schema, TaggedUnionSchema)


def _resolve_tagged_union(
    annotation: object,
    declaration: Discriminator,
    targets: dict[NamedSchemaIdentity, _NamedSchemaTarget],
) -> Schema:
    """Normalize one explicit union from existing branch field truth."""

    origin = get_origin(annotation)
    if origin not in (UnionType, Union):
        raise TaggedUnionDeclarationError("Discriminator requires a union annotation")
    options = tuple(_resolve_annotation(argument, targets) for argument in get_args(annotation))
    nullable = tuple(option for option in options if isinstance(option, PrimitiveSchema) and option.kind == "none")
    branch_options = cast(
        tuple[SpecReferenceSchema | TypedDictSchema, ...],
        tuple(option for option in options if option not in nullable),
    )
    if len(branch_options) < 2:
        raise TaggedUnionDeclarationError("a tagged union requires at least two non-None branches")
    if any(not isinstance(option, (SpecReferenceSchema, TypedDictSchema)) for option in branch_options):
        raise TaggedUnionDeclarationError("tagged unions support Spec or TypedDict branches, with optional None only")
    spec_family = all(isinstance(option, SpecReferenceSchema) for option in branch_options)
    typed_dict_family = all(isinstance(option, TypedDictSchema) for option in branch_options)
    if not (spec_family or typed_dict_family):
        raise TaggedUnionDeclarationError("tagged unions cannot mix Spec and TypedDict branches")

    if spec_family:
        spec_types = tuple(cast(SpecReferenceSchema, option).spec_type for option in branch_options)
        for index, candidate in enumerate(spec_types):
            if any(issubclass(candidate, other) or issubclass(other, candidate) for other in spec_types[index + 1 :]):
                raise TaggedUnionDeclarationError("tagged Spec branches must have non-overlapping nominal types")

    branch_data = []
    canonical_name = external_name = None
    sensitive = False
    for option in branch_options:
        field, serializers = _tagged_branch_field(option, declaration.name)
        if not field.required:
            raise TaggedUnionDeclarationError(f"discriminator field {field.name!r} must be required in every branch")
        literal = _single_literal(field.schema)
        if literal is None:
            raise TaggedUnionDeclarationError(f"discriminator field {field.name!r} must be a single-value Literal")
        if field.name in serializers:
            raise TaggedUnionDeclarationError(f"discriminator field {field.name!r} cannot have a serialization hook")
        json_tag = _json_tag(literal)
        if canonical_name is None:
            canonical_name = field.name
            external_name = field.external_name
        elif field.name != canonical_name or field.external_name != external_name:
            raise TaggedUnionDeclarationError(
                "all tagged-union branches must share one canonical field and external name"
            )
        sensitive = sensitive or bool(field.metadata.sensitive)
        branch_data.append(TaggedUnionBranch(literal, json_tag, option))

    assert canonical_name is not None and external_name is not None
    try:
        tagged = TaggedUnionSchema(
            canonical_name,
            external_name,
            tuple(sorted(branch_data, key=lambda branch: _literal_sort_key(branch.tag))),
            sensitive,
        )
    except ValueError as error:
        raise TaggedUnionDeclarationError(str(error)) from None
    if nullable:
        return UnionSchema(frozenset({tagged, PrimitiveSchema("none")}))
    return tagged


def _tagged_branch_field(
    branch: SpecReferenceSchema | TypedDictSchema,
    declared_name: str,
) -> tuple[SpecField | TypedDictField, frozenset[str]]:
    """Return one branch's canonical discriminator field and serializers."""

    if isinstance(branch, TypedDictSchema):
        matches = tuple(field for field in branch.fields if field.name == declared_name)
        if not matches:
            raise TaggedUnionDeclarationError(
                f"TypedDict branch {branch.name!r} has no discriminator key {declared_name!r}"
            )
        return matches[0], frozenset()

    namespace = vars(branch.spec_type)
    artifacts = namespace.get("__talea_artifacts__")
    declaration = namespace["__talea_declaration__"]
    if declaration.type_params:
        raise TaggedUnionDeclarationError(
            f"generic Spec branch {branch.spec_type.__qualname__!r} requires concrete specialization"
        )
    if artifacts is not None:
        schema = cast(SpecSchema, artifacts.schema)
        fields: tuple[SpecField, ...] = schema.fields
        serializers = frozenset(serializer.field for serializer in schema.serializers)
    elif declaration.prepared_fields is not None:
        effective: dict[str, SpecField] = {}
        for inherited in cast(tuple[SpecSchema, ...], declaration.inherited_schemas):
            for field in inherited.fields:
                effective.setdefault(field.name, field)
        for field in cast(tuple[SpecField, ...], declaration.prepared_fields):
            effective[field.name] = field
        fields = tuple(effective.values())
        effective_serializers: dict[str, SerializationHook] = {}
        for inherited in cast(tuple[SpecSchema, ...], declaration.inherited_schemas):
            for serializer in inherited.serializers:
                effective_serializers.setdefault(serializer.name, serializer)
        for name in declaration.shadowed_serializer_names:
            effective_serializers.pop(name, None)
        for serializer in cast(tuple[SerializationHook, ...], declaration.declared_serializers):
            effective_serializers[serializer.name] = serializer
        serializers = frozenset(serializer.field for serializer in effective_serializers.values())
    else:
        schema = cast(SpecSchema, declaration.artifacts().schema)
        fields = schema.fields
        serializers = frozenset(serializer.field for serializer in schema.serializers)
    matches = tuple(field for field in fields if field.name == declared_name or field.external_name == declared_name)
    if not matches:
        raise TaggedUnionDeclarationError(
            f"Spec branch {branch.spec_type.__qualname__!r} has no discriminator field or alias {declared_name!r}"
        )
    return matches[0], serializers


def _single_literal(schema: Schema) -> LiteralValue | None:
    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    if not isinstance(schema, LiteralSchema) or len(schema.values) != 1:
        return None
    return next(iter(schema.values))


def _json_tag(tag: LiteralValue) -> LiteralValue:
    value = tag.value.value if isinstance(tag.value, Enum) else tag.value
    if type(value) not in (str, int, bool):
        raise TaggedUnionDeclarationError(
            "discriminator tags require str, int, bool, or Enum members with those JSON values"
        )
    return LiteralValue(type(value), value)


def _literal_sort_key(value: LiteralValue) -> tuple[str, str, str]:
    item = value.value
    label = item.name if isinstance(item, Enum) else repr(item)
    return value.python_type.__module__, value.python_type.__qualname__, label


def _apply_constraints(schema: Schema, declared: tuple[Constraint, ...]) -> Schema:
    if not declared:
        return schema
    if isinstance(schema, ConstrainedSchema):
        base = schema.schema
        declared = (*schema.constraints, *declared)
    else:
        base = schema
    constraints = _normalize_constraints(base, declared)
    if not constraints:
        return base
    return ConstrainedSchema(base, constraints)


def _normalize_constraints(schema: Schema, declared: tuple[Constraint, ...]) -> tuple[Constraint, ...]:
    numeric_type = _numeric_type(schema)
    numeric = tuple(item for item in declared if isinstance(item, (Gt, Ge, Lt, Le, MultipleOf)))
    lengths = tuple(item for item in declared if isinstance(item, (MinLength, MaxLength)))
    patterns = tuple(item for item in declared if isinstance(item, Pattern))

    if numeric:
        if numeric_type is None:
            raise ConstraintDeclarationError(f"numeric constraints do not apply to {_schema_label(schema)}")
        for constraint in numeric:
            if type(constraint.value) is not numeric_type:
                raise ConstraintDeclarationError(
                    f"{type(constraint).__name__} boundary must be an exact {numeric_type.__name__}"
                )
    if lengths and not _is_sized_schema(schema):
        raise ConstraintDeclarationError(f"length constraints do not apply to {_schema_label(schema)}")
    if patterns and schema != PrimitiveSchema("str"):
        raise ConstraintDeclarationError(f"Pattern does not apply to {_schema_label(schema)}")

    normalized_numeric = _normalize_numeric(numeric_type, numeric)
    normalized_lengths = _normalize_lengths(schema, lengths)
    normalized_patterns = tuple(sorted(set(patterns), key=lambda item: (item.pattern, item.flags)))
    return (*normalized_numeric, *normalized_lengths, *normalized_patterns)


def _numeric_type(schema: Schema) -> type[object] | None:
    if isinstance(schema, AliasSchema):
        return _numeric_type(schema.schema)
    if isinstance(schema, ConstrainedSchema):
        return _numeric_type(schema.schema)
    if schema == PrimitiveSchema("int"):
        return int
    if schema == PrimitiveSchema("float"):
        return float
    if isinstance(schema, TypeSchema) and schema.python_type is Decimal:
        return Decimal
    return None


def _is_sized_schema(schema: Schema) -> bool:
    if isinstance(schema, AliasSchema):
        return _is_sized_schema(schema.schema)
    if isinstance(schema, ConstrainedSchema):
        return _is_sized_schema(schema.schema)
    return schema in (PrimitiveSchema("str"), PrimitiveSchema("bytes")) or isinstance(
        schema, (SequenceSchema, MappingSchema, VariadicTupleSchema, FixedTupleSchema)
    )


def _normalize_numeric(
    numeric_type: type[object] | None,
    constraints: tuple[Constraint, ...],
) -> tuple[Constraint, ...]:
    lowers = [item for item in constraints if isinstance(item, (Gt, Ge))]
    uppers = [item for item in constraints if isinstance(item, (Lt, Le))]
    multiples = [item for item in constraints if isinstance(item, MultipleOf)]
    lower = max(lowers, key=lambda item: (item.value, isinstance(item, Gt)), default=None)
    upper = min(uppers, key=lambda item: (item.value, not isinstance(item, Lt)), default=None)

    if lower is not None and upper is not None:
        if numeric_type is int:
            minimum = lower.value + int(isinstance(lower, Gt))
            maximum = upper.value - int(isinstance(upper, Lt))
            contradictory = minimum > maximum
        else:
            contradictory = lower.value > upper.value or (
                lower.value == upper.value and (isinstance(lower, Gt) or isinstance(upper, Lt))
            )
        if contradictory:
            raise ConstraintDeclarationError(f"contradictory numeric bounds: {lower!r} and {upper!r}")

    normalized_multiples = {MultipleOf(abs(item.value)) for item in multiples}
    ordered_multiples = tuple(sorted(normalized_multiples, key=lambda item: item.value))
    return tuple(item for item in (lower, upper, *ordered_multiples) if item is not None)


def _normalize_lengths(schema: Schema, constraints: tuple[Constraint, ...]) -> tuple[Constraint, ...]:
    minimums = [item for item in constraints if isinstance(item, MinLength)]
    maximums = [item for item in constraints if isinstance(item, MaxLength)]
    minimum = max(minimums, key=lambda item: item.value, default=None)
    maximum = min(maximums, key=lambda item: item.value, default=None)
    if isinstance(schema, FixedTupleSchema):
        size = len(schema.items)
        if (minimum is not None and size < minimum.value) or (maximum is not None and size > maximum.value):
            raise ConstraintDeclarationError(f"fixed tuple length {size} contradicts declared length constraints")
        return ()
    if minimum is not None and maximum is not None and minimum.value > maximum.value:
        raise ConstraintDeclarationError(f"contradictory length bounds: {minimum!r} and {maximum!r}")
    return tuple(item for item in (minimum, maximum) if item is not None)


def _schema_label(schema: Schema) -> str:
    if isinstance(schema, AliasSchema):
        return schema.name
    if isinstance(schema, PrimitiveSchema):
        return "None" if schema.kind == "none" else schema.kind
    if isinstance(schema, TypeSchema):
        return schema.python_type.__qualname__
    return type(schema).__name__
