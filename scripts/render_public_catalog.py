"""Render/check documentation without opening any live guild or user files."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from szofie.config import DEFAULTS
from szofie.guides import public_catalog_markdown


def render() -> str:
    cfg = {"economy." + key: value for key, value in DEFAULTS["economy"].items()}
    return public_catalog_markdown(cfg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "docs/PUBLIC_CATALOG.md"
    expected = render()
    if args.check:
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            print("Public catalog is stale; run scripts/render_public_catalog.py.")
            return 1
        print("Generated public catalog is current.")
    else:
        path.write_text(expected, encoding="utf-8")
        print("Rendered docs/PUBLIC_CATALOG.md from ordinary defaults.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
