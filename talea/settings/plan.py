"""Compile and execute the canonical Talea Settings source-resolution plan."""

import os
import stat
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import BinaryIO, Callable, Literal, cast, overload

from talea.errors import ValidationError
from talea.introspection import FieldInfo, inspect_spec
from talea.resources import ResourceLimitError
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    NamedReferenceSchema,
    PrimitiveSchema,
    RepresentationSchema,
    Schema,
    SpecReferenceSchema,
    TypedDictSchema,
    UnionSchema,
)
from talea.spec import Spec

from .decoding import TextDecoder, compile_text_decoder
from .models import SettingsInfo, SettingSource, SettingsPolicy, SettingsResult

_DELIMITER = "__"
_SOURCE_ORDER: tuple[SettingSource, ...] = ("override", "environment", "secret", "toml", "default")
_SUPPORTS_SECURE_DIR_FD = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")


@dataclass(frozen=True, slots=True)
class _FieldPlan:
    canonical: str
    external: str
    accepted: tuple[str, ...]
    decoder: TextDecoder
    sensitive: bool
    nested: "_ObjectPlan | None"


@dataclass(frozen=True, slots=True)
class _ObjectPlan:
    fields: tuple[_FieldPlan, ...]


@dataclass(frozen=True, slots=True)
class _SourceBinding:
    canonical_path: tuple[str, ...]
    external_path: tuple[str, ...]
    accepted_path: tuple[str, ...]
    decoder: TextDecoder
    sensitive: bool


@dataclass(frozen=True, slots=True)
class _Contribution:
    canonical_path: tuple[str, ...]
    external_path: tuple[object, ...]
    value: object
    source: SettingSource


class _SourceBudget:
    __slots__ = ("maximum", "observed")

    def __init__(self, maximum: int | None) -> None:
        self.maximum = maximum
        self.observed = 0

    def consume(self, amount: int) -> None:
        self.observed += amount

    def consume_text(self, text: str) -> None:
        """Charge UTF-8 bytes without first allocating an unbounded encoding."""

        maximum = self.maximum
        observed = self.observed
        if text.isascii():
            observed += len(text)
            if maximum is not None and observed > maximum:
                text = ""
                raise ResourceLimitError("settings_source_bytes", maximum, observed)
            self.observed = observed
            return
        for character in text:
            observed += len(character.encode("utf-8"))
            if maximum is not None and observed > maximum:
                text = ""
                character = ""
                raise ResourceLimitError("settings_source_bytes", maximum, observed)
        self.observed = observed


class _OverrideState:
    """Bound and detect cycles in one application-supplied override traversal."""

    __slots__ = ("active", "entries", "maximum_depth", "maximum_entries", "maximum_key_bytes")

    def __init__(self, policy: SettingsPolicy) -> None:
        self.active: set[int] = set()
        self.entries = 0
        self.maximum_depth = policy.max_override_depth
        self.maximum_entries = policy.max_override_entries
        self.maximum_key_bytes = policy.max_override_key_bytes

    def enter(self, source: Mapping[str, object], depth: int) -> None:
        maximum = self.maximum_depth
        if maximum is not None and depth > maximum:
            source = {}
            raise ResourceLimitError("settings_override_depth", maximum, depth)
        identity = id(source)
        if identity in self.active:
            source = {}
            raise ValueError("cyclic settings override mapping")
        self.active.add(identity)

    def leave(self, source: Mapping[str, object]) -> None:
        self.active.remove(id(source))

    def consume_entry(self) -> None:
        self.entries += 1
        maximum = self.maximum_entries
        if maximum is not None and self.entries > maximum:
            raise ResourceLimitError("settings_override_entries", maximum, self.entries)

    def consume_key(self, key: str) -> None:
        maximum = self.maximum_key_bytes
        if maximum is None:
            return
        if key.isascii():
            observed = len(key)
            if observed > maximum:
                key = ""
                raise ResourceLimitError("settings_override_key_bytes", maximum, observed)
            return
        observed = 0
        for character in key:
            observed += len(character.encode("utf-8"))
            if observed > maximum:
                key = ""
                character = ""
                raise ResourceLimitError("settings_override_key_bytes", maximum, observed)


