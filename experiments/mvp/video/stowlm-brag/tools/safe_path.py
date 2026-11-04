"""Write fence for the video kit (code12 P1, final-code P2-3).

Every destination must sit strictly inside experiments/mvp/video/, outside brag-vendor/, .tools/ and
record-kit/, with no symlink anywhere between the video folder and the leaf (the leaf included).
Callers check right before each write: safe_dest(), safe_mkdir(), write_text().

  safe_path.py PATH [PATH ...]    exit 1 if any path is not a safe destination (used by the build scripts)

Residual: a symlink swapped in between the check and the write (a race) isn't excluded; nothing else
writes these folders while a build runs.
"""
import os
import sys
from pathlib import Path

VIDEO = Path(__file__).resolve().parents[2]
NO_WRITE = [VIDEO / "brag-vendor", VIDEO / ".tools", VIDEO / "record-kit"]


class UnsafeDest(SystemExit):
    pass


def _lexical(p) -> Path:
    return Path(os.path.normpath(os.path.abspath(p)))


PROTECTED_NAMES = {d.name.casefold() for d in NO_WRITE}


def _same(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _protected(q: Path) -> bool:
    """True if q is, or is inside, a protected root under any spelling: casefolded names (case-insensitive
    filesystems, code14 P2-2) and directory identity (samefile) for every existing ancestor."""
    parts = q.relative_to(VIDEO).parts
    if parts and parts[0].casefold() in PROTECTED_NAMES:
        return True
    cur = q
    while True:
        if any(_same(cur, d) for d in NO_WRITE):
            return True
        if cur == VIDEO or cur.parent == cur:
            return False
        cur = cur.parent


def safe_dest(p) -> Path:
    """The path, checked: inside the fence lexically, not a protected root under any spelling or identity, every
    existing component below VIDEO a real directory or file (never a symlink), and the resolved parent still inside."""
    q = _lexical(p)
    if not q.is_relative_to(VIDEO) or _same(q, VIDEO) or q == VIDEO or _protected(q):
        raise UnsafeDest(f"destination outside the video fence (not brag-vendor/, .tools/, record-kit/): {q}")
    cur = VIDEO
    for part in q.relative_to(VIDEO).parts:
        cur = cur / part
        if cur.is_symlink():
            raise UnsafeDest(f"symlink in destination path: {cur} -> {os.readlink(cur)}")
        if not cur.exists():
            break
    real = q.parent.resolve()
    if not real.is_relative_to(VIDEO) or any(real.is_relative_to(d) or _same(real, d) for d in NO_WRITE):
        raise UnsafeDest(f"destination resolves outside the video fence: {q}")
    return q


def safe_mkdir(p) -> Path:
    q = safe_dest(p)
    q.mkdir(parents=True, exist_ok=True)
    return safe_dest(q)


def write_text(p, text: str) -> None:
    q = safe_dest(p)
    safe_mkdir(q.parent)
    safe_dest(q).write_text(text)


if __name__ == "__main__":
    bad = 0
    for a in sys.argv[1:]:
        try:
            safe_dest(a)
        except UnsafeDest as e:
            print(e, file=sys.stderr)
            bad = 1
    sys.exit(bad)
