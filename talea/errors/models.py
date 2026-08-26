"""Canonical Talea failure detail, public projection, and rendering."""

from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict, cast

from talea.errors.codes import ErrorCode
from talea.errors.safety import (
    JsonScalar,
    _InputSnapshot,
    safe_text,
    safe_type_name,
    snapshot_input,
)

__all__ = ["ErrorBranchData", "ErrorData", "ErrorLocation", "ValidationError"]

type ErrorLocation = tuple[object, ...]
type CustomStage = Literal["transform", "field_check", "spec_check"]


class ErrorBranchData(TypedDict):
    """JSON-compatible projection of one attempted union alternative."""

    label: str
    errors: list["ErrorData"]


class ErrorData(TypedDict):
    """JSON-compatible projection returned by :meth:`ValidationError.errors`.

    ``code``, ``location``, and ``message`` are always present. Other keys
    appear only when meaningful to that failure. Each call returns fresh lists
    and dictionaries, so callers may adapt a response without mutating the
    exception's canonical detail.
    """

    code: str
    location: list[JsonScalar]
    message: str
    expected: NotRequired[str]
    received: NotRequired[str]
    input: NotRequired[JsonScalar]
    context: NotRequired[dict[str, JsonScalar]]
    hook: NotRequired[str]
    stage: NotRequired[CustomStage]
    locations: NotRequired[list[list[JsonScalar]]]
    branches: NotRequired[list[ErrorBranchData]]


@dataclass(frozen=True, slots=True)
class _UnionBranch:
    label: str
    details: tuple["_ErrorDetail", ...]


@dataclass(frozen=True, slots=True)
class _ErrorDetail:
    """Own one immutable failure fact without presentation-time state."""

    code: ErrorCode
    location: ErrorLocation
    expected: str | None
    received: str | None
    input: _InputSnapshot | None
    context: tuple[tuple[str, JsonScalar], ...] = ()
    hook: str | None = None
    stage: CustomStage | None = None
    related_locations: tuple[ErrorLocation, ...] = ()
    branches: tuple[_UnionBranch, ...] = ()
    projected_location: tuple[JsonScalar, ...] = field(init=False, repr=False)
    projected_related_locations: tuple[tuple[JsonScalar, ...], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "projected_location", tuple(_project_segment(item) for item in self.location))
        object.__setattr__(
            self,
            "projected_related_locations",
            tuple(tuple(_project_segment(item) for item in location) for location in self.related_locations),
        )

    @property
    def message(self) -> str:
        """Project stable failure facts into concise human wording."""

        context = dict(self.context)
        if self.code is ErrorCode.TYPE:
            return f"Expected {safe_text(self.expected or '')}"
        if self.code is ErrorCode.LITERAL:
            return f"Expected {safe_text(self.expected or '')}"
        if self.code is ErrorCode.UNION:
            return f"Expected one of: {safe_text(self.expected or '')}"
        if self.code is ErrorCode.MISSING:
            return "Required field is missing"
        if self.code is ErrorCode.UNEXPECTED:
            return "Unexpected field"
        if self.code is ErrorCode.GREATER_THAN:
            return f"Expected value > {_context_text(context, 'limit')}"
        if self.code is ErrorCode.GREATER_THAN_OR_EQUAL:
            return f"Expected value >= {_context_text(context, 'limit')}"
        if self.code is ErrorCode.LESS_THAN:
            return f"Expected value < {_context_text(context, 'limit')}"
        if self.code is ErrorCode.LESS_THAN_OR_EQUAL:
            return f"Expected value <= {_context_text(context, 'limit')}"
        if self.code is ErrorCode.MULTIPLE_OF:
            return f"Expected a multiple of {_context_text(context, 'multiple_of')}"
        if self.code is ErrorCode.MIN_LENGTH:
            return f"Expected length >= {_context_text(context, 'minimum')}"
        if self.code is ErrorCode.MAX_LENGTH:
            return f"Expected length <= {_context_text(context, 'maximum')}"
        if self.code is ErrorCode.PATTERN:
            return f"Expected a match for pattern {_context_text(context, 'pattern')}"
        if self.code is ErrorCode.TRANSFORM:
            return f"Transform {self.hook!r} rejected the input"
        if self.code is ErrorCode.FIELD_CHECK:
            return f"Field check {self.hook!r} rejected the value"
        if self.code is ErrorCode.SPEC_CHECK:
            return f"Spec check {self.hook!r} rejected the values"
        if self.code is ErrorCode.FACTORY:
            return "Default factory failed"
        if self.code is ErrorCode.JSON_INVALID:
            return "Invalid JSON input"
        if self.code is ErrorCode.JSON_DUPLICATE:
            return "Duplicate JSON object key"
        raise AssertionError("unknown canonical Talea error code")


def _context_text(context: dict[str, JsonScalar], key: str) -> str:
    value = context.get(key)
    return snapshot_input(value).rendered


def _project_segment(segment: object) -> JsonScalar:
    return snapshot_input(segment).projection


