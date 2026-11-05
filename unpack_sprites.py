#!/usr/bin/env python3
"""Unpack butterfly pictures shipped as base64 parts."""

from __future__ import annotations

import base64
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: unpack_sprites.py SOURCE_DIR DEST_DIR", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    dest = Path(sys.argv[2])
    dest.mkdir(parents=True, exist_ok=True)
    parts_files = sorted(root.glob("*.png.parts"))
    if not parts_files:
        print("no butterfly pictures in " + str(root), file=sys.stderr)
        return 1
    for parts_file in parts_files:
        name = parts_file.name[: -len(".parts")]
        count = int(parts_file.read_text(encoding="utf-8").strip())
        blob = "".join(
            (root / f"{name}.b64.{index:02d}").read_text(encoding="utf-8")
            for index in range(count)
        )
        raw = base64.b64decode(blob)
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            print("bad picture " + name, file=sys.stderr)
            return 1
        (dest / name).write_bytes(raw)
    print(f"unpacked {len(parts_files)} butterflies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