class Settings[SettingsT: Spec]:
    """Retain one immutable, schema-derived settings source plan.

    The model must be a concrete Talea :class:`Spec`. Plan construction reads
    only Talea's canonical introspection and schema truth, compiles finite
    environment and secret names, and retains no source values. Each
    :meth:`load` snapshots its inputs, resolves canonical leaves in the fixed
    order override > environment > secret > TOML > default, and delegates the
    final structure to ``model.from_mapping``.
    """

    __slots__ = (
        "_case_sensitive",
        "_environment",
        "_info",
        "_model",
        "_object",
        "_policy",
        "_secrets",
        "_secret_names",
        "_toml",
    )

    _case_sensitive: bool
    _environment: Mapping[str, _SourceBinding]
    _info: SettingsInfo
    _model: type[SettingsT]
    _object: _ObjectPlan
    _policy: SettingsPolicy
    _secrets: Path | None
    _secret_names: Mapping[str, _SourceBinding]
    _toml: Path | None

    def __init__(
        self,
        model: type[SettingsT],
        *,
        prefix: str = "",
        case_sensitive: bool = False,
        toml: str | os.PathLike[str] | None = None,
        secrets: str | os.PathLike[str] | None = None,
        policy: SettingsPolicy | None = None,
    ) -> None:
        """Compile source names and immutable configuration for ``model``.

        ``prefix`` applies only to process-environment names. TOML uses nested
        tables and secrets use flat filenames joined with ``__``. Explicit
        paths are never searched or discovered; a configured missing path is a
        source acquisition error during :meth:`load`.
        """

        if not isinstance(model, type) or not getattr(model, "__talea_spec__", False) or model is Spec:
            raise TypeError("Settings requires a concrete Spec model")
        if type(prefix) is not str:
            raise TypeError("prefix must be a str")
        if any(character in prefix for character in ("\x00", "\n", "\r", "=")):
            raise ValueError("prefix cannot contain NUL, newline, carriage return, or '='")
        if type(case_sensitive) is not bool:
            raise TypeError("case_sensitive must be bool")
        selected_policy = SettingsPolicy() if policy is None else policy
        if not isinstance(selected_policy, SettingsPolicy):
            raise TypeError("policy must be a SettingsPolicy or None")
        info = inspect_spec(model)
        if info.generic_parameters:
            raise TypeError("Settings requires a concrete, fully specialized Spec model")
        if info.derivation is not None and info.derivation.partial:
            raise TypeError("Settings does not accept partial derived Spec roots")
        object_plan = _spec_object(model, frozenset({model}))
        environment, environment_display = _compile_source_names(
            object_plan,
            prefix,
            case_sensitive,
            selected_policy.max_source_names,
        )
        secret_names, _ = _compile_source_names(
            object_plan,
            "",
            case_sensitive,
            selected_policy.max_source_names,
        )
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_object", object_plan)
        object.__setattr__(self, "_case_sensitive", case_sensitive)
        object.__setattr__(self, "_environment", environment)
        object.__setattr__(self, "_secret_names", secret_names)
        object.__setattr__(self, "_toml", None if toml is None else Path(toml))
        object.__setattr__(self, "_secrets", None if secrets is None else Path(secrets))
        object.__setattr__(self, "_policy", selected_policy)
        object.__setattr__(
            self,
            "_info",
            SettingsInfo(model, _SOURCE_ORDER, prefix, _DELIMITER, case_sensitive, environment_display),
        )

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation of a retained source plan."""

        raise AttributeError("Settings plans are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject deletion from a retained source plan."""

        raise AttributeError("Settings plans are immutable")

    @property
    def info(self) -> SettingsInfo:
        """Return immutable, callback-free plan introspection."""

        return self._info

    @overload
    def load(
        self,
        overrides: Mapping[str, object] | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        provenance: Literal[False] = False,
    ) -> SettingsT: ...

    @overload
    def load(
        self,
        overrides: Mapping[str, object] | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        provenance: Literal[True],
    ) -> SettingsResult[SettingsT]: ...

    def load(
        self,
        overrides: Mapping[str, object] | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        provenance: bool = False,
    ) -> SettingsT | SettingsResult[SettingsT]:
        """Load one complete immutable snapshot from fresh source snapshots.

        ``environment=None`` copies ``os.environ`` once. Supplying a finite
        mapping provides deterministic tests without mutating process state.
        Overrides use ordinary nested Mapping semantics and are copied while
        traversed. A failed operation retains no merge or source state.
        """

        if overrides is not None and not isinstance(overrides, Mapping):
            raise TypeError("overrides must be a Mapping or None")
        if environment is not None and not isinstance(environment, Mapping):
            raise TypeError("environment must be a Mapping or None")
        if type(provenance) is not bool:
            raise TypeError("provenance must be bool")
        budget = _SourceBudget(self._policy.max_source_bytes)
        resolved_environment: dict[tuple[str, ...], tuple[_SourceBinding, str, str]] = {}
        winners: dict[tuple[str, ...], _Contribution] | None = None
        external: dict[str, object] = {}
        contributions: list[_Contribution] = []
        try:
            if self._toml is None and self._secrets is None and overrides is None:
                resolved_environment = self._resolve_environment(environment, budget)
                external = _materialize_resolved(resolved_environment.values())
            else:
                winners = {}
                if self._toml is not None:
                    _merge(winners, self._load_toml(budget))
                if self._secrets is not None:
                    _merge_leaves(winners, self._load_secrets(budget))
                resolved_environment = self._resolve_environment(environment, budget)
                _merge_leaves(winners, _environment_contributions(resolved_environment))
                if overrides is not None:
                    _collect_mapping(
                        self._object,
                        overrides,
                        (),
                        (),
                        "override",
                        self._normalize,
                        self._model.__name__,
                        contributions,
                        _OverrideState(self._policy),
                    )
                    _merge(winners, contributions)
                external = _materialize(winners.values())
            failure: ValidationError | None = None
            try:
                value = self._model.from_mapping(external, policy=self._policy.input_policy)
            except ValidationError as error:
                sensitive = winners is not None and any(item.source == "secret" for item in winners.values())
                failure = error.prefixed((), sensitive=sensitive)
            if failure is not None:
                external = {}
                winners = None
                resolved_environment = {}
                raise failure from None
            if not provenance:
                return value
            if winners is None:
                winners = {
                    path: _Contribution(path, binding.external_path, None, "environment")
                    for path, (binding, _, _) in resolved_environment.items()
                }
            origins = _provenance(self._object, winners)
            return SettingsResult(value, origins)
        except BaseException:
            overrides = None
            environment = None
            external = {}
            winners = None
            resolved_environment = {}
            contributions.clear()
            raise

    def _normalize(self, name: str) -> str:
        return name if self._case_sensitive else name.casefold()

    def _resolve_environment(
        self,
        supplied: Mapping[str, str] | None,
        budget: _SourceBudget,
    ) -> dict[tuple[str, ...], tuple[_SourceBinding, str, str]]:
        source = os.environ if supplied is None else supplied
        maximum = self._policy.max_environment_entries
        resolved: dict[tuple[str, ...], tuple[_SourceBinding, str, str]] = {}
        normalizer = None if self._case_sensitive else str.casefold
        observed = 0
        key = ""
        text = ""
        try:
            for key, text in source.items():
                observed += 1
                if maximum is not None and observed > maximum:
                    raise ResourceLimitError("settings_environment_entries", maximum, observed)
                if type(key) is not str or type(text) is not str:
                    raise TypeError("environment keys and values must be str")
                budget.consume_text(key)
                normalized = key if normalizer is None else normalizer(key)
                binding = self._environment.get(normalized)
                if binding is None:
                    continue
                budget.consume_text(text)
                previous = resolved.get(binding.canonical_path)
                if previous is not None:
                    _raise_source_conflict(self._model, binding, previous[1], binding.accepted_path[-1])
                resolved[binding.canonical_path] = (binding, binding.accepted_path[-1], text)
            return resolved
        except BaseException:
            supplied = None
            source = {}
            resolved.clear()
            key = ""
            text = ""
            raise

    def _load_toml(self, budget: _SourceBudget) -> list[_Contribution]:
        assert self._toml is not None
        data = b""
        text = ""
        parsed: dict[str, object] = {}
        contributions: list[_Contribution] = []
        try:
            data = _bounded_file(
                self._toml,
                self._policy.max_toml_bytes,
                "settings_toml_bytes",
                budget,
            )
            budget.consume(len(data))
            text = _decode_source_utf8(data, "TOML")
            parsed = _parse_toml(text)
            _collect_mapping(
                self._object,
                parsed,
                (),
                (),
                "toml",
                self._normalize,
                self._model.__name__,
                contributions,
            )
            return contributions
        except BaseException:
            data = b""
            text = ""
            parsed.clear()
            contributions.clear()
            raise

    def _load_secrets(self, budget: _SourceBudget) -> list[_Contribution]:
        assert self._secrets is not None
        root = self._secrets.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(str(self._secrets))
        root_fd: int | None = None
        entries: list[str] = []
        files: list[tuple[str, tuple[str, ...]]] = []
        resolved: dict[tuple[str, ...], tuple[_SourceBinding, str, tuple[str, ...]]] = {}
        contributions: list[_Contribution] = []
        data = b""
        text = ""
        try:
            anchored = _SUPPORTS_SECURE_DIR_FD
            if anchored:
                root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                opened = os.fstat(root_fd)
                current = root.stat()
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise OSError("configured secret directory changed during acquisition")
                scan_root: int | Path = root_fd
            else:
                scan_root = root
            maximum = self._policy.max_secret_files
            with os.scandir(scan_root) as directory:
                for directory_entry in directory:
                    entries.append(directory_entry.name)
                    if maximum is not None and len(entries) > maximum:
                        raise ResourceLimitError("settings_secret_files", maximum, len(entries))
            for name in entries:
                entry = root / name
                target = entry.resolve(strict=True)
                if target.is_dir():
                    continue
                try:
                    relative = target.relative_to(root)
                except ValueError:
                    raise ValueError("secret symlink target must remain within the configured directory") from None
                if not anchored and target != entry:
                    raise ValueError("secret symlinks require descriptor-relative platform support")
                files.append((name, relative.parts))
            for name, relative in files:
                binding = self._secret_names.get(self._normalize(name))
                if binding is None:
                    continue
                previous = resolved.get(binding.canonical_path)
                if previous is not None:
                    _raise_source_conflict(self._model, binding, previous[1], binding.accepted_path[-1])
                resolved[binding.canonical_path] = (binding, binding.accepted_path[-1], relative)
            for path, (binding, _, relative) in resolved.items():
                if root_fd is None:
                    data = _bounded_verified_file(
                        root.joinpath(*relative),
                        self._policy.max_secret_file_bytes,
                        "settings_secret_file_bytes",
                        budget,
                    )
                else:
                    data = _bounded_secret_file(
                        root_fd,
                        relative,
                        self._policy.max_secret_file_bytes,
                        budget,
                    )
                budget.consume(len(data))
                text = _decode_source_utf8(data, "secret")
                if text.endswith("\r\n"):
                    text = text[:-2]
                elif text.endswith("\n"):
                    text = text[:-1]
                contributions.append(_Contribution(path, binding.external_path, binding.decoder(text), "secret"))
            return contributions
        except BaseException:
            entries.clear()
            files.clear()
            resolved.clear()
            contributions.clear()
            data = b""
            text = ""
            raise
        finally:
            if root_fd is not None:
                os.close(root_fd)


