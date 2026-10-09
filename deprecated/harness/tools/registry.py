"""Tool registry. dispatch() is the ONLY door from a tool call to execution."""

from __future__ import annotations

from . import files

REGISTRY = {
    "read_file": files.handle_read_file,
    "write_file": files.handle_write_file,
    "run_command": files.handle_run_command,
}


def schemas() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "read_file", "description": "Read a file, return its text.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file", "description": "Write content to a file.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}},
             "required": ["path", "content"]}}},
        {"type": "function", "function": {
            "name": "run_command",
            "description": "Run a shell command (30s timeout).",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"}}, "required": ["command"]}}},
        {"type": "function", "function": {
            "name": "finish",
            "description": "Declare the task done with evidence.",
            "parameters": {"type": "object", "properties": {
                "summary": {"type": "string"},
                "evidence": {"type": "string"}},
             "required": ["summary", "evidence"]}}},
    ]


class Finish(Exception):
    def __init__(self, summary: str):
        super().__init__(summary)
        self.summary = summary


def dispatch(name: str, args: dict) -> str:
    """Execute one tool call. finish is intercepted (needs no execution)."""
    if name == "finish":
        raise Finish(str(args.get("summary", "")))
    handler = REGISTRY.get(name)
    if handler is None:
        return f"ERROR: unknown tool {name!r}"
    try:
        return handler(args)
    except Exception as exc:
        return f"ERROR: tool {name} failed: {exc}"
