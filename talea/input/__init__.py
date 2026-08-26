"""Compile external Mapping and decoded-JSON input boundaries for Specs."""

from talea.input.compilation import InputCallable, compile_input
from talea.input.json import JsonLoads, decode_json

__all__ = ["InputCallable", "JsonLoads", "compile_input", "decode_json"]