def _spec_object(model: type[Spec], visiting: frozenset[object]) -> _ObjectPlan:
    fields = tuple(_field_from_info(field, visiting) for field in inspect_spec(model).fields)
    return _ObjectPlan(fields)


def _field_from_info(field: FieldInfo, visiting: frozenset[object]) -> _FieldPlan:
    assert field.schema is not None
    nested = _nested_object(field.schema, visiting)
    return _FieldPlan(
        field.name,
        field.external_name,
        field.accepted_input_names,
        compile_text_decoder(field.schema),
        field.sensitive,
        nested,
    )


def _nested_object(schema: Schema, visiting: frozenset[object]) -> _ObjectPlan | None:
    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    if isinstance(schema, RepresentationSchema):
        return None
    if isinstance(schema, NamedReferenceSchema):
        # Canonical named references are graph back-edges. The defining
        # dataclass/TypedDict identity was added before its fields were walked.
        assert schema.identity in visiting
        return None
    if isinstance(schema, SpecReferenceSchema):
        if schema.spec_type in visiting:
            return None
        return _spec_object(cast(type[Spec], schema.spec_type), visiting | {schema.spec_type})
    if isinstance(schema, DataclassSchema):
        identity = schema.identity or schema.dataclass_type
        fields = tuple(
            _FieldPlan(
                field.name,
                field.external_name,
                field.accepted_input_names,
                compile_text_decoder(field.schema),
                bool(field.metadata.sensitive),
                _nested_object(field.schema, visiting | {identity}),
            )
            for field in schema.fields
            if field.init
        )
        return _ObjectPlan(fields)
    if isinstance(schema, TypedDictSchema):
        identity = schema.identity or (schema.module, schema.name)
        return _ObjectPlan(
            tuple(
                _FieldPlan(
                    field.name,
                    field.name,
                    (field.name,),
                    compile_text_decoder(field.schema),
                    bool(field.metadata.sensitive),
                    _nested_object(field.schema, visiting | {identity}),
                )
                for field in schema.fields
            )
        )
    if isinstance(schema, UnionSchema):
        non_none = tuple(
            option for option in schema.options if not (isinstance(option, PrimitiveSchema) and option.kind == "none")
        )
        if len(non_none) == 1:
            return _nested_object(non_none[0], visiting)
    return None


