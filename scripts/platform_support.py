"""Private files and quiet subprocesses on macOS and Windows (stdlib only)."""
import csv
import functools
import io
import os
from pathlib import Path
import subprocess
import tempfile


def process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@functools.lru_cache(maxsize=1)
def windows_sid():
    result = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, check=True, timeout=10,
                            **process_options())
    sid = next(csv.reader(io.StringIO(result.stdout)))[1]
    if not sid.startswith("S-1-") or not all(part.isdigit() for part in sid[2:].split("-")):
        raise RuntimeError("Cannot determine current Windows SID")
    return sid


def protect_private_path(path):
    """Use only for newly created private files/directories, never shared roots."""
    path = Path(path).resolve()
    if os.name == "nt":
        grant = f"*{windows_sid()}:" + ("(OI)(CI)(F)" if path.is_dir() else "(F)")
        result = subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r", grant],
                                capture_output=True, timeout=10, **process_options())
        if result.returncode:
            raise RuntimeError("Private Windows ACL could not be applied; aborting")
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def write_private(path, text):
    """Create a protected temporary file before writing; atomically replace output."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".codex-private-", dir=path.parent)
    os.close(descriptor)
    try:
        protect_private_path(temporary)
        with open(temporary, "w", encoding="utf-8") as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
