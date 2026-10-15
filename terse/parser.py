"""Terse v0.1 parser: concrete S-expression syntax -> AST tuples.

AST contract (shared by all workers):
  - AST node = Python tuple ("opname", arg1, arg2, ...).
  - Literals are raw Python values: int, float, str, bool, None.
  - Variable reference = ("var", "name").
  - Binding positions (let name, for name, fn param list) stay plain strings.

The parser is a hand-written tokenizer + recursive descent with line/col/index
tracking. It never crashes on malformed input: every failure raises ParseError
with code/pos/expected/got/hint. Op names are checked against the catalog
(E102) and arities are checked at parse time (E103).
"""

from __future__ import annotations

from .errors import ParseError

__all__ = ["parse", "try_parse", "tokenize", "ARITY", "OPS"]


# --- Op catalog: op -> arity spec -------------------------------------------
# Arity spec forms:
#   (lo, hi)  exactly lo..hi args (hi=None means unbounded)
#   "even"    even arg count (dict: alternating key/value)
ARITY: dict[str, tuple[int, int | None] | str] = {
    # arithmetic
    "add": (2, 2), "sub": (2, 2), "mul": (2, 2),
    "div": (2, 2), "mod": (2, 2), "pow": (2, 2),
    "neg": (1, 1),
    # (divis x n): x is divisible by n -- fused (eq (mod x n) 0).
    # Discovered by library learning (notes/library-learning.md).
    "divis": (2, 2),
    # (dec x): x - 1. The hot primitive for counted recursion (fib etc.):
    # 3 tokens vs 5 for (sub x 1).
    "dec": (1, 1),
    # comparison
    "eq": (2, 2), "ne": (2, 2), "lt": (2, 2),
    "le": (2, 2), "gt": (2, 2), "ge": (2, 2),
    # logic
    "and": (2, None), "or": (2, None), "not": (1, 1),
    # control
    # "if" is n-ary: (if c1 e1 c2 e2 ... [else]). Pairs of condition/branch;
    # a trailing odd argument is the else. Even arg count with all conditions
    # false evaluates to null.
    "if": (2, None), "while": (2, 2), "for": (3, 3),
    "seq": (0, None), "let": (3, 3), "fn": (2, 2),
    # (def name (params) body): top-level named function definition.
    # (fold (acc x) init coll body): loop with accumulator; the recursion
    # scheme for "loop with state" without explicit recursion.
    "def": (3, 3), "fold": (4, 4),
    "return": (1, 1),
    "assert": (1, 2),
    # collections
    "list": (0, None), "dict": "even", "get": (2, 2),
    "put": (3, 3), "len": (1, 1), "range": (1, 3),
    "push": (2, 2), "cat": (0, None),
    # (inc coll key [by]): persistent increment of a counter. Missing key
    # defaults to 0, so no pre-seeding is needed. by defaults to 1.
    "inc": (2, 3),
    # (map f coll): list of f(x). (filter pred coll): elements where pred
    # is truthy. (rev coll): reversed list or string.
    # (keep f coll): filter+map fusion -- list of non-null f(x) values.
    "map": (2, 2), "filter": (2, 2), "rev": (1, 1), "keep": (2, 2),
    # (sum coll): sum of a list of numbers -- fused
    # (fold (acc x) 0 coll (add acc x)).
    # Discovered by library learning (notes/library-learning.md).
    "sum": (1, 1),
    # strings / conversions
    "str": (1, 1), "int": (1, 1), "float": (1, 1),
    "upper": (1, 1), "lower": (1, 1),
    "split": (2, 2), "join": (2, 2),
    # (fmt template arg...): "{}" placeholders filled left to right.
    "fmt": (1, None),
    # effects
    "print": (0, None),
}
OPS = tuple(sorted(ARITY))

# Ops whose arguments include binding positions that must be parsed as raw
# symbols (plain strings), not as expressions.
_BINDING_OPS = ("let", "for", "fn", "def", "fold")


def _describe_arity(op: str) -> str:
    spec = ARITY[op]
    if spec == "even":
        return "an even number of arguments (alternating key/value)"
    lo, hi = spec
    if hi is None:
        return f"{lo} or more arguments"
    if lo == hi:
        return f"exactly {lo} argument(s)"
    return f"{lo}-{hi} arguments"


# --- Tokenizer ---------------------------------------------------------------
# Token = dict {"kind", "value", "line", "col", "index"}.
# kinds: "lparen" "rparen" "int" "float" "string" "bool" "null" "symbol" "eof".
# line is 1-based; col and index are 0-based.