def _compile_source_names(
    root: _ObjectPlan,
    prefix: str,
    case_sensitive: bool,
    maximum: int | None,
) -> tuple[dict[str, _SourceBinding], tuple[str, ...]]:
    compiled: dict[str, _SourceBinding] = {}
    displays: list[str] = []

    def visit(
        node: _ObjectPlan,
        canonical: tuple[str, ...],
        external: tuple[str, ...],
        accepted: tuple[tuple[str, ...], ...],
    ) -> None:
        for field in node.fields:
            next_canonical = (*canonical, field.canonical)
            next_external = (*external, field.external)
            next_accepted = (*accepted, field.accepted)
            if field.nested is not None:
                visit(field.nested, next_canonical, next_external, next_accepted)
                continue
            for combination in product(*next_accepted):
                display = prefix + _DELIMITER.join(combination)
                normalized = display if case_sensitive else display.casefold()
                if normalized in compiled:
                    other = compiled[normalized]
                    raise ValueError(
                        "settings source-name collision between "
                        f"{'.'.join(other.canonical_path)!r} and {'.'.join(next_canonical)!r}"
                    )
                compiled[normalized] = _SourceBinding(
                    next_canonical,
                    next_external,
                    combination,
                    field.decoder,
                    field.sensitive,
                )
                displays.append(display)
                if maximum is not None and len(compiled) > maximum:
                    raise ResourceLimitError("settings_source_names", maximum, len(compiled))

    visit(root, (), (), ())
    return compiled, tuple(displays)