def _project_detail(detail: _ErrorDetail) -> ErrorData:
    projected = ErrorData(
        code=detail.code.value,
        location=list(detail.projected_location),
        message=detail.message,
    )
    if detail.expected is not None:
        projected["expected"] = safe_text(detail.expected)
    if detail.received is not None:
        projected["received"] = detail.received
    if detail.input is not None:
        projected["input"] = detail.input.projection
    if detail.context:
        projected["context"] = dict(detail.context)
    if detail.hook is not None:
        projected["hook"] = detail.hook
    if detail.stage is not None:
        projected["stage"] = detail.stage
    if detail.related_locations:
        projected["locations"] = [list(location) for location in detail.projected_related_locations]
    if detail.branches:
        projected["branches"] = [
            ErrorBranchData(label=branch.label, errors=[_project_detail(item) for item in branch.details])
            for branch in detail.branches
        ]
    return projected


def _normalize_context(context: tuple[tuple[str, object], ...]) -> tuple[tuple[str, JsonScalar], ...]:
    return tuple((safe_text(key, 64), snapshot_input(value).projection) for key, value in context)


class ValidationError(TypeError):
    """Report one or more Talea validation failures.

    Validators and Spec constructors raise this exception after structural,
    constraint, factory, or custom validation rejects input. ``errors()``
    returns fresh JSON-compatible dictionaries containing stable codes and
    structured locations. Human rendering is lazy over immutable failure
    detail, while all potentially hostile input representation is bounded when
    the failure is created. Successful validation allocates none of this state.

    ``location`` is a tuple of field names, sequence indexes, mapping keys, or
    set members. Custom callback and factory exceptions remain available as
    ``__cause__``; message strings are presentation and should never be parsed
    as machine contracts.
    """

    def __init__(
        self,
        expected: str | None,
        value: object,
        location: ErrorLocation,
        code: ErrorCode | str = ErrorCode.TYPE,
        *,
        title: str | None = None,
        context: tuple[tuple[str, object], ...] = (),
        hook: str | None = None,
        stage: CustomStage | None = None,
        related_locations: tuple[ErrorLocation, ...] = (),
    ) -> None:
        normalized_code = ErrorCode(code)
        detail = _ErrorDetail(
            normalized_code,
            location,
            expected,
            safe_type_name(value),
            snapshot_input(value),
            _normalize_context(context),
            safe_text(hook, 96) if hook is not None else None,
            stage,
            related_locations,
        )
        self._initialize((detail,), (value,), title)

    def _initialize(
        self,
        details: tuple[_ErrorDetail, ...],
        values: tuple[object, ...],
        title: str | None,
    ) -> None:
        TypeError.__init__(self)
        self._details = details
        self._values = values
        self.title = safe_text(title) if title is not None else None

    @classmethod
    def _missing(cls, location: ErrorLocation, *, title: str) -> "ValidationError":
        """Build one field-omission detail without manufacturing an input value."""

        error = cls.__new__(cls)
        error._initialize(
            (_ErrorDetail(ErrorCode.MISSING, location, None, None, None),),
            (None,),
            title,
        )
        return error

    @classmethod
    def _aggregate(
        cls,
        failures: tuple["ValidationError", ...],
        *,
        title: str,
    ) -> "ValidationError":
        """Flatten independent boundary failures into one canonical exception."""

        if not failures:
            raise ValueError("validation error aggregation requires at least one failure")
        error = cls.__new__(cls)
        error._initialize(
            tuple(detail for failure in failures for detail in failure._details),
            tuple(value for failure in failures for value in failure._values),
            title,
        )
        causes = tuple(failure.__cause__ for failure in failures)
        if causes and causes[0] is not None and all(cause is causes[0] for cause in causes):
            error.__cause__ = causes[0]
        return error

    @classmethod
    def union(
        cls,
        expected: str,
        value: object,
        location: ErrorLocation,
        alternatives: tuple[str, ...],
        failures: tuple[tuple[str, "ValidationError"], ...] = (),
        *,
        title: str | None = None,
    ) -> "ValidationError":
        """Build one compact union failure from branch-local failures."""

        if failures:
            branches = tuple(_UnionBranch(safe_text(label), failure._details) for label, failure in failures)
        else:
            branches = tuple(
                _UnionBranch(
                    safe_text(label),
                    (
                        _ErrorDetail(
                            ErrorCode.TYPE,
                            location,
                            safe_text(label),
                            safe_type_name(value),
                            snapshot_input(value),
                        ),
                    ),
                )
                for label in alternatives
            )
        detail = _ErrorDetail(
            ErrorCode.UNION,
            location,
            safe_text(expected),
            safe_type_name(value),
            snapshot_input(value),
            branches=branches,
        )
        error = cls.__new__(cls)
        error._initialize((detail,), (value,), title)
        causes = tuple(failure.__cause__ for _, failure in failures if failure.__cause__ is not None)
        if causes and all(cause is causes[0] for cause in causes):
            error.__cause__ = causes[0]
        return error

    def prefixed(self, prefix: ErrorLocation, *, title: str | None = None) -> "ValidationError":
        """Return the same failure truth beneath a longer root location."""

        error = type(self).__new__(type(self))
        error._initialize(
            tuple(_prefix_detail(detail, prefix) for detail in self._details),
            self._values,
            title if title is not None else self.title,
        )
        if isinstance(self, CustomValidationError):
            custom_error = cast(CustomValidationError, error)
            custom_error.stage = self.stage
            custom_error.hook = self.hook
            custom_error.locations = tuple((*prefix, *location) for location in self.locations)
        error.__cause__ = self.__cause__
        return error

    @property
    def code(self) -> ErrorCode:
        """Return the first failure's stable machine-readable code."""

        return self._details[0].code

    @property
    def expected(self) -> str:
        """Return the first failure's expected contract, if one exists."""

        return self._details[0].expected or ""

    @property
    def value(self) -> object:
        """Return the exact first rejected object for Python debugging."""

        return self._values[0]

    @property
    def received_type(self) -> type[object]:
        """Return the concrete type of the first rejected object."""

        return type(self.value)

    @property
    def location(self) -> ErrorLocation:
        """Return the first root-relative structured failure location."""

        return self._details[0].location

    def errors(self) -> list[ErrorData]:
        """Return fresh JSON-compatible projections of every top-level error.

        Locations become JSON arrays while retaining each segment as a distinct
        value. Unsafe mapping keys, set members, containers, bytes, and custom
        objects are represented by bounded strings. Mutating the returned data
        never changes this exception or a later projection.
        """

        return [_project_detail(detail) for detail in self._details]

    def __str__(self) -> str:
        """Render a concise multiline description for logs and terminals."""

        header = self.title or "Validation error"
        if len(self._details) != 1:
            header = f"{header} ({len(self._details)} errors)"
        lines = [header]
        for detail in self._details:
            _render_detail(lines, detail, 1)
        return "\n".join(lines)


