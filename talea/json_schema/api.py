"""Public JSON Schema and OpenAPI projection entry points."""

from typing import Literal

from talea.metadata import DeclarationMetadata
from talea.schema.nodes import Schema

from .projection import project_schema

type SchemaMode = Literal["input", "output"]


def json_schema(
    schema: Schema,
    metadata: DeclarationMetadata,
    *,
    mode: SchemaMode = "input",
) -> dict[str, object]:
    """Return a fresh Draft 2020-12 schema for canonical Talea truth.

    External aliases name object properties, recursive named declarations use
    deterministic ``$defs`` references, and requiredness comes from canonical
    Spec or TypedDict fields. ``mode='input'`` describes Talea's JSON input
    boundary; ``mode='output'`` describes JSON serialization. An arbitrary
    transform or serializer raises ``SchemaProjectionError`` in the mode whose
    external domain the callback makes unknowable.
    """

    return project_schema(schema, metadata, mode=mode, target="json_schema")


def openapi_schema(
    schema: Schema,
    metadata: DeclarationMetadata,
    *,
    mode: SchemaMode = "input",
) -> dict[str, object]:
    """Return an OpenAPI 3.1-compatible schema and components fragment.

    The returned mapping has ``schema`` and ``components`` keys so framework
    adapters can insert the values into an OpenAPI document without inspecting
    projector state. It uses the OpenAPI 3.1 Schema Object dialect, Draft
    2020-12 semantics, component references, and canonical tagged-union
    discriminator mappings. Route and operation generation are outside scope.
    """

    return project_schema(schema, metadata, mode=mode, target="openapi")
