"""Own one Contract instance's independently lazy compiled capabilities."""

from dataclasses import dataclass, field
from threading import RLock

from talea.input.emission import InputMode
from talea.input.value import ValueInput, compile_value_input
from talea.schema.nodes import Schema
from talea.serialization.emission import OutputMode, ValueProjector, compile_value_projector


@dataclass(slots=True)
class _ContractArtifacts:
    """Retain canonical schema execution artifacts without global caching."""

    schema: Schema
    title: str
    python_input: ValueInput | None = None
    json_input: ValueInput | None = None
    python_output: ValueProjector | None = None
    json_output: ValueProjector | None = None
    lock: RLock = field(default_factory=RLock)

    def input_for(self, mode: InputMode) -> ValueInput:
        """Return one root input artifact, compiling it once when first used."""

        compiled = self.python_input if mode == "mapping" else self.json_input
        if compiled is not None:
            return compiled
        with self.lock:
            compiled = self.python_input if mode == "mapping" else self.json_input
            if compiled is None:
                compiled = compile_value_input(self.schema, mode, self.title)
                if mode == "mapping":
                    self.python_input = compiled
                else:
                    self.json_input = compiled
        return compiled

    def output_for(self, mode: OutputMode) -> ValueProjector:
        """Return one root projector, compiling it once when first used."""

        compiled = self.python_output if mode == "python" else self.json_output
        if compiled is not None:
            return compiled
        with self.lock:
            compiled = self.python_output if mode == "python" else self.json_output
            if compiled is None:
                compiled = compile_value_projector(self.schema, mode, True)
                if mode == "python":
                    self.python_output = compiled
                else:
                    self.json_output = compiled
        return compiled