class CustomValidationError(ValidationError):
    """Compatibility subtype for deliberate custom-hook rejections.

    Custom failures use the same canonical details, ``errors()`` projection,
    and rendering as every :class:`ValidationError`. The subtype remains for
    code written against Talea's Campaign 7 API; normal application handling
    only needs to catch ``ValidationError``.
    """

    def __init__(
        self,
        stage: CustomStage,
        hook: str,
        value: object,
        locations: tuple[ErrorLocation, ...],
        *,
        title: str | None = None,
    ) -> None:
        self.stage = stage
        self.hook = hook
        self.locations = locations
        location = locations[0] if len(locations) == 1 else ()
        super().__init__(
            None,
            value,
            location,
            ErrorCode(stage),
            title=title,
            hook=hook,
            stage=stage,
            related_locations=locations if len(locations) > 1 else (),
        )


def _render_detail(lines: list[str], detail: _ErrorDetail, indentation: int) -> None:
    prefix = "  " * indentation
    lines.append(f"{prefix}{_format_location(detail.projected_location)}")
    lines.append(f"{prefix}  {detail.message}")
    if detail.input is not None and detail.received is not None:
        lines.append(f"{prefix}  received: {detail.input.rendered} ({detail.received})")
    if detail.related_locations:
        related = ", ".join(_format_location(location) for location in detail.projected_related_locations)
        lines.append(f"{prefix}  fields: {related}")
    if detail.branches:
        lines.append(f"{prefix}  alternatives:")
        for branch in detail.branches:
            lines.append(f"{prefix}    {branch.label}")
            for child in branch.details:
                child_location = _format_location(child.projected_location)
                lines.append(f"{prefix}      {child_location}: {child.message}")


def _prefix_detail(detail: _ErrorDetail, prefix: ErrorLocation) -> _ErrorDetail:
    return _ErrorDetail(
        detail.code,
        (*prefix, *detail.location),
        detail.expected,
        detail.received,
        detail.input,
        detail.context,
        detail.hook,
        detail.stage,
        tuple((*prefix, *location) for location in detail.related_locations),
        tuple(
            _UnionBranch(
                branch.label,
                tuple(_prefix_detail(child, prefix) for child in branch.details),
            )
            for branch in detail.branches
        ),
    )


def _format_location(location: tuple[JsonScalar, ...]) -> str:
    if not location:
        return "<root>"
    first, *remaining = location
    if isinstance(first, str):
        rendered = safe_text(first)
    elif type(first) is int:
        rendered = f"[{first}]"
    else:
        rendered = f"[{snapshot_input(first).rendered}]"
    for segment in remaining:
        if isinstance(segment, str) and segment.isidentifier():
            rendered = f"{rendered}.{safe_text(segment)}"
        elif type(segment) is int:
            rendered = f"{rendered}[{segment}]"
        else:
            rendered = f"{rendered}[{snapshot_input(segment).rendered}]"
    return rendered
