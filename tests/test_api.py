"""Tests for the token-savings-metered execution API (api/server.py).

Spins up the real HTTP server in a background thread against a temp sqlite
ledger; no network beyond loopback.
"""

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from api import server as meter_server

TASKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "bench", "tasks")


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("TERSE_LEDGER_PATH", str(tmp_path / "ledger.sqlite"))
    srv = meter_server.ThreadingHTTPServer(("127.0.0.1", 0), meter_server.MeterHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    thread.join(timeout=5)


def post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def read_task(name):
    with open(os.path.join(TASKS, name + ".terse"), encoding="utf-8") as f:
        terse_src = f.read()
    with open(os.path.join(TASKS, name + ".py"), encoding="utf-8") as f:
        py_src = f.read()
    return terse_src, py_src


def test_health(svc):
    status, body = get(svc, "/health")
    assert status == 200 and body == {"ok": True}


def test_meter_fizzbuzz_positive_savings(svc):
    terse_src, py_src = read_task("fizzbuzz")
    status, rec = post(svc, "/meter",
                       {"terse_source": terse_src, "python_source": py_src})
    assert status == 200, rec
    assert rec["stdout_match"] is True
    assert rec["tokens_saved"] > 0
    assert rec["terse_tokens"] < rec["python_tokens"]
    assert rec["price_usd"] > 0
    assert rec["receipt_id"]
    for key in ("terse_tokens", "python_tokens", "tokens_saved",
                "savings_pct", "price_usd", "receipt_id", "method", "ts"):
        assert key in rec, key
    # pricing math: $0.30 / 1M tokens
    assert rec["price_usd"] == round(rec["tokens_saved"] * 0.30 / 1_000_000, 6)


def test_meter_stdout_mismatch_rejected(svc):
    status, body = post(svc, "/meter", {
        "terse_source": '(print "terse")',
        "python_source": 'print("python")',
    })
    assert status == 422
    assert body["error"]["code"] == "stdout_mismatch"


def test_meter_invalid_terse_structured_error(svc):
    status, body = post(svc, "/meter", {
        "terse_source": "(add 1",
        "python_source": 'print(1)',
    })
    assert status == 400
    assert body["error"]["code"].startswith("terse_")


def test_meter_python_crash_rejected(svc):
    status, body = post(svc, "/meter", {
        "terse_source": '(print 1)',
        "python_source": 'raise SystemExit(3)',
    })
    assert status == 400
    assert body["error"]["code"] == "python_failed"


def test_meter_python_timeout(svc, monkeypatch):
    monkeypatch.setattr(meter_server, "PY_TIMEOUT_S", 1)
    status, body = post(svc, "/meter", {
        "terse_source": '(print 1)',
        "python_source": 'import time; time.sleep(30); print(1)',
    })
    assert status == 400
    assert body["error"]["code"] == "python_timeout"


def test_receipt_roundtrip(svc):
    terse_src, py_src = read_task("fib")
    status, rec = post(svc, "/meter",
                       {"terse_source": terse_src, "python_source": py_src})
    assert status == 200, rec
    status, fetched = get(svc, f"/receipt/{rec['receipt_id']}")
    assert status == 200
    assert fetched["receipt_id"] == rec["receipt_id"]
    assert fetched["tokens_saved"] == rec["tokens_saved"]
    assert fetched["price_usd"] == rec["price_usd"]


def test_receipt_not_found(svc):
    status, body = get(svc, "/receipt/doesnotexist")
    assert status == 404
    assert body["error"]["code"] == "receipt_not_found"


def test_ledger_lists_newest_first(svc):
    for name in ("fib", "fizzbuzz"):
        terse_src, py_src = read_task(name)
        status, _ = post(svc, "/meter",
                         {"terse_source": terse_src, "python_source": py_src})
        assert status == 200
    status, body = get(svc, "/ledger?limit=2")
    assert status == 200
    ids = [r["receipt_id"] for r in body["receipts"]]
    assert len(ids) == 2
    # newest first: second metered (fizzbuzz) comes before fib
    terse_src, py_src = read_task("fizzbuzz")
    status, rec = post(svc, "/meter",
                       {"terse_source": terse_src, "python_source": py_src})
    status, body = get(svc, "/ledger?limit=1")
    assert body["receipts"][0]["receipt_id"] == rec["receipt_id"]


def test_body_too_large_rejected(svc, monkeypatch):
    monkeypatch.setattr(meter_server, "MAX_BODY_BYTES", 64)
    status, body = post(svc, "/meter", {
        "terse_source": "(print " + "x" * 100 + ")",
        "python_source": 'print(1)',
    })
    assert status == 413
    assert body["error"]["code"] == "body_too_large"
