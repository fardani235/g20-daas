"""User-facing error types.

Anything raised as ``ChangeError`` is reported verbatim in the run panel (one
actionable line); other exceptions are bugs and are shown with their traceback
tail.
"""


class ChangeError(Exception):
    """The run cannot proceed for a reason the user can act on."""


class InputError(ChangeError):
    """An input is missing, unreadable, not georeferenced, or the surveys do not overlap."""


class ParameterError(ChangeError):
    """A parameter value is outside what the plugin accepts."""
