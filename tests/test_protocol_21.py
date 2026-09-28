"""Protocol 2.1 against the real binary: resize and raw RGBA."""

import asyncio
import io

import pytest
from PIL import Image

from mlnative import AsyncRenderer

RED = {
    "version": 8,
    "sources": {},
    "layers": [{"id": "bg", "type": "background", "paint": {"background-color": "#ff0000"}}],
}

pytestmark = pytest.mark.integration


def test_one_process_draws_every_size():
    async def main():
        async with AsyncRenderer(256, 256, RED) as renderer:
            pid = renderer.pid
            sizes = []
            for size in [(400, 300), (64, 64), (1024, 768)]:
                png = await renderer.render([12.5, 41.9], 10, size=size)
                sizes.append(Image.open(io.BytesIO(png)).size)
            return pid, renderer.pid, sizes

    before, after, sizes = asyncio.run(main())
    assert before == after
    assert sizes == [(400, 300), (64, 64), (1024, 768)]


def test_rgba_has_the_same_pixels_as_the_png():
    async def main():
        async with AsyncRenderer(128, 96, RED) as renderer:
            png = await renderer.render([12.5, 41.9], 10)
            raw = await renderer.render([12.5, 41.9], 10, output="rgba")
            return png, raw

    png, raw = asyncio.run(main())
    decoded = Image.open(io.BytesIO(png)).convert("RGBA")
    assert (raw.width, raw.height) == decoded.size == (128, 96)
    assert raw.data == decoded.tobytes()


def test_rgba_counts_the_pixel_ratio():
    async def main():
        async with AsyncRenderer(50, 40, RED, pixel_ratio=2) as renderer:
            return await renderer.render([0, 0], 1, output="rgba")

    raw = asyncio.run(main())
    assert (raw.width, raw.height, len(raw.data)) == (100, 80, 100 * 80 * 4)
