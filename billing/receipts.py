#!/usr/bin/env python3
"""Signed meter receipts for the Terse metered API.

A receipt is the dict produced by POST /meter in ``api/server.py``:

    receipt_id, ts (UTC ISO), terse_tokens, python_tokens, tokens_saved,
    savings_pct, price_usd, stdout_match, method, flags

When the API server has ``TERSE_METER_SECRET`` set, it attaches a ``sig``
field:

    "sig": "hmac-sha256:<hex>"

computed over a canonical JSON encoding of every other field. This module
canonicalizes, signs, and verifies receipts, and loads receipts from the
API's ledger, from JSONL exports, or from the server's sqlite ledger.

Signature scheme is deliberately boring: HMAC-SHA256 (stdlib ``hmac``).
Keep the secret in the environment (``TERSE_METER_SECRET``), rotate it like
any other HMAC key, and never put it in a repo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone

SIG_SCHEME = "hmac-sha256"
SIG_FIELD = "sig"

REQUIRED_FIELDS = (
    "receipt_id",
    "ts",
    "terse_tokens",
    "python_tokens",
    "tokens_saved",
    "savings_pct",
    "price_usd",
    "stdout_match",
    "method",
    "flags",
)


def canonical_bytes(rec: dict) -> bytes:
    """Canonical bytes of a receipt (everything except ``sig``).

    Deterministic: keys sorted, compact separators, UTF-8. Any change to any
    field — including reordering — changes the signature input, so a tampered
    receipt fails verification.
    """
    body = {k: v for k, v in rec.items() if k != SIG_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def sign_receipt(rec: dict, secret: str) -> dict:
    """Return a copy of *rec* with an HMAC-SHA256 ``sig`` attached."""
    if not secret:
        raise ValueError("cannot sign a receipt with an empty secret")
    body = {k: v for k, v in rec.items() if k != SIG_FIELD}
    mac = hmac.new(secret.encode("utf-8"), canonical_bytes(body),
                   hashlib.sha256).hexdigest()
    signed = dict(body)
    signed[SIG_FIELD] = f"{SIG_SCHEME}:{mac}"
    return signed


def verify_receipt(rec: dict, secret: str) -> tuple[bool, str]:
    """Verify a signed receipt.

    Returns ``(True, "ok")`` or ``(False, <reason>)``. Reasons: missing sig,
    unknown scheme, missing required fields, or signature mismatch (tampered
    or wrong secret).
    """
    if not secret:
        return False, "no secret configured"
    sig = rec.get(SIG_FIELD)
    if not sig:
        return False, "missing signature (unsigned receipt)"
    scheme, _, mac = sig.partition(":")
    if scheme != SIG_SCHEME or not mac:
        return False, f"unknown signature scheme: {scheme!r}"
    missing = [f for f in REQUIRED_FIELDS if f not in rec]
    if missing:
        return False, f"missing required fields: {', '.join(missing)}"
    expected = hmac.new(secret.encode("utf-8"), canonical_bytes(rec),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return False, "signature mismatch (tampered or wrong secret)"
    return True, "ok"


# ---------------------------------------------------------------------------
# Receipt loading
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def receipts_from_ledger(base_url: str, limit: int = 200) -> list[dict]:
    """Fetch receipts from the meter API's /ledger endpoint (paged)."""
    out: list[dict] = []
    offset = 0
    while True:
        page = _http_get_json(
            f"{base_url.rstrip('/')}/ledger?limit={limit}&offset={offset}")
        batch = page.get("receipts", [])
        out.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return out


def receipts_from_jsonl(path: str) -> list[dict]:
    """Load receipts from a JSONL file (one receipt per line)."""
    recs: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}")
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{lineno}: not a JSON object")
            recs.append(obj)
    return recs


def receipts_from_ledger_sqlite(db_path: str) -> list[dict]:
    """Read receipts straight from the API server's ledger.sqlite."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, ts, terse_tokens, python_tokens, tokens_saved,"
            " savings_pct, price_usd, stdout_match, method, flags, sig"
            " FROM receipts ORDER BY ts DESC, rowid DESC").fetchall()
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
            # Old rows (before signing was added) have sig NULL; verify()
            # rejects them as unsigned.
            SIG_FIELD: r[10],
        }
        for r in rows
    ]


def partition_verified(receipts: list[dict], secret: str
                       ) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Split receipts into (verified, [(receipt, reason), ...]) using *secret*."""
    ok: list[dict] = []
    bad: list[tuple[dict, str]] = []
    for rec in receipts:
        good, reason = verify_receipt(rec, secret)
        if good:
            ok.append(rec)
        else:
            bad.append((rec, reason))
    return ok, bad


# ---------------------------------------------------------------------------
# Billing periods
# ---------------------------------------------------------------------------

def period_bounds(ts_iso: str) -> tuple[str, str]:
    """Return (period_start, period_end) as UTC ISO strings for the calendar
    month containing *ts_iso*. Stripe meters aggregate within billing periods,
    so we report usage per calendar month (UTC)."""
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start.isoformat(), end.isoformat()


def period_label(ts_iso: str) -> str:
    """'YYYY-MM' label for the calendar month of *ts_iso* (UTC)."""
    start, _ = period_bounds(ts_iso)
    return start[:7]


def default_secret() -> str:
    """Read the receipt-signing secret from the environment."""
    return os.environ.get("TERSE_METER_SECRET", "")
