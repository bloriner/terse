#!/usr/bin/env python3
"""Terse token-savings-metered execution API (v1 prototype).

Measures the token savings of a Terse program vs a caller-supplied Python
baseline for the same task, verifies both produce the same stdout, and prices
the savings. stdlib only.

Endpoints:
  POST /meter        {"terse_source", "python_source", "stdin_text"?} -> receipt
  GET  /receipt/<id> stored receipt (404 if unknown)
  GET  /ledger       ?limit=&offset=  receipts, newest first
  GET  /health       {"ok": true}

Run:
  python -m api.server            # from the repo root, :8080
  python -m api.server 9000       # custom port

Security posture (prototype): the Python baseline runs in a subprocess with a
hard timeout, but NOT in a sandbox — no network/fs isolation. Do not expose
this to untrusted callers without nsjail/gVisor/etc. in front of it.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from terse import run_source  # noqa: E402
from terse.errors import TerseError  # noqa: E402

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
# We charge 10% of the inference cost the customer avoids by emitting Terse
# instead of Python. Assumed blended inference price: $3.00 / 1M tokens
# (covers input+output across mid-tier models; deliberately conservative).
# The customer keeps the other 90% — the sales pitch is "you save $9 for
# every $1 you pay us."
PRICE_PER_TOKEN = 0.30 / 1_000_000  # USD per token saved ($0.30 / 1M)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_BODY_BYTES = 1_048_576  # 1 MiB request cap
PY_TIMEOUT_S = 5            # hard timeout for the Python baseline subprocess
TERSE_GAS = 1_000_000         # gas budget for the Terse side (interp default)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------
def count_tokens(text: str) -> tuple[int, str]:
    """Return (token_count, method). Prefers tiktoken cl100k_base."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), "tiktoken-cl100k"
    except ImportError:
        return math.ceil(len(text) / 4), "char-estimate"


