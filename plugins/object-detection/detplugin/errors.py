"""User-facing error types.

Anything raised as ``DetectionError`` is reported verbatim in the run panel;
other exceptions are treated as bugs and shown with their traceback tail.
"""


class DetectionError(Exception):
    """The run cannot proceed for a reason the user can act on."""


class InputError(DetectionError):
    """The orthophoto is missing, unreadable or not georeferenced."""


class ModelError(DetectionError):
    """A model card or model file is invalid, missing, or does not match its card."""


class ParameterError(DetectionError):
    """A parameter value is outside what the selected model accepts."""
