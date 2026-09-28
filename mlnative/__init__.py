"""
mlnative - Simple Python wrapper for MapLibre GL Native

A grug-brained library for rendering static map images.
"""

from importlib.metadata import version

from ._bridge import get_binary_path
from .aio import AsyncRenderer, RawImage
from .exceptions import MlnativeError
from .geo import (
    bounds_to_polygon,
    feature_collection,
    from_coordinates,
    from_latlng,
    point,
)
from .map import Bounds, Center, Map, RenderView, fit_bounds

__version__ = version("mlnative")
__all__ = [
    "AsyncRenderer",
    "Bounds",
    "Center",
    "Map",
    "MlnativeError",
    "RawImage",
    "RenderView",
    "bounds_to_polygon",
    "feature_collection",
    "fit_bounds",
    "from_coordinates",
    "from_latlng",
    "get_binary_path",
    "point",
]
