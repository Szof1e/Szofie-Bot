"""Offline source-release guard; reports paths, never credential values."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TOP = {
    ".env.example",
    ".gitignore",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "bot.py",
    "run.sh",
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "requirements-dev.txt",
    "SECURITY.md",
    ".coveragerc",
}
ALLOWED_DIRS = {"cogs", "szofie", "tests", "scripts", "docs", "assets", ".github"}
PROHIBITED = {"data", "backups", "logs", "output", "reports", "tmp", "__pycache__", ".venv"}
PRIVATE_MECHANIC = re.compile(
    r"RIGGED_|STRATEGIC_RESERVE|_owner_strategic_|_maintain_jet_reserve|"
    r"OWNER_(?:AA_|THOR_|VEHICLE_|ISD_|DEFENSE_|DELAYED_|B52_|M1A2_|COMANCHE_)|"
    r"slots_(?:channel_)?rig\.json|wheel_rig|leaderboard_hidden|forced_slots_pct|"
    r"security-redactions|_ISD_DAMAGE_(?:ATTACKER|OVERRIDE)"
)
TOKEN = re.compile(rb"(?:mfa\.[A-Za-z0-9_-]{60,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{27,})")


LOCAL_PATH = re.compile(rb"/Users/[A-Za-z0-9_.-]+/")


def production_paths():
    return [ROOT / "bot.py", *sorted((ROOT / "cogs").glob("*.py")), *sorted((ROOT / "szofie").glob("*.py"))]


def findings():
    problems = set()
    for path in production_paths():
        text = path.read_text(encoding="utf-8")
        if PRIVATE_MECHANIC.search(text):
            problems.add(str(path.relative_to(ROOT)) + " (identity-specific mechanism)")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value.startswith("owner_")
                        and key.value != "owner_paid"
                    ):
                        problems.add(str(path.relative_to(ROOT)) + " (identity-specific field)")
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                identifiers = [n.id.lower() for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
                if any(
                    re.search(r"(?:^|_)(?:ids?|roles?|channels?|guilds?)(?:_|$)", name)
                    for name in identifiers
                ):
                    for value in ast.walk(node.value):
                        if (
                            isinstance(value, ast.Constant)
                            and isinstance(value.value, int)
                            and 10**16 <= value.value < 10**20
                            and value.value != 123456789012345678
                        ):
                            problems.add(str(path.relative_to(ROOT)) + " (fixed deployment identifier)")
    # Scan all distributable working files, including untracked additions.
    candidates = [p for p in ROOT.iterdir() if p.is_file() and p.name in ALLOWED_TOP]
    for directory in ALLOWED_DIRS:
        candidates.extend(
            p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    for path in candidates:
        content = path.read_bytes()
        if TOKEN.search(content) or LOCAL_PATH.search(content):
            problems.add(str(path.relative_to(ROOT)) + " (credential or private local path)")
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    for raw in result.stdout.decode().split("\0"):
        if not raw:
            continue
        path = Path(raw)
        if (
            any(p in PROHIBITED for p in path.parts)
            or any(p.startswith(".env") and p != ".env.example" for p in path.parts)
            or (len(path.parts) == 1 and raw not in ALLOWED_TOP)
            or (len(path.parts) > 1 and path.parts[0] not in ALLOWED_DIRS)
        ):
            problems.add(raw + " (not distributable)")
        staged = subprocess.run(["git", "show", ":" + raw], cwd=ROOT, capture_output=True, check=True).stdout
        if TOKEN.search(staged):
            problems.add(raw + " (staged credential-shaped content)")
        if path.parts[0] in {"cogs", "szofie"} or raw == "bot.py":
            if PRIVATE_MECHANIC.search(staged.decode("utf-8")):
                problems.add(raw + " (staged identity-specific mechanism)")
    return sorted(problems)


def main():
    problems = findings()
    if problems:
        print("Public-release guard failed; values omitted:\n" + "\n".join(problems))
        return 1
    print("Public-release source and staging safeguards passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
