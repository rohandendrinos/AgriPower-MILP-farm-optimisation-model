"""Checks that current_code/ (the model repository) is exactly as delivered.

    python tools/check_current_code.py

Compares every file in current_code/ with the SHA-256 hashes in current_code.sha256 and
reports anything changed, missing or added. Operating-system and bytecode leftovers
(.DS_Store, __pycache__, *.pyc) are ignored, as git ignores them too.
To write a fresh manifest after the model repository is replaced on purpose:

    python tools/check_current_code.py --write
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "current_code"
MANIFEST = ROOT / "current_code.sha256"


def tracked_files():
    for path in sorted(MODEL.rglob("*")):
        if not path.is_file():
            continue
        if path.name == ".DS_Store" or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        yield path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    current = {path.relative_to(ROOT).as_posix(): digest(path) for path in tracked_files()}
    if "--write" in sys.argv:
        MANIFEST.write_text("".join(f"{h}  {name}\n" for name, h in current.items()), encoding="utf-8", newline="\n")
        print(f"Wrote {len(current)} hashes to {MANIFEST.name}")
        return 0
    expected = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        value, name = line.split("  ", 1)
        expected[name] = value
    changed = [name for name in expected if name in current and current[name] != expected[name]]
    missing = [name for name in expected if name not in current]
    added = [name for name in current if name not in expected]
    for label, names in (("CHANGED", changed), ("MISSING", missing), ("ADDED", added)):
        for name in names:
            print(f"{label}: {name}")
    if changed or missing or added:
        return 1
    print(f"current_code/ is unchanged ({len(expected)} files checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
