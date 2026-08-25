"""Compatibility imports for Talea's dedicated error domain."""

from talea.errors.models import CustomValidationError, ErrorLocation, ValidationError

__all__ = ["CustomValidationError", "ErrorLocation", "ValidationError"]
