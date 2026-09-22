"""User-facing error types.

Anything raised as ``SegmentationError`` is reported verbatim in the run panel;
other exceptions are treated as bugs and shown with their traceback tail.
"""


class SegmentationError(Exception):
    """The run cannot proceed for a reason the user can act on."""


class InputError(SegmentationError):
    """Selected inputs are missing, unreadable, incompatible or do not overlap."""


class ModelError(SegmentationError):
    """A model card or model file is invalid, or the model cannot run here."""


class ParameterError(SegmentationError):
    """A parameter value is outside what the selected model accepts."""
