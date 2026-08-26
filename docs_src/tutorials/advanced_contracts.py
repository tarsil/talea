"""Generic, recursive, tagged, metadata, schema, and policy composition."""

from typing import Annotated, Literal, cast

from talea import (
    Alias,
    Contract,
    Deprecated,
    Description,
    Discriminator,
    Examples,
    ReadOnly,
    ResourcePolicy,
    Sensitive,
    Spec,
    Title,
    WriteOnly,
    apply_patch,
    derive_spec,
)


class Envelope[T](Spec):
    payload: T


type Tree[T] = T | list[Tree[T]]


class ValueNode(Spec):
    kind: Literal["value"]
    value: int


class GroupNode(Spec):
    kind: Literal["group"]
    children: list[Node]


type Node = Annotated[ValueNode | GroupNode, Discriminator("kind")]


class Document(Spec, metadata=(Title("Document"), Description("A recursive document."))):
    document_id: Annotated[int, Alias("documentId"), ReadOnly(), Examples(7)]
    tree: Tree[int]
    root: Node
    credential: Annotated[str, Sensitive(), WriteOnly()]
    legacy_label: Annotated[str | None, Deprecated()] = None


document = Document.from_mapping(
    {
        "documentId": 7,
        "tree": [1, [2]],
        "root": {"kind": "group", "children": [{"kind": "value", "value": 3}]},
        "credential": "token",
    },
    policy=ResourcePolicy(max_depth=16, max_nodes=1_000),
)
assert isinstance(document.root, GroupNode)
assert Envelope[Document](payload=document).payload is document
assert Contract[Tree[int]](Tree[int]).validate([1, [2]]) == [1, [2]]

DocumentPatch = derive_spec(Document, partial=True)
patched = apply_patch(document, DocumentPatch.from_json('{"tree":[9]}'))
assert patched.tree == [9]

document_schema = cast(dict[str, object], Document.openapi_schema()["schema"])
assert document_schema["title"] == "Document"
