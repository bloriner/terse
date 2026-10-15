"""Terse v0.1 error protocol.

Every error the Terse toolchain raises is a `TerseError` carrying machine-readable
fields, so an LLM repair loop can parse the failure, locate it, and fix it
without guessing. Errors are never bare strings.

Field conventions (shared by parser, interpreter, transpiler workers):
  - code: stable error code, e.g. "E102". See ERROR_CODES below.
  - phase: "parse" | "runtime" | "transpile" (set by the subclass).
  - op: op name involved, or None.
  - pos: {"line": int, "col": int, "index": int} or None.
      line is 1-based, col is 0-based, index is the 0-based char offset
      into the source string. (A later compiler pass may attach positions
      to runtime errors; until then the interpreter passes pos=None.)
  - message: one concise machine-readable sentence.
  - expected / got: what was wanted vs. what was seen, or None.
  - hint: a concrete fix suggestion aimed at an AI agent, or None.
"""

from __future__ import annotations

__all__ = [
    "TerseError",
    "ParseError",
    "TerseRuntimeError",
    "TranspileError",
    "ERROR_CODES",
]


class TerseError(Exception):
    """Base class for all Terse errors. Subclasses fix `phase`."""

    phase = "error"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        op: str | None = None,
        pos: dict | None = None,
        hint: str | None = None,
        expected: str | None = None,
        got: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.op = op
        self.pos = dict(pos) if pos is not None else None
        self.hint = hint
        self.expected = expected
        self.got = got
        super().__init__(str(self))

    def to_dict(self) -> dict:
        """Return every field as a plain JSON-serializable dict."""
        return {
            "code": self.code,
            "phase": self.phase,
            "op": self.op,
            "pos": dict(self.pos) if self.pos is not None else None,
            "message": self.message,
            "expected": self.expected,
            "got": self.got,
            "hint": self.hint,
        }

    def __str__(self) -> str:
        s = f"[{self.code} {self.phase}] {self.message}"
        if self.hint:
            s += f" (hint: {self.hint})"
        return s

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({str(self)!r})"


class ParseError(TerseError):
    """Concrete-syntax failure: tokenizing or parsing the source."""

    phase = "parse"


class TerseRuntimeError(TerseError):
    """Runtime failure raised by the Terse interpreter.

    Named TerseRuntimeError (not RuntimeError) to avoid shadowing the builtin.
    """

    phase = "runtime"


class TranspileError(TerseError):
    """Failure while transpiling Terse to another language (e.g. Python)."""

    phase = "transpile"


# --- Error code registry: code -> short description -------------------------
ERROR_CODES: dict[str, str] = {
    # Parse phase
    "E100": "unexpected-eof",
    "E101": "unexpected-token",
    "E103": "arity-mismatch",
    "E104": "bad-literal",
    "E105": "bad-binding",
    # Runtime phase
    "E200": "unbound-variable",    # reference to a variable with no binding in scope
    "E201": "division-by-zero",    # div/mod by zero (also 0 ** negative)
    "E202": "key-or-index-error",  # missing dict key, out-of-range list index,
                                   # or wrong type used for get/put access
    "E203": "conversion-error",    # failed int()/float() conversion
    "E204": "assertion-failed",    # assert evaluated a falsy condition
    "E205": "gas-exhausted",       # evaluation gas ran out
    "E206": "not-callable",        # call of a non-function value
    "E207": "call-arity-mismatch", # wrong number of arguments for an op/call
    "E208": "type-error",          # op applied to unsupported type(s)
    # Transpile phase
    "E300": "transpile-unsupported",  # e.g. `return` outside fn-body statement position
}