# ---------------------------------------------------------------------------
# Ledger (sqlite)
# ---------------------------------------------------------------------------
def _db_path() -> str:
    env = os.environ.get("TERSE_LEDGER_PATH")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.sqlite")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS receipts (
             id TEXT PRIMARY KEY,
             ts TEXT NOT NULL,
             terse_tokens INTEGER NOT NULL,
             python_tokens INTEGER NOT NULL,
             tokens_saved INTEGER NOT NULL,
             savings_pct REAL NOT NULL,
             price_usd REAL NOT NULL,
             stdout_match INTEGER NOT NULL,
             method TEXT NOT NULL,
             flags TEXT NOT NULL DEFAULT '[]'
           )"""
    )
    conn.commit()
    return conn


def _store_receipt(rec: dict) -> None:
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO receipts (id, ts, terse_tokens, python_tokens, tokens_saved,"
            " savings_pct, price_usd, stdout_match, method, flags)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                rec["receipt_id"],
                rec["ts"],
                rec["terse_tokens"],
                rec["python_tokens"],
                rec["tokens_saved"],
                rec["savings_pct"],
                rec["price_usd"],
                1 if rec["stdout_match"] else 0,
                rec["method"],
                json.dumps(rec["flags"]),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _get_receipt(rid: str) -> dict | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, ts, terse_tokens, python_tokens, tokens_saved,"
            " savings_pct, price_usd, stdout_match, method, flags"
            " FROM receipts WHERE id = ?",
            (rid,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "receipt_id": row[0],
        "ts": row[1],
        "terse_tokens": row[2],
        "python_tokens": row[3],
        "tokens_saved": row[4],
        "savings_pct": row[5],
        "price_usd": row[6],
        "stdout_match": bool(row[7]),
        "method": row[8],
        "flags": json.loads(row[9]),
    }


def _list_receipts(limit: int, offset: int) -> list[dict]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, ts, terse_tokens, python_tokens, tokens_saved,"
            " savings_pct, price_usd, stdout_match, method, flags"
            " FROM receipts ORDER BY ts DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "receipt_id": r[0],
            "ts": r[1],
            "terse_tokens": r[2],
            "python_tokens": r[3],
            "tokens_saved": r[4],
            "savings_pct": r[5],
            "price_usd": r[6],
            "stdout_match": bool(r[7]),
            "method": r[8],
            "flags": json.loads(r[9]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _run_terse(source: str) -> tuple[str, dict | None]:
    """Returns (stdout, error_dict|None)."""
    try:
        _value, output = run_source(source, gas=TERSE_GAS)
        return output, None
    except TerseError as exc:
        d = exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}
        return "", {"code": d.get("code", "E999"), "message": d.get("message", str(exc))}
    except Exception as exc:  # never leak tracebacks
        return "", {"code": "E999", "message": f"terse runtime failure: {exc}"}


def _run_python(source: str, stdin_text: str) -> tuple[str, str, dict | None]:
    """Returns (stdout, stderr_excerpt, error_dict|None)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", source],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=PY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "", "", {
            "code": "python_timeout",
            "message": f"python baseline exceeded {PY_TIMEOUT_S}s timeout",
        }
    except Exception as exc:
        return "", "", {"code": "python_launch_failed", "message": str(exc)}
    if proc.returncode != 0:
        return "", proc.stderr[-500:], {
            "code": "python_failed",
            "message": f"python baseline exited with code {proc.returncode}",
        }
    return proc.stdout, "", None


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class MeterHandler(BaseHTTPRequestHandler):
    server_version = "TerseMeter/0.1"

    def log_message(self, fmt, *args):  # keep logs on one stderr line
        sys.stderr.write("terse-meter: " + fmt % args + "\n")

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status: int, code: str, message: str) -> None:
        self._send(status, {"error": {"code": code, "message": message}})

    def _read_json(self) -> tuple[dict | None, str | None]:
        length = self.headers.get("Content-Length")
        if length is None:
            return None, "missing Content-Length"
        try:
            n = int(length)
        except ValueError:
            return None, "bad Content-Length"
        if n > MAX_BODY_BYTES:
            return None, "body too large"
        raw = self.rfile.read(n)
        try:
            return json.loads(raw), None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "invalid JSON"

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send(200, {"ok": True})
            return
        if path.startswith("/receipt/"):
            rid = path[len("/receipt/"):]
            if not rid or "/" in rid:
                self._err(400, "bad_receipt_id", "receipt id must be a single path segment")
                return
            rec = _get_receipt(rid)
            if rec is None:
                self._err(404, "receipt_not_found", f"no receipt {rid}")
                return
            self._send(200, rec)
            return
        if path == "/ledger":
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int(qs.get("limit", ["50"])[0]), 1), 200)
                offset = max(int(qs.get("offset", ["0"])[0]), 0)
            except ValueError:
                self._err(400, "bad_pagination", "limit/offset must be integers")
                return
            self._send(200, {"receipts": _list_receipts(limit, offset),
                             "limit": limit, "offset": offset})
            return
        self._err(404, "not_found", f"no route {path}")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/meter":
            self._err(404, "not_found", "only POST /meter is supported")
            return
        payload, err = self._read_json()
        if err == "body too large":
            self._err(413, "body_too_large",
                      f"request body exceeds {MAX_BODY_BYTES} bytes")
            return
        if err:
            self._err(400, "bad_request", err)
            return

        terse_source = payload.get("terse_source")
        python_source = payload.get("python_source")
        stdin_text = payload.get("stdin_text", "")
        if not isinstance(terse_source, str) or not terse_source.strip():
            self._err(400, "bad_request", "terse_source must be a non-empty string")
            return
        if not isinstance(python_source, str) or not python_source.strip():
            self._err(400, "bad_request", "python_source must be a non-empty string")
            return
        if not isinstance(stdin_text, str):
            self._err(400, "bad_request", "stdin_text must be a string")
            return
        if len(terse_source) + len(python_source) > MAX_BODY_BYTES:
            self._err(413, "body_too_large", "sources exceed size cap")
            return

        # 1. Run the Terse side (gas-capped, in-process).
        terse_out, terse_err = _run_terse(terse_source)
        if terse_err:
            self._send(400, {"error": {"code": "terse_" + terse_err["code"],
                                       "message": terse_err["message"]}})
            return

        # 2. Run the Python baseline (subprocess, hard timeout).
        py_out, py_stderr, py_err = _run_python(python_source, stdin_text)
        if py_err:
            body = {"error": {"code": py_err["code"], "message": py_err["message"]}}
            if py_stderr:
                body["error"]["stderr_excerpt"] = py_stderr
            self._send(400, body)
            return

        # 3. Stdout must match — no savings claimed for wrong programs.
        if terse_out != py_out:
            self._send(422, {"error": {
                "code": "stdout_mismatch",
                "message": "terse and python outputs differ; no savings metered",
                "terse_stdout_excerpt": terse_out[:500],
                "python_stdout_excerpt": py_out[:500],
            }})
            return

        # 4. Meter.
        terse_tokens, method = count_tokens(terse_source)
        python_tokens, _m2 = count_tokens(python_source)
        saved = python_tokens - terse_tokens
        pct = (saved / python_tokens * 100.0) if python_tokens else 0.0
        price = round(max(0.0, saved * PRICE_PER_TOKEN), 6)
        flags: list[str] = []
        if pct > 90:
            flags.append("anomaly: savings_pct > 90 — review baseline")
        if saved <= 0:
            flags.append("no-savings: terse used >= python tokens")

        rec = {
            "receipt_id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).isoformat(),
            "terse_tokens": terse_tokens,
            "python_tokens": python_tokens,
            "tokens_saved": saved,
            "savings_pct": round(pct, 2),
            "price_usd": price,
            "stdout_match": True,
            "method": method,
            "flags": flags,
        }
        _store_receipt(rec)
        self._send(200, rec)


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    srv = ThreadingHTTPServer((host, port), MeterHandler)
    print(f"terse-meter listening on http://{host}:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 8080
    run_server(port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