_DELIMS = " \t\r\n();\""



def _read_string(src: str, i: int, line: int, col: int, start: dict):
    """Read a double-quoted string starting at src[i] == '\"'.

    Returns (token, new_i, new_line, new_col). Raises ParseError E104 on
    unterminated strings or bad escapes.
    """
    n = len(src)
    i += 1
    col += 1
    buf: list[str] = []
    closed = False
    while i < n:
        ch = src[i]
        if ch == '"':
            closed = True
            i += 1
            col += 1
            break
        if ch == "\\":
            if i + 1 >= n:
                break
            e = src[i + 1]
            if e == "n":
                buf.append("\n")
            elif e == "t":
                buf.append("\t")
            elif e == '"':
                buf.append('"')
            elif e == "\\":
                buf.append("\\")
            else:
                esc_pos = {"line": line, "col": col, "index": i}
                raise ParseError(
                    "E104",
                    f"bad escape '\\{e}' in string literal",
                    pos=esc_pos,
                    expected='one of \\" \\\\ \\n \\t after backslash',
                    got=f"'\\{e}'",
                    hint=f"replace '\\{e}' with a supported escape "
                         "(\\\", \\\\, \\n, \\t)",
                )
            i += 2
            col += 2
            continue
        if ch == "\n":
            raise ParseError(
                "E104",
                "unterminated string literal (newline inside string)",
                pos={"line": line, "col": col, "index": i},
                expected='closing \'"\' on the same line',
                got="newline",
                hint="close the string with a double-quote before the line ends",
            )
        buf.append(ch)
        i += 1
        col += 1
    if not closed:
        raise ParseError(
            "E104",
            "unterminated string literal",
            pos=start,
            expected='closing \'"\'',
            got="end of input",
            hint="add a closing double-quote",
        )
    tok = {"kind": "string", "value": "".join(buf), **start}
    return tok, i, line, col


# Public tokenizer: split source into token dicts.
# Raises ParseError (E104) on bad string literals.
def tokenize(src: str) -> list[dict]:
    tokens: list[dict] = []
    i, n = 0, len(src)
    line, col = 1, 0

    def here() -> dict:
        return {"line": line, "col": col, "index": i}

    while i < n:
        c = src[i]
        if c in " \t\r\n":
            if c == "\n":
                line += 1
                col = 0
            else:
                col += 1
            i += 1
            continue
        if c == ";":
            while i < n and src[i] != "\n":
                i += 1
                col += 1
            continue
        start = here()
        if c == "(":
            tokens.append({"kind": "lparen", "value": "(", **start})
            i += 1
            col += 1
            continue
        if c == ")":
            tokens.append({"kind": "rparen", "value": ")", **start})
            i += 1
            col += 1
            continue
        if c == '"':
            tok, i, line, col = _read_string(src, i, line, col, start)
            tokens.append(tok)
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and src[i + 1].isdigit()):
            j = i
            if src[j] == "-":
                j += 1
            while j < n and src[j].isdigit():
                j += 1
            is_float = False
            if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and src[j].isdigit():
                    j += 1
            text = src[i:j]
            value = float(text) if is_float else int(text)
            tokens.append({
                "kind": "float" if is_float else "int",
                "value": value, "raw": text, **start,
            })
            col += j - i
            i = j
            continue
        j = i
        while j < n and src[j] not in _DELIMS:
            j += 1
        text = src[i:j]
        if text == "true":
            tokens.append({"kind": "bool", "value": True, **start})
        elif text == "false":
            tokens.append({"kind": "bool", "value": False, **start})
        elif text == "null":
            tokens.append({"kind": "null", "value": None, **start})
        else:
            tokens.append({"kind": "symbol", "value": text, **start})
        col += j - i
        i = j

    tokens.append({"kind": "eof", "value": None,
                   "line": line, "col": col, "index": n})
    return tokens


# --- Recursive descent -------------------------------------------------------


def _pos_of(tok: dict) -> dict:
    return {"line": tok["line"], "col": tok["col"], "index": tok["index"]}


def _describe_token(tok: dict) -> str:
    kind, value = tok["kind"], tok["value"]
    if kind == "eof":
        return "end of input"
    if kind == "lparen":
        return "'('"
    if kind == "rparen":
        return "')'"
    if kind == "symbol":
        return f"symbol '{value}'"
    if kind == "string":
        return f'string "{value}"'
    if kind in ("int", "float"):
        return f"number {value!r}"
    if kind == "bool":
        return f"boolean {value!r}"
    if kind == "null":
        return "null"
    return kind


