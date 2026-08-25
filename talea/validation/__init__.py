"""Compile canonical schemas into specialized strict validators."""

from talea.validation.compilation import Validator, compile_validator
from talea.validation.errors import ValidationError

__all__ = ["ValidationError", "Validator", "compile_validator"]
