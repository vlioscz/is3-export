"""Tests for loading the export, including from a password protected unit.

The unit runs lighttpd, whose mod_auth offers Basic and Digest, so both are
exercised against a server that mimics each challenge.  Tests drive asyncio
directly rather than through a plugin, so no async pytest plugin is required.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable
from typing import Any, TypeVar

import aiohttp
import pytest
from aiohttp import web

from custom_components.is3_export.source import (
    Is3ExportAuthError,
    Is3ExportError,
    async_fetch_export,
    export_url,
    read_export_file,
)

T = TypeVar("T")

EXPORT_BODY = (
    "VERSION_01-03-03_CREATE_2026-07-20-22-18-42_IDM3_03-04-19_ID_ABC_NAME_Test\r\n"
    "Sv_loznice SA3-012M_RE10_0D0001 0x0102000A 0x00000000\r\n"
    "Sv_obyv DA3-22M_OUT1_0D0002 0x01040001 0x00000032 %\r\n"
).encode("utf-8-sig")

USER = "admin"
PASSWORD = "tajne"


def run(coro: Awaitable[T]) -> T:
    """Run one coroutine to completion."""
    return asyncio.run(coro)


def _handler(mode: str) -> Any:
    """A request handler demanding no auth, Basic, or Digest."""

    async def handle(request: web.Request) -> web.Response:
        """Serve the export, challenging first when the mode says so."""
        header = request.headers.get("Authorization", "")

        if mode == "none":
            return web.Response(body=EXPORT_BODY)

        if mode == "basic":
            expected = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
            if header == f"Basic {expected}":
                return web.Response(body=EXPORT_BODY)
            return web.Response(
                status=401, headers={"WWW-Authenticate": 'Basic realm="iNELS"'}
            )

        if header.lower().startswith("digest") and f'username="{USER}"' in header:
            return web.Response(body=EXPORT_BODY)
        return web.Response(
            status=401,
            headers={
                "WWW-Authenticate": 'Digest realm="iNELS", nonce="abc", qop="auth"'
            },
        )

    return handle


async def _fetch(
    mode: str, username: str | None, password: str | None
) -> Any:
    """Serve the export in the given mode and fetch it back."""
    app = web.Application()
    app.router.add_get("/immfiles/export.is3", _handler(mode))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        async with aiohttp.ClientSession() as session:
            return await async_fetch_export(
                session, "127.0.0.1", port, username, password
            )
    finally:
        await runner.cleanup()


def test_export_url() -> None:
    """The path the unit serves its export from."""
    assert export_url("192.168.1.10", 80) == "http://192.168.1.10:80/immfiles/export.is3"


def test_fetch_without_auth() -> None:
    """An unprotected unit needs no credentials."""
    export = run(_fetch("none", None, None))
    assert len(export.entries) == 2
    assert export.header.name == "Test"


def test_credentials_are_harmless_when_not_required() -> None:
    """Supplying a password to an open unit must not break the fetch."""
    export = run(_fetch("none", USER, PASSWORD))
    assert len(export.entries) == 2


@pytest.mark.parametrize("mode", ["basic", "digest"])
def test_missing_credentials_are_reported_as_auth(mode: str) -> None:
    """A challenge with no credentials must say a password is needed.

    It must not surface as a generic read failure, or the user has no idea the
    unit is protected.
    """
    with pytest.raises(Is3ExportAuthError, match="requires a password"):
        run(_fetch(mode, None, None))


@pytest.mark.parametrize("mode", ["basic", "digest"])
def test_correct_credentials_succeed(mode: str) -> None:
    """Both schemes lighttpd offers must work."""
    export = run(_fetch(mode, USER, PASSWORD))
    assert len(export.entries) == 2


def test_wrong_password_is_reported_as_auth() -> None:
    """Rejected credentials are an auth problem, not a parse problem."""
    with pytest.raises(Is3ExportAuthError, match="rejected"):
        run(_fetch("basic", USER, "wrong"))


def test_missing_file_is_reported(tmp_path) -> None:
    """A path that does not exist gives a clear error."""
    with pytest.raises(Is3ExportError, match="Cannot read export file"):
        read_export_file(tmp_path / "nope.is3")


def test_empty_export_is_rejected(tmp_path) -> None:
    """A file with no entries must not be accepted as a valid export."""
    path = tmp_path / "empty.is3"
    path.write_text("VERSION_01-03-03_ID_ABC_NAME_Test\r\n", encoding="utf-8-sig")
    with pytest.raises(Is3ExportError, match="No entries"):
        read_export_file(path)
