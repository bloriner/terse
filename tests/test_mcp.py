"""MCP server tests: spawn the stdio server, handshake, exercise all 4 tools."""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO_ROOT, "mcp", "server.py")


@pytest.fixture(scope="module")
def mcp():
    """A live server process speaking NDJSON JSON-RPC over stdio."""
    proc = subprocess.Popen(
        [sys.executable, SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=REPO_ROOT,
    )
    yield proc
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass
    proc.wait(timeout=10)


def rpc(mcp, method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    mcp.stdin.write(json.dumps(msg) + "\n")
    mcp.stdin.flush()
    line = mcp.stdout.readline()
    assert line, "server closed stdout unexpectedly"
    resp = json.loads(line)
    assert resp.get("jsonrpc") == "2.0"
    assert resp.get("id") == msg_id
    return resp


def notify(mcp, method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    mcp.stdin.write(json.dumps(msg) + "\n")
    mcp.stdin.flush()


def call_tool(mcp, name, arguments, msg_id):
    resp = rpc(mcp, "tools/call", {"name": name, "arguments": arguments}, msg_id=msg_id)
    assert "result" in resp, resp
    result = resp["result"]
    assert result["content"] and result["content"][0]["type"] == "text"
    return result, json.loads(result["content"][0]["text"])


def test_handshake(mcp):
    resp = rpc(
        mcp,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "terse-test", "version": "0"},
        },
        msg_id=1,
    )
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "terse-mcp"
    notify(mcp, "notifications/initialized")  # must not produce a reply


def test_tools_list(mcp):
    resp = rpc(mcp, "tools/list", msg_id=2)
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == ["terse_eval", "terse_transpile", "terse_tokens", "terse_parse",
                     "terse_alias_define", "terse_alias_list", "terse_alias_clear"]
    for tool in resp["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_terse_eval(mcp):
    result, payload = call_tool(mcp, "terse_eval", {"source": "(add 1 2)"}, msg_id=3)
    assert not result.get("isError")
    assert payload == {"value": 3, "output": []}


def test_terse_eval_print_and_gas(mcp):
    result, payload = call_tool(
        mcp, "terse_eval", {"source": "(print (add 1 2))", "gas": 5000}, msg_id=4
    )
    assert not result.get("isError")
    assert payload["output"] == ["3"]
    # gas exhaustion is a structured error, not a traceback
    result, payload = call_tool(
        mcp, "terse_eval", {"source": "(let go (fn (n) (go (add n 1))) (go 0))", "gas": 100}, msg_id=5
    )
    assert result.get("isError")
    assert payload["error"]["code"] == "E205"


def test_terse_eval_error_payload(mcp):
    result, payload = call_tool(mcp, "terse_eval", {"source": "(add 1)"}, msg_id=6)
    assert result.get("isError")
    err = payload["error"]
    assert err["code"] and err["message"]
    assert "traceback" not in json.dumps(payload).lower()


def test_terse_transpile(mcp):
    result, payload = call_tool(
        mcp,
        "terse_transpile",
        {"source": "(def sq (x) (mul x x)) (print (sq 9))"},
        msg_id=7,
    )
    assert not result.get("isError")
    assert "def sq" in payload["python"]
    assert "9" in payload["python"]


def test_terse_transpile_error(mcp):
    result, payload = call_tool(
        mcp, "terse_transpile", {"source": "(def sq (x) (mul x x))"}, msg_id=8
    )
    assert result.get("isError")
    assert payload["error"]["code"] == "E300"


def test_terse_tokens(mcp):
    result, payload = call_tool(
        mcp, "terse_tokens", {"source": "(add 1 2)"}, msg_id=9
    )
    assert not result.get("isError")
    assert isinstance(payload["tokens"], int) and payload["tokens"] > 0
    assert payload["method"] in ("tiktoken-cl100k", "char-estimate")


def test_terse_parse(mcp):
    result, payload = call_tool(
        mcp, "terse_parse", {"source": "(add 1 2)"}, msg_id=10
    )
    assert not result.get("isError")
    assert payload["ast"] == ["add", 1, 2]


def test_terse_parse_error(mcp):
    result, payload = call_tool(
        mcp, "terse_parse", {"source": "(add 1"}, msg_id=11
    )
    assert result.get("isError")
    assert payload["error"]["phase"] == "parse"


def test_unknown_method(mcp):
    resp = rpc(mcp, "nope/not-a-method", msg_id=12)
    assert resp["error"]["code"] == -32601


def test_unknown_tool(mcp):
    result, payload = call_tool(mcp, "terse_frobnicate", {}, msg_id=13)
    assert result.get("isError")
    assert payload["error"]["code"] == "E400"


def test_alias_define_eval(mcp):
    result, payload = call_tool(
        mcp, "terse_alias_define",
        {"alias": "$dbl", "expansion": "(mul x 2)"}, msg_id=20,
    )
    assert payload["defined"] is True
    # $dbl used twice; x bound via let
    result, payload = call_tool(
        mcp, "terse_eval",
        {"source": "(let x 21 (add $dbl $dbl))"}, msg_id=21,
    )
    assert payload["value"] == 84


def test_alias_list_and_clear(mcp):
    call_tool(mcp, "terse_alias_define",
              {"alias": "$one", "expansion": "1"}, msg_id=22)
    result, payload = call_tool(mcp, "terse_alias_list", {}, msg_id=23)
    assert payload["aliases"]["$one"] == "1"
    result, payload = call_tool(mcp, "terse_alias_clear", {}, msg_id=24)
    assert payload["cleared"] >= 1
    result, payload = call_tool(mcp, "terse_alias_list", {}, msg_id=25)
    assert payload["aliases"] == {}


def test_alias_bad_name_rejected(mcp):
    result, payload = call_tool(
        mcp, "terse_alias_define",
        {"alias": "nope", "expansion": "1"}, msg_id=26,
    )
    assert result.get("isError")


def test_alias_cycle_rejected(mcp):
    result, payload = call_tool(
        mcp, "terse_alias_define",
        {"alias": "$loop", "expansion": "$loop"}, msg_id=27,
    )
    assert result.get("isError")
