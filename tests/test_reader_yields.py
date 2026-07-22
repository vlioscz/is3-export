"""The reader yields to the loop after every line.

readuntil() returns without suspending when the next line is already buffered,
so without an explicit yield the reader would drain a whole burst in one loop
step and starve the callbacks each line schedules (an automation firing, the UI
push) -- the button-press lag.  This pins the yield: a callback scheduled before
the burst must run *between* line dispatches, not after all of them.
"""

from __future__ import annotations

import asyncio

from custom_components.is3_export.api import Is3Client


class _BufferedReader:
    """A reader whose lines are already in memory, so readuntil never suspends."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readuntil(self, _sep: bytes) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        raise asyncio.IncompleteReadError(b"", None)


def test_reader_yields_between_buffered_lines() -> None:
    order: list[str] = []

    def on_event(address: int, _value: int) -> None:
        order.append(f"line:{address:#x}")

    async def run() -> None:
        client = Is3Client("h", 1, on_event=on_event)
        client._closing = True  # skip the reconnect after the stream ends
        client._reader = _BufferedReader(
            [
                b"EVENT 0x01010001 0x1\r\n",
                b"EVENT 0x01010002 0x1\r\n",
                b"EVENT 0x01010003 0x1\r\n",
            ]
        )
        # Scheduled now; it can only run when the reader hands the loop a turn.
        asyncio.get_running_loop().call_soon(lambda: order.append("marker"))
        await client._async_read_loop()

    asyncio.run(run())

    assert order.count("marker") == 1
    assert order == [
        "line:0x1010001",
        "marker",
        "line:0x1010002",
        "line:0x1010003",
    ], f"the marker must run between lines, not after all of them: {order}"
