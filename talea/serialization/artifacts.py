"""Own lazy class-level publication of compiled outbound functions."""

from dataclasses import dataclass
from threading import RLock
from typing import cast

from talea.declaration.models import SpecSchema
from talea.serialization.compilation import (
    FilteredSpecSerializer,
    SpecSerializer,
    compile_serialization,
)
from talea.serialization.emission import OutputMode

_OUTPUT_COMPILATION_LOCK = RLock()


@dataclass(slots=True)
class _OutputArtifacts:
    """Own independently lazy compiled outbound functions for one Spec."""

    python_alias: SpecSerializer | None = None
    json_alias: SpecSerializer | None = None
    variants: dict[tuple[OutputMode, bool, bool], SpecSerializer | FilteredSpecSerializer] | None = None

    def output_for(
        self,
        schema: SpecSchema,
        mode: OutputMode,
        by_alias: bool,
        filtered: bool,
    ) -> SpecSerializer | FilteredSpecSerializer:
        """Return one serializer, publishing first-use compilation atomically."""

        default_alias = by_alias and not filtered
        if default_alias:
            compiled = self.python_alias if mode == "python" else self.json_alias
        else:
            compiled = None if self.variants is None else self.variants.get((mode, by_alias, filtered))
        if compiled is not None:
            return compiled
        with _OUTPUT_COMPILATION_LOCK:
            if default_alias:
                compiled = self.python_alias if mode == "python" else self.json_alias
            else:
                compiled = None if self.variants is None else self.variants.get((mode, by_alias, filtered))
            if compiled is None:
                compiled = compile_serialization(schema, mode, by_alias, filtered)
                if default_alias:
                    if mode == "python":
                        self.python_alias = cast(SpecSerializer, compiled)
                    else:
                        self.json_alias = cast(SpecSerializer, compiled)
                else:
                    if self.variants is None:
                        self.variants = {}
                    self.variants[(mode, by_alias, filtered)] = compiled
        return compiled
