#!/usr/bin/env python3
"""Terse MCP server (stdio transport, stdlib only).

Speaks MCP over newline-delimited JSON-RPC 2.0 on stdin/stdout
(no Content-Length framing). Lets AI coding agents parse, evaluate,
transpile, and token-count Terse source.

Run:
    python -m mcp.server        # from the repo root
    python mcp/server.py        # from the repo root
"""

from __future__ import annotations

import json
import math
import os
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "terse-mcp"
SERVER_VERSION = "0.3.0"
DEFAULT_GAS = 100_000
MAX_GAS = 10_000_000


def _ensure_repo_on_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)  # repo root: <repo>/mcp -> <repo>
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_repo_on_path()

import terse  # noqa: E402
from terse.errors import TerseError  # noqa: E402


# ---------------------------------------------------------------------------
# value conversion
# ---------------------------------------------------------------------------

def _truncate(s: str, limit: int = 500) -> str:
    return s if len(s) <= limit else s[:limit] + "..."


def _tuple_str(t: tuple) -> str:
    parts = []
    for v in t:
        if isinstance(v, tuple):
            parts.append(_tuple_str(v))
        elif isinstance(v, str):
            parts.append(repr(v))
        else:
            parts.append(str(v))
    inner = ", ".join(parts)
    return "(%s%s)" % (inner, "," if len(t) == 1 else "")


def json_safe(value):
    """Convert an interpreter value into JSON-serializable data.

    Tuples (and any other non-JSON-native objects) become a readable
    string form; lists/dicts recurse; non-finite floats become strings.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return _tuple_str(value)
    return "<%s: %s>" % (type(value).__name__, _truncate(str(value)))


def ast_to_json(node):
    """Convert a Terse AST (nested tuples, possibly containing lists) to JSON."""
    if isinstance(node, tuple):
        return [ast_to_json(x) for x in node]
    if isinstance(node, list):
        return [ast_to_json(x) for x in node]
    if isinstance(node, dict):
        return {str(k): ast_to_json(v) for k, v in node.items()}
    return json_safe(node)


def error_payload(exc: BaseException) -> dict:
    """Structured error payload. Never includes a traceback."""
    if isinstance(exc, TerseError):
        d = exc.to_dict()
        payload = {"code": d["code"], "message": d["message"], "phase": d["phase"]}
        if d.get("hint"):
            payload["hint"] = d["hint"]
        if d.get("pos"):
            payload["pos"] = d["pos"]
        if d.get("op"):
            payload["op"] = d["op"]
        return {"error": payload}
    return {
        "error": {
            "code": "E999",
            "message": "internal error: %s: %s"
            % (type(exc).__name__, _truncate(str(exc) or "unknown")),
            "phase": "internal",
        }
    }


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _require_source(args: dict) -> str:
    source = args.get("source")
    if not isinstance(source, str):
        raise ValueError("missing or invalid 'source': expected a string")
    return source


def tool_terse_eval(args: dict) -> dict:
    source = _require_source(args)
    gas = args.get("gas", DEFAULT_GAS)
    if isinstance(gas, bool) or not isinstance(gas, int):
        raise ValueError("'gas' must be an integer")
    if gas < 1:
        raise ValueError("'gas' must be >= 1")
    gas = min(gas, MAX_GAS)
    try:
        value, output = terse.run_source(source, gas=gas)
    except TerseError as exc:
        return error_payload(exc)
    except Exception as exc:  # never leak tracebacks
        return error_payload(exc)
    lines = output.splitlines() if output else []
    return {"value": json_safe(value), "output": lines}


def tool_terse_transpile(args: dict) -> dict:
    source = _require_source(args)
    try:
        python_src = terse.to_python(terse.parse(source))
    except TerseError as exc:
        return error_payload(exc)
    except Exception as exc:
        return error_payload(exc)
    return {"python": python_src}


def tool_terse_tokens(args: dict) -> dict:
    source = _require_source(args)
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return {"tokens": len(enc.encode(source)), "method": "tiktoken-cl100k"}
    except ImportError:
        return {"tokens": math.ceil(len(source) / 4), "method": "char-estimate"}


def tool_terse_parse(args: dict) -> dict:
    source = _require_source(args)
    try:
        ast = terse.parse(source)
    except TerseError as exc:
        return error_payload(exc)
    except Exception as exc:
        return error_payload(exc)
    return {"ast": ast_to_json(ast)}


TOOLS = {
    "terse_eval": {
        "fn": tool_terse_eval,
        "description": (
            "Evaluate Terse source with the tree-walking interpreter. "
            "Returns the result value (JSON-safe) and captured print output lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Terse source code to evaluate."},
                "gas": {
                    "type": "integer",
                    "description": "Evaluation gas limit (default 100000).",
                    "default": DEFAULT_GAS,
                    "minimum": 1,
                },
            },
            "required": ["source"],
        },
    },
    "terse_transpile": {
        "fn": tool_terse_transpile,
        "description": "Transpile Terse source to readable, executable Python.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Terse source code to transpile."}
            },
            "required": ["source"],
        },
    },
    "terse_tokens": {
        "fn": tool_terse_tokens,
        "description": (
            "Count tokens of Terse source with tiktoken cl100k_base; "
            "falls back to a ceil(len/4) character estimate when tiktoken is unavailable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Terse source code to measure."}
            },
            "required": ["source"],
        },
    },
    "terse_parse": {
        "fn": tool_terse_parse,
        "description": "Parse Terse source and return the AST as JSON (tuples become lists).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Terse source code to parse."}
            },
            "required": ["source"],
        },
    },
}


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(name: str, args: dict) -> dict:
    try:
        tool = TOOLS.get(name)
        if tool is None:
            raise KeyError("unknown tool: %s" % name)
        if not isinstance(args, dict):
            args = {}
        payload = tool["fn"](args)
    except (ValueError, KeyError) as exc:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": {"code": "E400", "message": str(exc)}})}],
            "isError": True,
        }
    except Exception as exc:  # never leak tracebacks
        return {
            "content": [{"type": "text", "text": json.dumps(error_payload(exc))}],
            "isError": True,
        }
    text = json.dumps(payload)
    result = {"content": [{"type": "text", "text": text}]}
    if isinstance(payload, dict) and "error" in payload:
        result["isError"] = True
    return result


def handle(msg: dict):
    """Return a JSON-RPC response dict, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return None
    method = msg["method"]
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        return _ok(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        tools = [
            {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
            for name, t in TOOLS.items()
        ]
        return _ok(msg_id, {"tools": tools})
    if method == "tools/call":
        if is_notification:
            return None
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        return _ok(msg_id, _call_tool(name, args))
    # unknown method
    if is_notification:
        return None
    return _err(msg_id, -32601, "Method not found: %s" % method)


def serve() -> None:
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print("invalid JSON-RPC line: %s" % exc, file=sys.stderr)
            continue
        try:
            resp = handle(msg)
        except Exception as exc:  # absolute last resort; never crash the loop
            print("handler crashed: %r" % exc, file=sys.stderr)
            continue
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
