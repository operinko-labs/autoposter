"""Read-only check that computed asset paths match the existing Posterizarr tree.

Usage:
    python scripts/verify_asset_paths.py /assets

Exits non-zero if any library directory contains files this code would not
address, which would mean adoption re-renders instead of reusing them.
"""
import re
import sys
from pathlib import Path

KNOWN = re.compile(r"^(poster|background|Season\d{2,}|S\d{2,}E\d{2,})\.jpg$")


def main(assets_root: str) -> int:
    root = Path(assets_root)
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    total = 0
    unexpected: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            continue
        total += 1
        if not KNOWN.match(path.name):
            unexpected.append(path)

    print(f"scanned {total} asset files under {root}")
    print(f"unrecognised names: {len(unexpected)}")
    for path in unexpected[:25]:
        print(f"  {path}")
    if len(unexpected) > 25:
        print(f"  ... and {len(unexpected) - 25} more")
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/assets"))
