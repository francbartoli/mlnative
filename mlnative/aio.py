"""An asyncio interface to one renderer process.

``AsyncRenderer`` owns one renderer process and sends it one command at a
time over asyncio pipes: a render in progress keeps no thread waiting and
leaves the event loop free. The process can be resized between renders and
can answer raw RGBA pixels instead of a PNG.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, overload

from ._bridge import PROTOCOL_VERSION, STDERR_BUFFER_LINES, _get_timeout, get_binary_path
from .exceptions import MlnativeError
from .map import (
    MAX_ZOOM,
    Bounds,
    Center,
    _normalize_view,
    _serialize_style,
    _validate_dimension,
    _validate_pixel_ratio,
    fit_bounds,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawImage:
    """Pixels as the renderer drew them: RGBA, row by row, four bytes a pixel."""

    width: int
    height: int
    data: bytes


class AsyncRenderer:
    """One renderer process, driven from asyncio.

    Example::

        async with AsyncRenderer(512, 512, style) as renderer:
            png = await renderer.render([12.49, 41.89], 11)
            raw = await renderer.render([12.49, 41.89], 11, size=(800, 600), output="rgba")

    An error the process answers raises :class:`MlnativeError` and keeps the
    process. A process that exits, does not answer within the timeout or is
    cancelled in the middle of a command is stopped: build a new renderer.
    """

    def __init__(
        self,
        width: int,
        height: int,
        style: str | dict[str, Any] | None = None,
        *,
        pixel_ratio: float = 1.0,
        timeout: float | None = None,
        command: Sequence[str] | None = None,
    ) -> None:
        """Validate the options; the process starts with :meth:`start`.

        ``command`` replaces the packaged binary, for tests.
        """
        _validate_dimension(width, height)
        _validate_pixel_ratio(pixel_ratio)
        self._size = (width, height)
        self._style = _serialize_style(style)
        self._pixel_ratio = pixel_ratio
        self._timeout = _get_timeout(timeout)
        self._command = list(command) if command is not None else None
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_BUFFER_LINES)
        self._stderr_task: asyncio.Task[None] | None = None
        self._reaping: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    @property
    def pid(self) -> int | None:
        """The process id while the process runs, for monitoring."""
        process = self._process
        if process is None or process.returncode is not None:
            return None
        return process.pid

    @property
    def size(self) -> tuple[int, int]:
        """The size the process renders at, in logical pixels."""
        return self._size

    async def start(self) -> None:
        """Start the process and load the style."""
        if self._process is not None:
            raise MlnativeError("Renderer already started")
        command = self._command or [str(get_binary_path())]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise MlnativeError(f"Failed to start renderer: {error}") from error
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._process))
        width, height = self._size
        try:
            await self._call(
                {
                    "cmd": "init",
                    "width": width,
                    "height": height,
                    "style": self._style,
                    "pixel_ratio": self._pixel_ratio,
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
        except MlnativeError:
            await self.aclose()
            raise

    @overload
    async def render(
        self,
        center: Center,
        zoom: float,
        *,
        bearing: float = 0,
        pitch: float = 0,
        size: tuple[int, int] | None = None,
        output: Literal["png"] = "png",
    ) -> bytes: ...

    @overload
    async def render(
        self,
        center: Center,
        zoom: float,
        *,
        bearing: float = 0,
        pitch: float = 0,
        size: tuple[int, int] | None = None,
        output: Literal["rgba"],
    ) -> RawImage: ...

    async def render(
        self,
        center: Center,
        zoom: float,
        *,
        bearing: float = 0,
        pitch: float = 0,
        size: tuple[int, int] | None = None,
        output: Literal["png", "rgba"] = "png",
    ) -> bytes | RawImage:
        """Render one view: PNG bytes, or a :class:`RawImage` with ``output="rgba"``.

        ``size`` resizes the process first, and later renders keep it.
        """
        if output not in ("png", "rgba"):
            raise MlnativeError(f"output must be 'png' or 'rgba', got {output!r}")
        command: dict[str, Any] = {
            "cmd": "render",
            **_normalize_view(center, zoom, bearing, pitch, "Center"),
            "output": output,
        }
        if size is not None:
            _validate_dimension(size[0], size[1])
            command["width"], command["height"] = size[0], size[1]
        response = await self._call(command)
        if size is not None:
            self._size = (size[0], size[1])
        image = response.get("rgba" if output == "rgba" else "png")
        if not isinstance(image, bytes) or not image:
            raise MlnativeError("Render returned no image data")
        if output == "rgba":
            return RawImage(int(response["width"]), int(response["height"]), image)
        return image

    async def reload_style(self, style: str | dict[str, Any]) -> None:
        """Replace the style without restarting the process."""
        text = _serialize_style(style)
        await self._call({"cmd": "reload_style", "style": text})
        self._style = text

    def fit_bounds(
        self, bounds: Bounds, *, padding: int = 0, max_zoom: float = MAX_ZOOM
    ) -> tuple[list[float], float]:
        """The centre and zoom that fit ``bounds`` at the current size; see :func:`fit_bounds`."""
        return fit_bounds(bounds, self._size[0], self._size[1], padding=padding, max_zoom=max_zoom)

    async def aclose(self) -> None:
        """Ask the process to quit, and kill it if it has not within five seconds."""
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with suppress(Exception):
                if process.stdin is not None:
                    process.stdin.write(b'{"cmd":"quit"}\n')
                    await process.stdin.drain()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
        if process is not None:
            await self._reap(process)
        if self._reaping:
            await asyncio.gather(*self._reaping, return_exceptions=True)
        task, self._stderr_task = self._stderr_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def __aenter__(self) -> AsyncRenderer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _call(self, command: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            process = self._process
            if process is None or process.returncode is not None:
                raise MlnativeError("Renderer is not running")
            try:
                response = await asyncio.wait_for(self._exchange(process, command), self._timeout)
            except TimeoutError:
                self._log_failure(f"no answer to {command['cmd']} within {self._timeout}s")
                await self._stop_now()
                raise MlnativeError(
                    f"Timeout waiting for renderer response after {self._timeout}s; "
                    "the renderer was stopped"
                ) from None
            except asyncio.CancelledError:
                # The answer may still arrive and would be read as the next
                # command's: the process cannot be trusted any more.
                self._kill()
                raise
            except (OSError, EOFError, ValueError) as error:
                self._log_failure(f"{command['cmd']} broke the renderer: {error}")
                await self._stop_now()
                raise MlnativeError("Renderer process closed unexpectedly") from error
        if response.get("status") != "ok":
            reason = response.get("error", "unknown error")
            raise MlnativeError(f"{command['cmd'].capitalize()} failed: {reason}")
        return response

    async def _exchange(
        self, process: asyncio.subprocess.Process, command: dict[str, Any]
    ) -> dict[str, Any]:
        if process.stdin is None or process.stdout is None:
            raise MlnativeError("Renderer pipes are not open")
        process.stdin.write(json.dumps(command).encode("utf-8") + b"\n")
        await process.stdin.drain()
        header = await process.stdout.readline()
        if not header:
            raise EOFError("the renderer closed its output")
        response: dict[str, Any] = json.loads(header)
        if "png_len" in response:
            response["png"] = await process.stdout.readexactly(int(response["png_len"]))
        elif "rgba_len" in response:
            response["rgba"] = await process.stdout.readexactly(int(response["rgba_len"]))
        return response

    def _kill(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        # Reaped on the loop: pipes left to the collector fail once the loop is closed.
        task = asyncio.ensure_future(self._reap(process))
        self._reaping.add(task)
        task.add_done_callback(self._reaping.discard)

    async def _stop_now(self) -> None:
        self._kill()
        if self._reaping:
            await asyncio.gather(*self._reaping, return_exceptions=True)

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process) -> None:
        """Wait for a stopped process, then close its pipes."""
        with suppress(Exception):
            await asyncio.wait_for(process.wait(), 5)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            with suppress(Exception):
                await asyncio.wait_for(process.stdout.read(), 5)

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        # A line longer than the stream limit ends the drain, not the renderer.
        with suppress(ValueError):
            async for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._stderr.append(line)
                logger.debug("renderer.stderr: %s", line)

    def _log_failure(self, message: str) -> None:
        logger.error("%s | recent_stderr=%s", message, list(self._stderr))
