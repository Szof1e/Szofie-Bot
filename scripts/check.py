"""Run offline source, catalogue and ordinary-gameplay release checks."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    files = [ROOT / "bot.py"]
    for directory in ("cogs", "szofie", "tests", "scripts"):
        files.extend((ROOT / directory).glob("*.py"))
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    print(f"Syntax checked for {len(files)} Python files.", flush=True)
    run("-m", "ruff", "check", "bot.py", "cogs", "szofie", "tests", "scripts")
    run("scripts/audit_public_release.py")
    run("scripts/render_public_catalog.py", "--check")
    if args.coverage:
        run("-m", "coverage", "run", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q")
        run("-m", "coverage", "report")
    else:
        run("-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q")
    print("All offline public-release checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