class _Parser:
    def __init__(self, tokens: list[dict]) -> None:
        self.toks = tokens
        self.i = 0

    def peek(self) -> dict:
        return self.toks[self.i]

    def advance(self) -> dict:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    # -- program ---------------------------------------------------------
    def parse_program(self) -> tuple:
        forms = []
        while self.peek()["kind"] != "eof":
            forms.append(self.parse_expr())
        if not forms:
            eof = self.peek()
            raise ParseError(
                "E100", "empty program",
                pos=_pos_of(eof),
                expected="at least one top-level form",
                got="end of input",
                hint="write an expression, e.g. (add 1 2)",
            )
        if len(forms) == 1:
            return forms[0]
        return ("seq", *forms)

    # -- expressions -----------------------------------------------------
    def parse_expr(self) -> tuple:
        tok = self.peek()
        kind = tok["kind"]
        if kind == "lparen":
            return self.parse_list()
        if kind in ("int", "float", "string", "bool", "null"):
            self.advance()
            return tok["value"]
        if kind == "symbol":
            self.advance()
            return ("var", tok["value"])
        if kind == "rparen":
            raise ParseError(
                "E101", "unexpected ')'",
                pos=_pos_of(tok),
                expected="an expression",
                got="')'",
                hint="remove the extra ')' or open a form with '(' first",
            )
        # kind == "eof"
        raise ParseError(
            "E100", "unexpected end of input",
            pos=_pos_of(tok),
            expected="an expression (an '(' may be unclosed)",
            got="end of input",
            hint="add the missing closing ')'",
        )

    # -- list forms ------------------------------------------------------
    def parse_list(self) -> tuple:
        self.advance()  # consume '('
        head = self.peek()
        if head["kind"] == "rparen":
            raise ParseError(
                "E101", "empty list '()' is not a valid form",
                pos=_pos_of(head),
                expected="an op name after '('",
                got="'()'",
                hint="write (opname arg ...) e.g. (add 1 2)",
            )
        if head["kind"] == "lparen":
            # Computed function position: ((fn (x) x) 5), ((get d "f") x).
            fn_expr = self.parse_expr()
            args = self._parse_call_args()
            self._expect_rparen()
            return ("call", fn_expr, *args)
        if head["kind"] != "symbol":
            raise ParseError(
                "E101",
                f"list head must be an op name, got {_describe_token(head)}",
                pos=_pos_of(head),
                expected="an op name (a symbol)",
                got=_describe_token(head),
                hint="Terse is prefix: write (add 1 2), not (1 + 2)",
            )
        op = head["value"]
        self.advance()
        if op not in ARITY:
            # Not a known op: head-position function call.
            # (fib 20) parses as ("call", ("var", "fib"), 20) — the Lisp way.
            # A typo like (ad 1 2) becomes a call to an unbound variable;
            # the interpreter's E200 carries the "did you mean 'add'?" hint,
            # so the repair loop still gets its signal (at runtime, not parse).
            args = self._parse_call_args()
            self._expect_rparen()
            return ("call", ("var", op), *args)
        if op == "fn":
            return self.parse_fn(op, head)
        if op == "let":
            return self.parse_let(op, head)
        if op == "for":
            return self.parse_for(op, head)
        if op == "def":
            return self.parse_def(op, head)
        if op == "fold":
            return self.parse_fold(op, head)
        args = []
        while True:
            tok = self.peek()
            if tok["kind"] == "rparen":
                break
            if tok["kind"] == "eof":
                raise ParseError(
                    "E100", f"unclosed '(' for op '{op}'",
                    op=op,
                    pos=_pos_of(tok),
                    expected=f"')' to close '{op}'",
                    got="end of input",
                    hint=f"add ')' to close the ({op} ...) form",
                )
            args.append(self.parse_expr())
        self.advance()  # consume ')'
        self.check_arity(op, head, args)
        return (op, *args)

    def _parse_call_args(self) -> list:
        """Parse argument expressions up to the closing ')' (call forms)."""
        args = []
        while True:
            tok = self.peek()
            if tok["kind"] == "rparen":
                break
            if tok["kind"] == "eof":
                raise ParseError(
                    "E100", "unclosed '(' in call form",
                    pos=_pos_of(tok),
                    expected="')' to close the call",
                    got="end of input",
                    hint="add ')' to close the call form",
                )
            args.append(self.parse_expr())
        return args

    def _expect_rparen(self) -> None:
        tok = self.peek()
        if tok["kind"] != "rparen":
            # Not reachable via _parse_call_args (it stops at rparen/eof and
            # raises on eof), but kept for symmetry/safety.
            raise ParseError(
                "E101", "expected ')'",
                pos=_pos_of(tok),
                expected="')'",
                got=_describe_token(tok),
                hint="add ')' to close the form",
            )
        self.advance()  # consume ')'

    # -- special forms with binding positions -----------------------------
    def _parse_binding_name(self, op: str, what: str) -> str:
        """Parse one binding name: a bare symbol token -> plain string."""
        tok = self.peek()
        if tok["kind"] != "symbol":
            raise ParseError(
                "E105",
                f"{what} of '{op}' must be a symbol, "
                f"got {_describe_token(tok)}",
                op=op,
                pos=_pos_of(tok),
                expected="a symbol (a name)",
                got=_describe_token(tok),
                hint=f"write a plain name, e.g. ({op} x ...) not "
                     f"({op} {_describe_token(tok)} ...)",
            )
        self.advance()
        return tok["value"]

    def _expect_close(self, op: str, head: dict) -> None:
        tok = self.peek()
        if tok["kind"] != "rparen":
            if tok["kind"] == "eof":
                raise ParseError(
                    "E100", f"unclosed '(' for op '{op}'",
                    op=op, pos=_pos_of(tok),
                    expected=f"')' to close '{op}'",
                    got="end of input",
                    hint=f"add ')' to close the ({op} ...) form",
                )
            # Extra arguments beyond the fixed shape -> arity mismatch.
            self.check_arity(op, head, ["<extra>"] * 99)  # always raises
        self.advance()

    def _parse_operand(self, op: str, what: str, seen: int):
        """Parse one expression operand; a premature ')' means a missing arg."""
        tok = self.peek()
        if tok["kind"] in ("rparen", "eof"):
            raise ParseError(
                "E103", f"op '{op}' is missing its {what}",
                op=op,
                pos=_pos_of(tok),
                expected=_describe_arity(op),
                got=f"'{op}' form ends after {seen} argument(s)",
                hint=f"add the missing {what} to the ({op} ...) form",
            )
        return self.parse_expr()

    def parse_let(self, op: str, head: dict) -> tuple:
        name = self._parse_binding_name(op, "binding name")
        value = self._parse_operand(op, "value", 1)
        body = self._parse_operand(op, "body", 2)
        self._expect_close(op, head)  # enforces arity 3
        return ("let", name, value, body)

    def parse_for(self, op: str, head: dict) -> tuple:
        name = self._parse_binding_name(op, "loop variable")
        iterable = self._parse_operand(op, "iterable", 1)
        body = self._parse_operand(op, "body", 2)
        self._expect_close(op, head)  # enforces arity 3
        return ("for", name, iterable, body)

    def parse_fn(self, op: str, head: dict) -> tuple:
        # Params are a raw list of symbols: (fn (a b) body).
        tok = self.peek()
        if tok["kind"] != "lparen":
            raise ParseError(
                "E105",
                f"params of 'fn' must be a list of symbols, "
                f"got {_describe_token(tok)}",
                op=op, pos=_pos_of(tok),
                expected="a parameter list like (a b)",
                got=_describe_token(tok),
                hint="write (fn (a b) body)",
            )
        self.advance()  # consume '(' of param list
        params: list[str] = []
        while True:
            ptok = self.peek()
            if ptok["kind"] == "rparen":
                break
            if ptok["kind"] == "eof":
                raise ParseError(
                    "E100", "unclosed parameter list of 'fn'",
                    op=op, pos=_pos_of(ptok),
                    expected="')' to close the parameter list",
                    got="end of input",
                    hint="add ')' to close the parameter list",
                )
            if ptok["kind"] != "symbol":
                raise ParseError(
                    "E105",
                    f"fn parameter must be a symbol, "
                    f"got {_describe_token(ptok)}",
                    op=op, pos=_pos_of(ptok),
                    expected="a symbol (a parameter name)",
                    got=_describe_token(ptok),
                    hint="write (fn (a b) body) with plain names only",
                )
            params.append(ptok["value"])
            self.advance()
        self.advance()  # consume ')' of param list
        body = self._parse_operand(op, "body", 1)
        self._expect_close(op, head)  # enforces arity 2
        return ("fn", params, body)

    def parse_def(self, op: str, head: dict) -> tuple:
        # (def name (params) body): named function definition.
        name = self._parse_binding_name(op, "function name")
        tok = self.peek()
        if tok["kind"] != "lparen":
            raise ParseError(
                "E105",
                f"params of 'def' must be a list of symbols, "
                f"got {_describe_token(tok)}",
                op=op, pos=_pos_of(tok),
                expected="a parameter list like (a b)",
                got=_describe_token(tok),
                hint="write (def name (a b) body)",
            )
        self.advance()  # consume '(' of param list
        params: list[str] = []
        while True:
            ptok = self.peek()
            if ptok["kind"] == "rparen":
                break
            if ptok["kind"] == "eof":
                raise ParseError(
                    "E100", "unclosed parameter list of 'def'",
                    op=op, pos=_pos_of(ptok),
                    expected="')' to close the parameter list",
                    got="end of input",
                    hint="add ')' to close the parameter list",
                )
            if ptok["kind"] != "symbol":
                raise ParseError(
                    "E105",
                    f"def parameter must be a symbol, "
                    f"got {_describe_token(ptok)}",
                    op=op, pos=_pos_of(ptok),
                    expected="a symbol (a parameter name)",
                    got=_describe_token(ptok),
                    hint="write (def name (a b) body) with plain names only",
                )
            params.append(ptok["value"])
            self.advance()
        self.advance()  # consume ')' of param list
        body = self._parse_operand(op, "body", 2)
        self._expect_close(op, head)  # enforces arity 3
        return ("def", name, params, body)

    def parse_fold(self, op: str, head: dict) -> tuple:
        # (fold (acc x) init coll body): loop with accumulator.
        tok = self.peek()
        if tok["kind"] != "lparen":
            raise ParseError(
                "E105",
                f"bindings of 'fold' must be a list of two symbols, "
                f"got {_describe_token(tok)}",
                op=op, pos=_pos_of(tok),
                expected="a binding list like (acc x)",
                got=_describe_token(tok),
                hint="write (fold (acc x) init coll body)",
            )
        self.advance()  # consume '(' of binding list
        names: list[str] = []
        while True:
            ntok = self.peek()
            if ntok["kind"] == "rparen":
                break
            if ntok["kind"] == "eof":
                raise ParseError(
                    "E100", "unclosed binding list of 'fold'",
                    op=op, pos=_pos_of(ntok),
                    expected="')' to close the binding list",
                    got="end of input",
                    hint="add ')' to close the binding list",
                )
            if ntok["kind"] != "symbol":
                raise ParseError(
                    "E105",
                    f"fold binding must be a symbol, "
                    f"got {_describe_token(ntok)}",
                    op=op, pos=_pos_of(ntok),
                    expected="a symbol (accumulator and element names)",
                    got=_describe_token(ntok),
                    hint="write (fold (acc x) init coll body)",
                )
            names.append(ntok["value"])
            self.advance()
        self.advance()  # consume ')' of binding list
        if len(names) != 2:
            raise ParseError(
                "E103", f"op 'fold' needs exactly 2 bindings (acc x)",
                op=op, pos=_pos_of(head),
                expected="a binding list like (acc x)",
                got=f"{len(names)} name(s)",
                hint="write (fold (acc x) init coll body)",
            )
        init = self._parse_operand(op, "initial accumulator", 1)
        coll = self._parse_operand(op, "collection", 2)
        body = self._parse_operand(op, "body", 3)
        self._expect_close(op, head)  # enforces arity 4
        return ("fold", names, init, coll, body)

    # -- arity ------------------------------------------------------------
    def check_arity(self, op: str, head: dict, args: list) -> None:
        spec = ARITY[op]
        n = len(args)
        if spec == "even":
            ok = (n % 2 == 0)
        else:
            lo, hi = spec
            ok = n >= lo and (hi is None or n <= hi)
        if not ok:
            raise ParseError(
                "E103", f"op '{op}' got {n} argument(s)",
                op=op,
                pos=_pos_of(head),
                expected=_describe_arity(op),
                got=f"{n} argument(s)",
                hint=f"({op} ...) takes {_describe_arity(op)}; "
                     f"adjust the argument count",
            )


# --- Public API ---------------------------------------------------------------


def parse(src: str) -> tuple:
    """Parse Terse source into an AST tuple. Raises ParseError on any failure."""
    parser = _Parser(tokenize(src))
    return parser.parse_program()


def try_parse(src: str) -> tuple[tuple | None, list[dict]]:
    """Parse, returning (ast, []) on success or (None, [error_dict]) on failure.

    Never raises ParseError; only bugs outside the error protocol propagate.
    """
    try:
        return parse(src), []
    except ParseError as e:
        return None, [e.to_dict()]
