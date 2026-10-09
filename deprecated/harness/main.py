"""Terminal REPL. P1: in-memory history, /exit only. Sessions land in P3."""

from __future__ import annotations

from agent.loop import run


def main() -> int:
    history: list = []
    print("harness P1 — type /exit to quit")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line == "/exit":
            break
        try:
            answer, history = run(line, history)
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue
        print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
