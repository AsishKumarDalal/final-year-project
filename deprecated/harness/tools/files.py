"""File + shell tool handlers. Return observation strings, never raise."""

from __future__ import annotations

import subprocess
from pathlib import Path


def handle_read_file(args: dict) -> str:
    path = args.get("path", "")
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: cannot read {path!r}: {exc}"


def handle_write_file(args: dict) -> str:
    path, content = args.get("path", ""), args.get("content", "")
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"
    except Exception as exc:
        return f"ERROR: cannot write {path!r}: {exc}"


def handle_run_command(args: dict) -> str:
    cmd = args.get("command", "")
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=30)
        out = (proc.stdout or "") + (proc.stderr or "")
        return out or f"(exit {proc.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"
    except Exception as exc:
        return f"ERROR: cannot run command: {exc}"
