"""fit_bounds: Web Mercator centre and MapLibre's 512-pixel world."""

import io
import math

import pytest
from PIL import Image

from mlnative import Map, bounds_to_polygon, fit_bounds

TALL = (10.0, 40.0, 30.0, 60.0)


def _mercator_lat(y: float) -> float:
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)


def _y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def test_the_centre_is_the_mercator_midpoint():
    (lon, lat), _ = fit_bounds(TALL, 400, 300)

    assert lon == pytest.approx(20.0)
    assert lat == pytest.approx(_mercator_lat((_y(40) + _y(60)) / 2))
    assert lat > 51  # the mean of the latitudes would be 50


def test_the_world_fits_a_512_pixel_map_at_zoom_zero():
    _, zoom = fit_bounds((-180, -85.0511287798, 180, 85.0511287798), 512, 512)

    assert zoom == pytest.approx(0.0, abs=1e-6)


def test_the_map_method_gives_the_same_camera():
    with Map(400, 300, pixel_ratio=2) as m:
        assert m.fit_bounds(TALL, padding=40) == fit_bounds(TALL, 400, 300, padding=40)


@pytest.mark.integration
@pytest.mark.parametrize("pixel_ratio", [1, 2])
def test_the_bounds_touch_the_padding_on_the_limiting_side(pixel_ratio):
    style = {
        "version": 8,
        "sources": {"box": {"type": "geojson", "data": bounds_to_polygon(TALL)}},
        "layers": [
            {"id": "bg", "type": "background", "paint": {"background-color": "#ffffff"}},
            {"id": "box", "type": "fill", "source": "box", "paint": {"fill-color": "#ff0000"}},
        ],
    }
    with Map(400, 300, pixel_ratio=pixel_ratio) as m:
        m.load_style(style)
        center, zoom = m.fit_bounds(TALL, padding=40)
        image = Image.open(io.BytesIO(m.render(center, zoom))).convert("RGB")

    r = pixel_ratio
    red, white = (255, 0, 0), (255, 255, 255)
    # The height limits: the box spans rows 40 to 260 of the 300-pixel map.
    assert image.getpixel((200 * r, 150 * r)) == red
    assert image.getpixel((200 * r, 44 * r)) == red
    assert image.getpixel((200 * r, 36 * r)) == white
    assert image.getpixel((200 * r, 256 * r)) == red
    assert image.getpixel((200 * r, 264 * r)) == white
