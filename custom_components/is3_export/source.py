"""Where the export file comes from.

The central unit serves its own export over plain HTTP, so the file does not
have to be copied by hand.  A path on disk stays supported for units whose web
server is unreachable, and for working from a saved export.

The unit can be password protected.  Its web server is lighttpd, whose mod_auth
offers both Basic and Digest, so both are handled: credentials are sent as Basic
up front, and a Digest challenge is answered by retrying.  The ASCII port itself
takes no credentials -- probing a live unit showed it accepts commands from
anyone who can open the socket.

Note that the export is a deliberate subset of the installation: in the iNELS
IDM3 configuration software the installer picks which devices are published to
the iNELS3 export.  Addresses outside it are not missing, they are unpublished,
which is why the unit pushes events for addresses that never appear here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import BasicAuth, ClientError, ClientSession, DigestAuthMiddleware

from .const import EXPORT_URL_PATH
from .export import Is3Export, parse_export

_LOGGER = logging.getLogger(__name__)

# Exports are UTF-8 with a BOM; utf-8-sig strips it.
_ENCODING = "utf-8-sig"

FETCH_TIMEOUT = 15
HTTP_UNAUTHORIZED = 401


class Is3ExportError(Exception):
    """The export could not be read or made no sense."""


class Is3ExportAuthError(Is3ExportError):
    """The unit demanded credentials, or rejected the ones given."""


class Is3ExportFormatError(Is3ExportError):
    """The file parsed, but it is not an iNELS3 (.is3) export.

    An ``.imm`` file -- the older format -- has entry lines this parser happily
    reads, but no ``VERSION_..._IDM3_...`` header and no controller channels. It
    once went in without a word and pruned every entity the controllers back
    (thermostats, their buttons) because the new export simply did not contain
    them. The header is the one unambiguous mark of a ``.is3``, so a file
    without it is refused rather than classified.
    """


def export_url(host: str, http_port: int) -> str:
    """The URL the unit serves its export from."""
    return f"http://{host}:{http_port}{EXPORT_URL_PATH}"


def read_export_file(path: Path) -> Is3Export:
    """Read and parse an export file from disk."""
    try:
        payload = path.read_text(encoding=_ENCODING)
    except OSError as err:
        raise Is3ExportError(f"Cannot read export file {path}: {err}") from err
    return _parse(payload, str(path))


async def async_fetch_export(
    session: ClientSession,
    host: str,
    http_port: int,
    username: str | None = None,
    password: str | None = None,
) -> Is3Export:
    """Download and parse the export straight from the unit."""
    url = export_url(host, http_port)
    auth = BasicAuth(username, password) if username else None

    try:
        response = await session.get(url, auth=auth, timeout=FETCH_TIMEOUT)
        if response.status == HTTP_UNAUTHORIZED:
            raw = await _async_fetch_with_digest(url, response, username, password)
        else:
            response.raise_for_status()
            raw = await response.read()
    except (ClientError, TimeoutError) as err:
        raise Is3ExportError(f"Cannot fetch {url}: {err}") from err

    # The unit does not declare a charset, so decode explicitly rather than
    # letting aiohttp guess.
    return _parse(raw.decode(_ENCODING, errors="replace"), url)


async def _async_fetch_with_digest(
    url: str, challenge, username: str | None, password: str | None
) -> bytes:
    """Answer a 401 by retrying with Digest, if that is what was asked for."""
    scheme = challenge.headers.get("WWW-Authenticate", "")

    if not username or not password:
        raise Is3ExportAuthError(
            f"{url} requires a password. Enter the credentials set for the unit, "
            f"or configure a local export file instead"
        )

    if not scheme.lower().startswith("digest"):
        # Basic credentials were already sent and came back rejected.
        raise Is3ExportAuthError(f"{url} rejected the supplied credentials")

    # Digest needs a session of its own: the middleware is set per session, and
    # Home Assistant's shared session must not be reconfigured.
    digest = DigestAuthMiddleware(login=username, password=password)
    async with ClientSession(middlewares=(digest,)) as session:
        response = await session.get(url, timeout=FETCH_TIMEOUT)
        if response.status == HTTP_UNAUTHORIZED:
            raise Is3ExportAuthError(f"{url} rejected the supplied credentials")
        response.raise_for_status()
        return await response.read()


def parse_export_text(payload: str, origin: str) -> Is3Export:
    """Parse export text that arrived by hand -- an upload into the form, say."""
    return _parse(payload, origin)


def _parse(payload: str, origin: str) -> Is3Export:
    """Parse an export, rejecting one that is not a ``.is3`` or yielded nothing.

    The header check comes first and matters most: an ``.imm`` file parses into
    entities and would quietly replace a real installation with a controller-less
    subset of itself, deleting the thermostats and their buttons on the way. A
    ``.is3`` always opens with ``VERSION_..._IDM3_...``; nothing else does.
    """
    export = parse_export(payload)
    if export.header is None or not export.header.idm3:
        raise Is3ExportFormatError(
            f"{origin} is not an iNELS3 (.is3) export -- it has no "
            f"VERSION_..._IDM3_... header. An .imm file (the older format) has no "
            f"controllers, so uploading it would drop your thermostats and their "
            f"buttons. Save the project as .is3 from IDM3 and upload that."
        )
    if not export.entries:
        raise Is3ExportError(f"No entries found in export from {origin}")
    return export
