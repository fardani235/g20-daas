"""User-facing error types.

Anything raised as ``FootprintError`` is reported verbatim in the run panel
(one actionable line); other exceptions are bugs and are shown with their
traceback tail.
"""


class FootprintError(Exception):
    """The run cannot proceed for a reason the user can act on."""


class InputError(FootprintError):
    """The orthophoto is missing, unreadable, not georeferenced or too large for the grid."""


class ModelError(FootprintError):
    """A model card or model file is invalid, missing, corrupt or does not match its card."""


class ParameterError(FootprintError):
    """A parameter value is outside what the plugin accepts."""
