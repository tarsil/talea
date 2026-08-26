import pytest

from talea import Spec
from talea.schema import AnnotationResolutionError
from talea.spec.generics import validate_annotation_expression


@pytest.mark.parametrize(
    "expression",
    [
        "factory()",
        "target.__class__",
        "__builtins__",
        "[item for item in values]",
        "lambda: Target",
        "f'{Target}'",
        "Target if condition else Other",
    ],
)
def test_executable_or_dunder_annotation_syntax_is_rejected(expression: str) -> None:
    with pytest.raises(AnnotationResolutionError):
        validate_annotation_expression(expression)


def test_dunder_attribute_is_rejected_before_namespace_object_access() -> None:
    accesses: list[str] = []

    class Probe:
        def __getattribute__(self, name: str) -> object:
            accesses.append(name)
            return object.__getattribute__(self, name)

    probe = Probe()
    with pytest.raises(AnnotationResolutionError):

        class Payload(Spec):
            value: "probe.__class__"

    assert accesses == []
