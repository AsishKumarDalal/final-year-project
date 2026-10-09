"""Fetch Wikipedia article extracts into the test corpus (stdlib only, no key).

Wikipedia's API needs no authentication, and medical articles are CC BY-SA —
suitable for a *test* corpus. The main corpus decision (§19.4) is separate;
whatever lands in ``data/corpus_test/`` is test data, not the curated index.

Usage:
    python3 -m graphrag.wikipedia "Myocardial infarction" "Aspirin" --out data/corpus_test
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .textnorm import slugify

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "graphrag-indexer/0.1 (+medical-harness; test-corpus-fetch)"


def fetch_extract(title: str, *, timeout_s: int = 60) -> tuple[str, str]:
    """Return (resolved_title, plain_text). Raises on failure."""
    params = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": "1",
        "redirects": "1", "format": "json", "titles": title,
    })
    request = urllib.request.Request(
        f"{API}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    pages = (payload.get("query", {}) or {}).get("pages", {}) or {}
    for page in pages.values():
        if "missing" in page:
            raise ValueError(f"no Wikipedia article for {title!r}")
        return page.get("title", title), page.get("extract", "")
    raise ValueError(f"no Wikipedia article for {title!r}")


def clean_extract(text: str) -> str:
    """Collapse decoration and whitespace. Section structure is preserved."""
    text = re.sub(r"={2,}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def save_article(title: str, text: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slugify(title)}.txt"
    header = f"# {title}\n# Source: English Wikipedia (CC BY-SA). Test corpus only.\n\n"
    path.write_text(header + clean_extract(text), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch Wikipedia test corpus")
    parser.add_argument("titles", nargs="+")
    parser.add_argument("--out", default="data/corpus_test")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    saved = 0
    for title in args.titles:
        try:
            resolved, text = fetch_extract(title)
        except Exception as exc:  # noqa: BLE001 — one bad title must not stop the run
            print(f"SKIP {title!r}: {exc}")
            continue
        if len(text) < 2000:
            print(f"SKIP {title!r}: extract too short ({len(text)} chars)")
            continue
        path = save_article(resolved, text, out_dir)
        print(f"saved {resolved!r} -> {path} ({len(text)} chars)")
        saved += 1
    print(f"{saved}/{len(args.titles)} articles saved to {out_dir}")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())