def _collect_mapping(
    node: _ObjectPlan,
    source: Mapping[str, object],
    canonical: tuple[str, ...],
    external: tuple[str, ...],
    kind: Literal["override", "toml"],
    normalize: Callable[[str], str],
    title: str,
    output: list[_Contribution],
    state: _OverrideState | None = None,
    depth: int = 1,
) -> None:
    items: list[tuple[object, object]] = []
    by_name: dict[str, list[tuple[object, object]]] = {}
    key: object = ""
    value: object = None
    item: tuple[object, object] = ("", None)
    matches: list[tuple[object, object]] = []
    consumed: set[object] = set()
    entered = False
    try:
        if state is not None:
            state.enter(source, depth)
            entered = True
        for key, value in source.items():
            if state is not None:
                state.consume_entry()
                if type(key) is str:
                    state.consume_key(key)
            item = (key, value)
            items.append(item)
            if type(key) is str:
                by_name.setdefault(normalize(key), []).append(item)
        for field in node.fields:
            matches = [
                item for name in field.accepted for item in by_name.get(normalize(name), ()) if item[0] not in consumed
            ]
            if len(matches) > 1:
                binding = _SourceBinding(
                    (*canonical, field.canonical),
                    (*external, field.external),
                    (field.external,),
                    field.decoder,
                    field.sensitive,
                )
                _raise_source_conflict(title, binding, str(matches[0][0]), str(matches[1][0]))
            if not matches:
                continue
            key, value = matches[0]
            consumed.add(key)
            next_canonical = (*canonical, field.canonical)
            next_external = (*external, field.external)
            if field.nested is not None and isinstance(value, Mapping):
                _collect_mapping(
                    field.nested,
                    value,
                    next_canonical,
                    next_external,
                    kind,
                    normalize,
                    title,
                    output,
                    state,
                    depth + 1,
                )
            else:
                output.append(_Contribution(next_canonical, next_external, value, kind))
        for key, value in items:
            if key in consumed:
                continue
            marker = f"\x00{len(output)}"
            output.append(_Contribution((*canonical, marker), (*external, key), value, kind))
    except BaseException:
        if state is not None and entered:
            state.leave(source)
            entered = False
        source = {}
        items.clear()
        by_name.clear()
        key = ""
        value = None
        item = ("", None)
        matches.clear()
        consumed.clear()
        raise
    finally:
        if state is not None and entered:
            state.leave(source)


