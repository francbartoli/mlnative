"""A stand-in for the renderer binary: protocol 2.1 without MapLibre.

It answers ``init``, ``render``, ``reload_style`` and ``quit`` the way the
binary does, with flat images: a PNG signature followed by the size, or
RGBA zeros. ``FAKE_DAEMON`` in the environment picks a failure:
``exit-on-render`` quits without answering, ``hang-on-render`` never
answers, ``error-on-render`` answers an error.
"""

import json
import os
import struct
import sys
import time


def reply(out, header: dict, payload: bytes = b"") -> None:
    out.write(json.dumps(header).encode() + b"\n" + payload)
    out.flush()


def main() -> None:
    mode = os.environ.get("FAKE_DAEMON", "")
    out = sys.stdout.buffer
    size, ratio = (0, 0), 1.0
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        command = json.loads(line)
        kind = command["cmd"]
        if kind == "quit":
            return
        if kind == "init":
            size, ratio = (command["width"], command["height"]), command.get("pixel_ratio", 1.0)
            reply(out, {"status": "ok"})
        elif kind == "reload_style":
            reply(out, {"status": "ok"})
        elif kind == "render":
            if mode == "exit-on-render":
                return
            if mode == "hang-on-render":
                time.sleep(3600)
            if mode == "error-on-render":
                reply(out, {"status": "error", "error": "Render failed: boom"})
                continue
            if "width" in command:
                size = (command["width"], command["height"])
            width, height = round(size[0] * ratio), round(size[1] * ratio)
            if command.get("output") == "rgba":
                data = bytes(width * height * 4)
                header = {"status": "ok", "rgba_len": len(data), "width": width, "height": height}
                reply(out, header, data)
            else:
                data = b"\x89PNG" + struct.pack(">II", width, height)
                reply(out, {"status": "ok", "png_len": len(data)}, data)
        else:
            reply(out, {"status": "error", "error": f"Invalid command: {kind}"})


if __name__ == "__main__":
    main()
