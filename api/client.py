#!/usr/bin/env python3
"""Tiny stdlib client for the Terse metered execution API.

Usage:
  python -m api.client --terse prog.terse --python ref.py [--stdin in.txt]
                       [--base http://127.0.0.1:8080]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def meter(base_url: str, terse_source: str, python_source: str,
          stdin_text: str = "") -> dict:
    """POST a (terse, python) pair to /meter. Returns the receipt dict.

    Raises RuntimeError with the server's error payload on failure.
    """
    payload = json.dumps({
        "terse_source": terse_source,
        "python_source": python_source,
        "stdin_text": stdin_text,
    }).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/meter",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except Exception:
            detail = {"error": {"code": "http_error", "message": str(exc)}}
        raise RuntimeError(f"meter failed (HTTP {exc.code}): {detail}") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Meter Terse vs Python token savings.")
    ap.add_argument("--terse", required=True, help="path to .terse source file")
    ap.add_argument("--python", required=True, help="path to python baseline file")
    ap.add_argument("--stdin", default=None, help="optional stdin file for both runs")
    ap.add_argument("--base", default="http://127.0.0.1:8080",
                    help="meter API base URL")
    args = ap.parse_args(argv)

    with open(args.terse, encoding="utf-8") as f:
        terse_source = f.read()
    with open(args.python, encoding="utf-8") as f:
        python_source = f.read()
    stdin_text = ""
    if args.stdin:
        with open(args.stdin, encoding="utf-8") as f:
            stdin_text = f.read()

    try:
        rec = meter(args.base, terse_source, python_source, stdin_text)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