def _raise_source_conflict(
    model: object,
    binding: _SourceBinding,
    first: str,
    second: str,
) -> None:
    title = model.__name__ if isinstance(model, type) else str(model)
    raise ValidationError._alias_conflict(
        (first, second),
        binding.external_path,
        title=title,
        sensitive=binding.sensitive,
    )


def _merge(target: dict[tuple[str, ...], _Contribution], values: list[_Contribution]) -> None:
    for contribution in values:
        path = contribution.canonical_path
        for length in range(1, len(path) + 1):
            target.pop(path[:length], None)
        for existing in tuple(target):
            if len(existing) > len(path) and existing[: len(path)] == path:
                target.pop(existing)
        target[path] = contribution


def _merge_leaves(target: dict[tuple[str, ...], _Contribution], values: list[_Contribution]) -> None:
    """Merge schema-proven leaf contributions without descendant scans."""

    for contribution in values:
        path = contribution.canonical_path
        for length in range(1, len(path)):
            target.pop(path[:length], None)
        target[path] = contribution


def _environment_contributions(
    resolved: Mapping[tuple[str, ...], tuple[_SourceBinding, str, str]],
) -> list[_Contribution]:
    return [
        _Contribution(path, binding.external_path, binding.decoder(text), "environment")
        for path, (binding, _, text) in resolved.items()
    ]


def _is_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) <= len(right) and right[: len(left)] == left


def _materialize(values: Iterable[_Contribution]) -> dict[str, object]:
    result: dict[object, object] = {}
    for contribution in values:
        current = result
        for segment in contribution.external_path[:-1]:
            nested = current.get(segment)
            if not isinstance(nested, dict):
                nested = {}
                current[segment] = nested
            current = nested
        current[contribution.external_path[-1]] = contribution.value
    return cast(dict[str, object], result)


def _materialize_resolved(
    values: Iterable[tuple[_SourceBinding, str, str]],
) -> dict[str, object]:
    result: dict[object, object] = {}
    for binding, _, text in values:
        current = result
        for segment in binding.external_path[:-1]:
            nested = current.get(segment)
            if not isinstance(nested, dict):
                nested = {}
                current[segment] = nested
            current = nested
        current[binding.external_path[-1]] = binding.decoder(text)
    return cast(dict[str, object], result)


def _provenance(
    root: _ObjectPlan,
    winners: Mapping[tuple[str, ...], _Contribution],
) -> dict[tuple[str, ...], SettingSource]:
    origins = {path: contribution.source for path, contribution in winners.items() if "\x00" not in path[-1]}

    def visit(node: _ObjectPlan, path: tuple[str, ...]) -> None:
        for field in node.fields:
            field_path = (*path, field.canonical)
            if any(_is_prefix(winner, field_path) for winner in origins):
                continue
            if field.nested is None:
                origins[field_path] = "default"
            else:
                visit(field.nested, field_path)

    visit(root, ())
    return origins


