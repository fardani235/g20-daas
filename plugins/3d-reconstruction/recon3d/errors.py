"""User-facing error types.

Anything raised as ``ReconstructionError`` is reported verbatim in the run
panel; other exceptions are bugs and are shown with their traceback tail.
"""


class ReconstructionError(Exception):
    """The run cannot proceed for a reason the user can act on."""


class InputError(ReconstructionError):
    """Selected inputs are missing, unreadable, incompatible or do not overlap."""


class ParameterError(ReconstructionError):
    """A parameter value is invalid for the selected workflow."""
