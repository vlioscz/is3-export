"""Errors raised when talking to a central unit.

They live in their own module so that everything from a light entity to the
config flow catches the same three names, whatever the transport underneath is
doing.  The split that matters is the last one: Home Assistant retries an
unreachable unit quietly, but a refused password has to open a dialog, and
raising the same error for both means a password prompt on every network blip.
"""

from __future__ import annotations


class Is3Error(Exception):
    """Any failure talking to the unit."""


class Is3ConnectionError(Is3Error):
    """The unit could not be reached, or stopped answering."""


class Is3AuthError(Is3ConnectionError):
    """The unit refused the password, or left its data plane shut.

    The unit hands back a token even when it will not honour it, so this also
    covers the case where authorization looked fine but no value can be read.
    """