def _decode_source_utf8(data: bytes, source: Literal["TOML", "secret"]) -> str:
    """Decode source text without retaining rejected bytes in exception state."""

    text: str | None = None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        data = b""
    if text is None:
        raise ValueError(f"settings {source} is not valid UTF-8") from None
    return text


def _parse_toml(text: str) -> dict[str, object]:
    """Parse TOML without retaining its document in a public parse failure."""

    parsed: dict[str, object] | None = None
    message = ""
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        message = f"malformed settings TOML at line {error.lineno}, column {error.colno}"
        text = ""
    if parsed is None:
        raise ValueError(message) from None
    return parsed


def _bounded_file(
    path: Path,
    maximum: int | None,
    code: Literal["settings_secret_file_bytes", "settings_toml_bytes"],
    budget: _SourceBudget,
) -> bytes:
    if not path.is_file():
        if path.exists():
            raise IsADirectoryError(str(path))
        raise FileNotFoundError(str(path))
    read_maximum = maximum
    if budget.maximum is not None:
        remaining = budget.maximum - budget.observed
        if read_maximum is None or remaining < read_maximum:
            read_maximum = remaining
    with path.open("rb") as stream:
        data = _read_bounded(stream, read_maximum)
    if maximum is not None and len(data) > maximum:
        observed = len(data)
        data = b""
        raise ResourceLimitError(code, maximum, observed)
    if budget.maximum is not None and budget.observed + len(data) > budget.maximum:
        observed = budget.observed + len(data)
        data = b""
        raise ResourceLimitError("settings_source_bytes", budget.maximum, observed)
    return data


def _bounded_verified_file(
    path: Path,
    maximum: int | None,
    code: Literal["settings_secret_file_bytes", "settings_toml_bytes"],
    budget: _SourceBudget,
) -> bytes:
    """Open one direct file and verify the descriptor still names that inode."""

    expected = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(expected.st_mode):
        raise IsADirectoryError(str(path))
    file_fd = os.open(path, os.O_RDONLY)
    try:
        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise OSError("configured secret file changed during acquisition")
        read_maximum = maximum
        if budget.maximum is not None:
            remaining = budget.maximum - budget.observed
            if read_maximum is None or remaining < read_maximum:
                read_maximum = remaining
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = -1
            data = _read_bounded(stream, read_maximum)
        if maximum is not None and len(data) > maximum:
            observed = len(data)
            data = b""
            raise ResourceLimitError(code, maximum, observed)
        if budget.maximum is not None and budget.observed + len(data) > budget.maximum:
            observed = budget.observed + len(data)
            data = b""
            raise ResourceLimitError("settings_source_bytes", budget.maximum, observed)
        return data
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _bounded_secret_file(
    root_fd: int,
    relative: tuple[str, ...],
    maximum: int | None,
    budget: _SourceBudget,
) -> bytes:
    """Read one authorized regular file beneath an already-open directory."""

    if not relative:
        raise IsADirectoryError("configured secret path resolved to its directory")
    directory_fd = os.dup(root_fd)
    file_fd: int | None = None
    try:
        for segment in relative[:-1]:
            next_fd = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(relative[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise IsADirectoryError(relative[-1])
        read_maximum = maximum
        if budget.maximum is not None:
            remaining = budget.maximum - budget.observed
            if read_maximum is None or remaining < read_maximum:
                read_maximum = remaining
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = None
            data = _read_bounded(stream, read_maximum)
        if maximum is not None and len(data) > maximum:
            observed = len(data)
            data = b""
            raise ResourceLimitError("settings_secret_file_bytes", maximum, observed)
        if budget.maximum is not None and budget.observed + len(data) > budget.maximum:
            observed = budget.observed + len(data)
            data = b""
            raise ResourceLimitError("settings_source_bytes", budget.maximum, observed)
        return data
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _read_bounded(stream: BinaryIO, maximum: int | None) -> bytes:
    """Read at most one byte beyond a selected source limit."""

    if maximum is None:
        return stream.read()
    return stream.read(maximum + 1)
