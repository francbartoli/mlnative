"""AsyncRenderer against a fake renderer process: framing, sizes and failures."""

import asyncio
import struct
import sys
from pathlib import Path

import pytest

from mlnative import AsyncRenderer, MlnativeError, RawImage

FAKE = [sys.executable, str(Path(__file__).with_name("fake_daemon.py"))]
STYLE = {"version": 8, "sources": {}, "layers": []}


def png_size(png: bytes) -> tuple[int, int]:
    return struct.unpack(">II", png[4:12])


def test_a_render_is_a_png_at_the_process_size():
    async def main():
        async with AsyncRenderer(256, 128, STYLE, command=FAKE) as renderer:
            return await renderer.render([12.5, 41.9], 10)

    assert png_size(asyncio.run(main())) == (256, 128)


def test_a_size_resizes_the_process_for_this_and_later_renders():
    async def main():
        async with AsyncRenderer(256, 256, STYLE, command=FAKE) as renderer:
            first = await renderer.render([0, 0], 1, size=(400, 300))
            second = await renderer.render([0, 0], 1)
            return first, second, renderer.size

    first, second, size = asyncio.run(main())
    assert png_size(first) == png_size(second) == (400, 300)
    assert size == (400, 300)


def test_rgba_is_the_pixels_with_their_size():
    async def main():
        async with AsyncRenderer(10, 5, STYLE, pixel_ratio=2, command=FAKE) as renderer:
            return await renderer.render([0, 0], 1, output="rgba")

    raw = asyncio.run(main())
    assert isinstance(raw, RawImage)
    assert (raw.width, raw.height, len(raw.data)) == (20, 10, 20 * 10 * 4)


def test_an_error_answer_raises_and_keeps_the_process(monkeypatch):
    monkeypatch.setenv("FAKE_DAEMON", "error-on-render")

    async def main():
        async with AsyncRenderer(64, 64, STYLE, command=FAKE) as renderer:
            with pytest.raises(MlnativeError, match="boom"):
                await renderer.render([0, 0], 1)
            return renderer.pid

    assert asyncio.run(main()) is not None


def test_a_process_that_exits_raises_and_is_gone(monkeypatch):
    monkeypatch.setenv("FAKE_DAEMON", "exit-on-render")

    async def main():
        async with AsyncRenderer(64, 64, STYLE, command=FAKE) as renderer:
            with pytest.raises(MlnativeError, match="closed unexpectedly"):
                await renderer.render([0, 0], 1)
            return renderer.pid

    assert asyncio.run(main()) is None


def test_a_render_that_never_answers_times_out_and_stops_the_process(monkeypatch):
    monkeypatch.setenv("FAKE_DAEMON", "hang-on-render")

    async def main():
        async with AsyncRenderer(64, 64, STYLE, timeout=0.5, command=FAKE) as renderer:
            with pytest.raises(MlnativeError, match="Timeout"):
                await renderer.render([0, 0], 1)
            return renderer.pid

    assert asyncio.run(main()) is None


def test_a_cancelled_render_kills_the_process(monkeypatch):
    monkeypatch.setenv("FAKE_DAEMON", "hang-on-render")

    async def main():
        renderer = AsyncRenderer(64, 64, STYLE, command=FAKE)
        await renderer.start()
        task = asyncio.create_task(renderer.render([0, 0], 1))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pid = renderer.pid
        with pytest.raises(MlnativeError, match="not running"):
            await renderer.render([0, 0], 1)
        await renderer.aclose()
        return pid

    assert asyncio.run(main()) is None


def test_aclose_stops_the_process():
    async def main():
        renderer = AsyncRenderer(64, 64, STYLE, command=FAKE)
        await renderer.start()
        running = renderer.pid
        await renderer.aclose()
        return running, renderer.pid

    running, after = asyncio.run(main())
    assert running is not None and after is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"size": (0, 10)}, "positive"),
        ({"size": (5000, 10)}, "4096"),
        ({"output": "jpeg"}, "output"),
    ],
)
def test_a_bad_size_or_output_is_refused_before_the_process_sees_it(kwargs, message):
    async def main():
        async with AsyncRenderer(64, 64, STYLE, command=FAKE) as renderer:
            with pytest.raises(MlnativeError, match=message):
                await renderer.render([0, 0], 1, **kwargs)
            return renderer.pid

    assert asyncio.run(main()) is not None


@pytest.mark.parametrize("stop", ["timeout", "exit", "cancel"])
def test_a_stopped_process_is_reaped_and_its_pipes_closed(monkeypatch, stop):
    """Closed while the loop runs: left to the collector, they fail on a closed loop."""
    monkeypatch.setenv("FAKE_DAEMON", "exit-on-render" if stop == "exit" else "hang-on-render")

    async def main():
        renderer = AsyncRenderer(64, 64, STYLE, timeout=0.3, command=FAKE)
        await renderer.start()
        process = renderer._process
        try:
            if stop == "cancel":
                task = asyncio.create_task(renderer.render([0, 0], 1))
                await asyncio.sleep(0.1)
                task.cancel()
                await task
            else:
                await renderer.render([0, 0], 1)
        except (asyncio.CancelledError, MlnativeError):
            pass
        await renderer.aclose()
        return process.returncode, process.stdin.is_closing(), process.stdout.at_eof()

    returncode, stdin_closed, stdout_done = asyncio.run(main())
    assert returncode is not None
    assert stdin_closed and stdout_done
