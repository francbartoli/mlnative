"""The platform tag of the wheels matches where the binary is built."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "build_cibw_wheel.py"


def _script():
    spec = importlib.util.spec_from_file_location("build_cibw_wheel", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_wheels_claim_the_glibc_of_ubuntu_24_04():
    tags = _script().PLATFORM_TAGS

    assert tags["x86_64"] == ("linux-x64", "manylinux_2_39_x86_64")
    assert tags["aarch64"] == ("linux-arm64", "manylinux_2_39_aarch64")
