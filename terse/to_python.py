"""Terse -> Python transpiler (v0.1).

Transpiles a Terse AST (tuples per the shared AST contract) into readable,
executable Python source.

Usage:
    from terse.parser import parse
    from terse.to_python import to_python

    src = to_python(parse('(print "hi")'))
    ns: dict = {}
    exec(src, ns)            # runs the program
    ns["_terse_result"]      # value of the final expression

Codegen model
-------------
``gen(node) -> (stmts, expr)``: every node compiles to a list of preceding
Python statements plus one value expression. Fresh temporaries ``_t0``,
``_t1``, ... hold intermediate values; each ``fn`` becomes
``def _fn0(...): ...`` whose value is its name.

Variable renaming
-----------------
Every bound name (``let`` / ``for`` / ``fn`` params) is renamed to a unique
Python identifier (``x`` -> ``x_1``) in a scoped renaming environment, so
shadowing is always correct. A Terse name that is a Python keyword gets a
trailing underscore before uniquification (``class`` -> ``class__1``), and
names that are not valid identifiers are sanitized. Bound names are never
reassigned after binding, so closures capture the right cell.

``return``
---------
``return`` is a *statement* and is only valid in statement position inside
an ``fn`` body: the body's top-level ``seq`` (a non-``seq`` body counts as
an implicit one-element ``seq``), or nested ``while`` / ``for`` /
statement-``if`` bodies within it. It is *not* valid directly in a ``let``
body, in an ``fn``/``let`` value, or in any other value position -- those
raise ``TranspileError`` (E300), as does ``return`` outside an ``fn``. The
value of an ``fn`` body's last expression is returned implicitly, so
``return`` is only needed for early exits.

``let`` and recursion
---------------------
``let`` is letrec for functions, mirroring the interpreter: when the bound
value is an ``fn``, the name is bound *before* the value is compiled, so
the function body can call itself by name::

    (let fib (fn (n)
               (if (lt n 2) n
                   (add (call fib (sub n 1))
                        (call fib (sub n 2)))))
      (print (call fib 10)))

For non-function values, ``let`` is a standard (non-recursive) binding:
a value that references the name being bound is an unbound-variable
error (``TranspileError`` E300 at transpile time).

Short-circuit logic
-------------------
``and`` / ``or`` return the deciding operand (Python-like) *and*
short-circuit side effects: operands are guarded by nested ifs rather
than emitted as one flat ``a and b`` expression, so
``(and false (print "x"))`` prints nothing -- exactly like the interpreter.

``if``
------
In value position ``if`` compiles to a temp-var assignment form; in
statement position it compiles to a native ``if``/``else`` statement (so
``return`` works inside its branches). A two-argument ``if`` yields ``None``
when the condition is falsy.

Error mapping (terse runtime failure -> Python exception)
--------------------------------------------------------
- ``get`` on a missing dict key -> ``KeyError``; on a bad list index ->
  ``IndexError``.
- ``put`` / ``push`` given a non-list/non-dict -> ``TypeError``.
- ``div`` / ``mod`` by zero -> ``ZeroDivisionError``.
- ``int`` / ``float`` of a bad string -> ``ValueError``.
- ``assert`` failure -> ``AssertionError``.
- Unbound variables and malformed ASTs are caught at transpile time
  (``TranspileError`` E300), never at runtime.

Argument-order conventions (chosen where the contract is silent)
---------------------------------------------------------------
- ``(join sep coll)`` -> ``sep.join(coll)`` (separator first, mirroring the
  subject-first order of ``(split s sep)`` -> ``s.split(sep)``).
- ``(put coll key val)``, ``(get coll key)``, ``(push lst v)``.
- ``(range a)`` / ``(range a b)`` / ``(range a b c)`` -> ``list(range(...))``.
- ``(dict k1 v1 k2 v2 ...)`` preserves key order; ``put`` on an existing
  key keeps its position (like Python dicts).
"""

from __future__ import annotations

import keyword
from contextlib import contextmanager

from .errors import TranspileError

__all__ = ["to_python", "TranspileError"]


# --- Runtime prelude ---------------------------------------------------------
# Helpers mirroring interpreter semantics. They are emitted verbatim at the
# top of every generated module.

_PRELUDE = '''\
def _terse_get(c, k):
    """terse `get`: index a list / look up a dict key.

    Missing dict key -> KeyError, bad list index -> IndexError
    (mirrors the interpreter's key-or-index error).
    """
    return c[k]


def _terse_put(c, k, v):
    """terse `put`: return a NEW collection with k set to v.

    The input collection is never mutated. Anything else -> TypeError.
    """
    if isinstance(c, dict):
        d = dict(c)
        d[k] = v
        return d
    if isinstance(c, list):
        out = list(c)
        out[k] = v
        return out
    raise TypeError(
        "terse put: expected list or dict, got %s" % type(c).__name__)


def _terse_push(lst, v):
    """terse `push`: return a NEW list with v appended.

    The input list is never mutated. Non-list input -> TypeError.
    """
    if not isinstance(lst, list):
        raise TypeError(
            "terse push: expected list, got %s" % type(lst).__name__)
    return lst + [v]


def _terse_print(*args):
    """terse `print`: emit " ".join(str(a) for a in args) + newline.

    Returns None, like the interpreter.
    """
    print(*args)
'''


# --- Small utilities ---------------------------------------------------------

def _indent(lines: list[str], level: int = 1) -> list[str]:
    pad = "    " * level
    return [pad + ln for ln in lines]


def _sanitize(name: str) -> str:
    """Make a Terse name a safe Python identifier stem."""
    s = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(name))
    if not s or s[0].isdigit():
        s = "_" + s
    if keyword.iskeyword(s):
        s += "_"
    return s


_BINOP_SYMBOLS = {
    "add": "+", "sub": "-", "mul": "*",
    "div": "/", "mod": "%", "pow": "**",
    "eq": "==", "ne": "!=", "lt": "<",
    "le": "<=", "gt": ">", "ge": ">=",
}

# Ops that may perform side effects, so a bare expression-statement must be
# kept (not discarded) when they appear in statement position.
_SIDE_EFFECT_OPS = frozenset({"print", "call"})


class _Compiler:
    """Single-use recursive transpiler. See module docstring for the model."""

    def __init__(self) -> None:
        self._tmp = 0
        self._fns = 0
        self._vars = 0
        self._env: dict[str, str] = {}
        self._in_fn = 0
        # True only in statement positions where `return` is legal: the fn
        # body's top-level seq, or a nested while/for/statement-if body.
        # `let` bodies (and every value position) reset it to False.
        self._return_ok = False

    # -- names ----------------------------------------------------------

    def _temp(self) -> str:
        n = self._tmp
        self._tmp += 1
        return f"_t{n}"

    def _fnname(self) -> str:
        n = self._fns
        self._fns += 1
        return f"_fn{n}"

    def _bind(self, name: str) -> str:
        self._vars += 1
        py = f"{_sanitize(name)}_{self._vars}"
        self._env[name] = py
        return py

    @contextmanager
    def _scope(self):
        saved = dict(self._env)
        try:
            yield
        finally:
            self._env = saved

    @contextmanager
    def _return_scope(self, ok: bool):
        saved = self._return_ok
        self._return_ok = ok
        try:
            yield
        finally:
            self._return_ok = saved

    def _check_return(self, node: tuple) -> None:
        """Raise E300 unless `return` is legal at this statement position."""
        if not self._in_fn:
            raise TranspileError(
                "E300",
                "return outside of fn",
                op="return",
                hint="return is only valid as a statement inside an fn "
                     "body (its top-level seq, or a nested while/for/if)",
            )
        if not self._return_ok:
            raise TranspileError(
                "E300",
                "return is not valid in this position",
                op="return",
                hint="return is only valid directly in the fn body's "
                     "top-level seq, or in a nested while/for/if body -- "
                     "not inside a let value/body or any other expression",
            )

    # -- shared checks --------------------------------------------------

    @staticmethod
    def _arity(node: tuple, lo: int, hi: int | None = None) -> None:
        n = len(node) - 1
        ok = n >= lo and (hi is None or n <= hi)
        if not ok:
            if hi is None:
                want = f"{lo} or more"
            elif lo == hi:
                want = f"exactly {lo}"
            else:
                want = f"{lo}-{hi}"
            raise TranspileError(
                "E300",
                f"op {node[0]!r} takes {want} argument(s), got {n}",
                op=node[0],
                hint="check the AST contract for this op's arity",
            )

    def _lit(self, value) -> str:
        if value is None:
            return "None"
        if value is True:
            return "True"
        if value is False:
            return "False"
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, (int, float)):
            return repr(value)
        raise TranspileError(
            "E300",
            f"unsupported literal {value!r}",
            hint="literals must be int, float, str, bool, or None",
        )

    # -- value position: gen(node) -> (stmts, expr) ---------------------

    def gen(self, node) -> tuple[list[str], str]:
        """Compile ``node`` in value position."""
        if isinstance(node, tuple):
            if not node or not isinstance(node[0], str):
                raise TranspileError(
                    "E300",
                    f"malformed AST node: {node!r}",
                    hint="nodes are tuples like ('add', 1, 2)",
                )
            op = node[0]
            if op == "var":
                return self._gen_var(node)
            if op in _BINOP_SYMBOLS:
                return self._gen_binop(node, _BINOP_SYMBOLS[op])
            if op == "neg":
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"-({e})"
            if op == "dec":
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"({e}) - 1"
            if op == "divis":
                # Fused (eq (mod x n) 0): True iff x is divisible by n.
                self._arity(node, 2, 2)
                sx, ex = self.gen(node[1])
                sn, en = self.gen(node[2])
                return sx + sn, f"(({ex}) % ({en}) == 0)"
            if op in ("and", "or"):
                return self._gen_andor(node)
            if op == "not":
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"not ({e})"
            if op == "if":
                return self._gen_if(node)
            if op == "while":
                return self._stmt_while(node), "None"
            if op == "for":
                return self._stmt_for(node), "None"
            if op == "seq":
                return self._gen_seq(node)
            if op == "let":
                return self._gen_let(node)
            if op == "fn":
                return self._gen_fn(node)
            if op == "def":
                raise TranspileError(
                    "E300",
                    "def is statement-only; use it at the top level, not as a value",
                    op="def",
                    hint="write (def name (params) body) as a top-level form",
                )
            if op == "fold":
                return self._gen_fold(node)
            if op == "inc":
                return self._gen_inc(node)
            if op == "map":
                return self._gen_map(node)
            if op == "filter":
                return self._gen_filter(node)
            if op == "rev":
                return self._gen_rev(node)
            if op == "keep":
                return self._gen_keep(node)
            if op == "sum":
                # Fused (fold (acc x) 0 coll (add acc x)).
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"sum({e})"
            if op == "call":
                return self._gen_call(node)
            if op == "return":
                if not self._in_fn:
                    raise TranspileError(
                        "E300",
                        "return outside of fn",
                        op="return",
                        hint="return is only valid as a statement inside an "
                             "fn body (its top-level seq, or a nested "
                             "while/for/if)",
                    )
                raise TranspileError(
                    "E300",
                    "return used in expression (value) position",
                    op="return",
                    hint="return is a statement: put it directly in the fn "
                         "body's seq, or in a nested while/for/if body -- "
                         "not inside another expression",
                )
            if op == "assert":
                s, e, line = self._gen_assert(node)
                return s + [line], e
            if op == "list":
                return self._gen_list(node)
            if op == "dict":
                return self._gen_dict(node)
            if op == "get":
                self._arity(node, 2, 2)
                sc, ec = self.gen(node[1])
                sk, ek = self.gen(node[2])
                return sc + sk, f"_terse_get({ec}, {ek})"
            if op == "put":
                self._arity(node, 3, 3)
                sc, ec = self.gen(node[1])
                sk, ek = self.gen(node[2])
                sv, ev = self.gen(node[3])
                return sc + sk + sv, f"_terse_put({ec}, {ek}, {ev})"
            if op == "len":
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"len({e})"
            if op == "range":
                self._arity(node, 1, 3)
                parts: list[str] = []
                stmts: list[str] = []
                for arg in node[1:]:
                    sa, ea = self.gen(arg)
                    stmts += sa
                    parts.append(ea)
                return stmts, f"list(range({', '.join(parts)}))"
            if op == "push":
                self._arity(node, 2, 2)
                sl, el = self.gen(node[1])
                sv, ev = self.gen(node[2])
                return sl + sv, f"_terse_push({el}, {ev})"
            if op == "cat":
                stmts = []
                parts = []
                for arg in node[1:]:
                    sa, ea = self.gen(arg)
                    stmts += sa
                    parts.append(f"str({ea})")
                return stmts, '"".join([' + ", ".join(parts) + "])"
            if op == "fmt":
                # (fmt tmpl a b) -> tmpl.format(a, b); Python str.format
                # shares the {} placeholder syntax. Missing-arg errors
                # surface as IndexError in Python vs E207 in Terse.
                if len(node) < 2:
                    self._arity(node, 1, None)
                st, et = self.gen(node[1])
                stmts, parts = st, []
                for arg in node[2:]:
                    sa, ea = self.gen(arg)
                    stmts += sa
                    parts.append(ea)
                return stmts, f"({et}).format({', '.join(parts)})"
            if op in ("str", "int", "float"):
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"{op}({e})"
            if op in ("upper", "lower"):
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s, f"({e}).{op}()"
            if op == "split":
                self._arity(node, 2, 2)
                ss, es = self.gen(node[1])
                sd, ed = self.gen(node[2])
                return ss + sd, f"({es}).split({ed})"
            if op == "join":
                self._arity(node, 2, 2)
                ss, es = self.gen(node[1])
                sl, el = self.gen(node[2])
                return ss + sl, f"({es}).join({el})"
            if op == "print":
                stmts = []
                parts = []
                for arg in node[1:]:
                    sa, ea = self.gen(arg)
                    stmts += sa
                    parts.append(ea)
                return stmts, f"_terse_print({', '.join(parts)})"
            raise TranspileError(
                "E300",
                f"unsupported op {op!r}",
                op=op,
                hint="check the AST contract for the v0.1 op list",
            )
        return [], self._lit(node)

    def _gen_var(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 1, 1)
        name = node[1]
        if name not in self._env:
            raise TranspileError(
                "E300",
                f"unbound variable {name!r}",
                op="var",
                hint="bind it with let/for/fn before use",
            )
        return [], self._env[name]

    def _gen_binop(self, node: tuple, sym: str) -> tuple[list[str], str]:
        self._arity(node, 2, 2)
        sa, ea = self.gen(node[1])
        sb, eb = self.gen(node[2])
        return sa + sb, f"({ea}) {sym} ({eb})"

    def _gen_andor(self, node: tuple) -> tuple[list[str], str]:
        """Short-circuit and/or returning the deciding operand.

        Operands are guarded by nested ifs so side effects in a skipped
        operand never run (a flat ``a and b`` expression would run every
        operand's setup statements eagerly).
        """
        op = node[0]
        self._arity(node, 2, None)
        args = node[1:]
        s0, e0 = self.gen(args[0])
        t = self._temp()
        # Build the guarded tail inside-out: each later operand only runs
        # when the accumulator still "wants" it.
        tail: list[str] = []
        for arg in reversed(args[1:]):
            sa, ea = self.gen(arg)
            cond = f"{t}" if op == "and" else f"not ({t})"
            step = sa + [f"{t} = {ea}"]
            tail = step + ([f"if {cond}:"] + _indent(tail) if tail else [])
        lines = s0 + [f"{t} = {e0}"]
        if tail:
            cond = f"{t}" if op == "and" else f"not ({t})"
            lines += [f"if {cond}:"] + _indent(tail)
        return lines, t

    def _gen_if(self, node: tuple) -> tuple[list[str], str]:
        """Value-position n-ary if: temp-var assignment form."""
        args = node[1:]
        if len(args) < 2:
            raise TranspileError(
                "E300", f"if needs 2 or more arguments, got {len(args)}",
                op="if",
            )
        t = self._temp()
        lines: list[str] = []
        i, n = 0, len(args)
        first = True
        while i + 1 < n:
            sc, ec = self.gen(args[i])
            st, et = self.gen(args[i + 1])
            kw = "if" if first else "elif"
            lines += sc + [f"{kw} {ec}:"] + _indent(st + [f"{t} = {et}"])
            first = False
            i += 2
        if i < n:  # trailing else
            se, ee = self.gen(args[i])
            lines += se + ["else:"] + _indent([f"{t} = {ee}"])
        else:
            lines += ["else:"] + _indent([f"{t} = None"])
        return lines, t

    def _gen_seq(self, node: tuple) -> tuple[list[str], str]:
        items = node[1:]
        if not items:
            return [], "None"
        stmts: list[str] = []
        for it in items[:-1]:
            stmts += self.gen_stmt(it)
        s, e = self.gen(items[-1])
        return stmts + s, e

    def _gen_let(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 3, 3)
        _, name, value, body = node
        if not isinstance(name, str):
            raise TranspileError(
                "E300",
                f"let binding name must be a string, got {name!r}",
                op="let",
                hint="write ('let', 'x', value, body)",
            )
        # letrec: when the value is an `fn`, bind the name BEFORE compiling
        # the value so the function body can reference its own name
        # (mirrors the interpreter, which rebinds closures to their
        # let's scope). Any other self-reference in the value stays an
        # unbound-variable error, as before.
        if isinstance(value, tuple) and value and value[0] == "fn":
            with self._scope():
                py = self._bind(name)
                sv, ev = self.gen(value)
                sb, eb = self.gen(body)
            return sv + [f"{py} = {ev}"] + sb, eb
        # Standard let: value is compiled in the OUTER env (the name is
        # not yet bound), then bound for the body.
        sv, ev = self.gen(value)
        with self._scope():
            py = self._bind(name)
            sb, eb = self.gen(body)
        return sv + [f"{py} = {ev}"] + sb, eb

    def _gen_fn(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 2, 2)
        _, params, body = node
        if (
            not isinstance(params, (list, tuple))
            or not all(isinstance(p, str) for p in params)
        ):
            raise TranspileError(
                "E300",
                f"fn params must be a list of strings, got {params!r}",
                op="fn",
                hint="write ('fn', ['a', 'b'], body)",
            )
        fname = self._fnname()
        with self._scope():
            pynames = [self._bind(p) for p in params]
            self._in_fn += 1
            try:
                blines = self._fn_body(body)
            finally:
                self._in_fn -= 1
        code = [f"def {fname}({', '.join(pynames)}):"]
        code += _indent(blines) if blines else ["    pass"]
        return code, fname

    def _stmt_def(self, node: tuple) -> list[str]:
        """Statement-position def: (def name (params) body) -> def name(...)."""
        self._arity(node, 3, 3)
        _, name, params, body = node
        if not isinstance(name, str):
            raise TranspileError(
                "E300", f"def name must be a string, got {name!r}", op="def",
            )
        if not isinstance(params, (list, tuple)) or not all(
            isinstance(p, str) for p in params
        ):
            raise TranspileError(
                "E300", f"def params must be a list of strings, got {params!r}",
                op="def",
            )
        # Bind the name in the enclosing env BEFORE compiling the body, so
        # the body can reference its own name (mirrors the interpreter's
        # letrec). Params are bound in a child scope.
        py = self._bind(name)
        with self._scope():
            pynames = [self._bind(p) for p in params]
            self._in_fn += 1
            try:
                blines = self._fn_body(body)
            finally:
                self._in_fn -= 1
        code = [f"def {py}({', '.join(pynames)}):"]
        code += _indent(blines) if blines else ["    pass"]
        return code

    def _gen_fold(self, node: tuple) -> tuple[list[str], str]:
        """Value-position fold: (fold (acc x) init coll body)."""
        self._arity(node, 4, 4)
        _, names, init, coll, body = node
        if (
            not isinstance(names, (list, tuple))
            or len(names) != 2
            or not all(isinstance(nm, str) for nm in names)
        ):
            raise TranspileError(
                "E300",
                f"fold bindings must be [acc, x], got {names!r}",
                op="fold",
            )
        si, ei = self.gen(init)
        sc, ec = self.gen(coll)
        acc = self._temp()
        elem = self._temp()
        with self._scope():
            self._env[names[0]] = acc
            self._env[names[1]] = elem
            sb, eb = self.gen(body)
        lines = si + sc + [f"{acc} = {ei}", f"for {elem} in {ec}:"]
        lines += _indent(sb + [f"{acc} = {eb}"])
        return lines, acc

    def _gen_inc(self, node: tuple) -> tuple[list[str], str]:
        """Value-position inc: (inc coll key [by]) -> new dict."""
        self._arity(node, 2, 3)
        s0, e0 = self.gen(node[1])
        s1, e1 = self.gen(node[2])
        if len(node) > 3:
            s2, e2 = self.gen(node[3])
        else:
            s2, e2 = [], "1"
        t = self._temp()
        kt = self._temp()
        lines = (
            s0 + s1 + s2
            + [f"{kt} = {e1}", f"{t} = dict({e0})",
               f"{t}[{kt}] = {t}.get({kt}, 0) + ({e2})"]
        )
        return lines, t

    def _fn_body(self, body) -> list[str]:
        """Statement list for an fn body, ending with an implicit return."""
        if isinstance(body, tuple) and body and body[0] == "seq":
            items = list(body[1:])
        else:
            items = [body]
        if not items:
            return ["return None"]
        lines: list[str] = []
        with self._return_scope(True):
            for it in items[:-1]:
                lines += self.gen_stmt(it)
            last = items[-1]
            if isinstance(last, tuple) and last and last[0] == "return":
                lines += self.gen_stmt(last)
            else:
                s, e = self.gen(last)
                lines += s + [f"return {e}"]
        return lines

    def _stmt_let(self, node: tuple) -> list[str]:
        """Statement-position let: value, bind, then body as statements.

        A `return` directly in the body is rejected here (the body's value
        is discarded, so returning from it is not in the allowed set);
        `return` inside a nested while/for/if within the body still works.
        """
        self._arity(node, 3, 3)
        _, name, value, body = node
        if not isinstance(name, str):
            raise TranspileError(
                "E300",
                f"let binding name must be a string, got {name!r}",
                op="let",
                hint="write ('let', 'x', value, body)",
            )
        # Standard let (mirroring the interpreter): the value is compiled
        # in the outer env, then the name is bound for the body.
        sv, ev = self.gen(value)
        with self._scope():
            py = self._bind(name)
            with self._return_scope(False):
                blk = self.gen_block(body)
        return sv + [f"{py} = {ev}"] + blk

    def _gen_call(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 1, None)
        sf, ef = self.gen(node[1])
        stmts = list(sf)
        exprs = []
        for arg in node[2:]:
            sa, ea = self.gen(arg)
            stmts += sa
            exprs.append(ea)
        return stmts, f"{ef}({', '.join(exprs)})"

    def _gen_map(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 2, 2)
        sf, ef = self.gen(node[1])
        sc, ec = self.gen(node[2])
        x = self._temp()
        return sf + sc, f"[{ef}({x}) for {x} in {ec}]"

    def _gen_filter(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 2, 2)
        sf, ef = self.gen(node[1])
        sc, ec = self.gen(node[2])
        x = self._temp()
        return sf + sc, f"[{x} for {x} in {ec} if {ef}({x})]"

    def _gen_rev(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 1, 1)
        s, e = self.gen(node[1])
        return s, f"({e})[::-1]"

    def _gen_keep(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 2, 2)
        sf, ef = self.gen(node[1])
        sc, ec = self.gen(node[2])
        x = self._temp()
        # [y for y in (f(x) for x in coll) if y is not None]
        inner = self._temp()
        return (
            sf + sc,
            f"[{inner} for {inner} in ({ef}({x}) for {x} in {ec}) "
            f"if {inner} is not None]",
        )

    def _gen_assert(self, node: tuple) -> tuple[list[str], str]:
        self._arity(node, 1, 2)
        sc, ec = self.gen(node[1])
        if len(node) == 3:
            sm, em = self.gen(node[2])
            return sc + sm, "None", f"assert {ec}, {em}"
        return sc, "None", f"assert {ec}"

    def _gen_list(self, node: tuple) -> tuple[list[str], str]:
        stmts: list[str] = []
        parts: list[str] = []
        for arg in node[1:]:
            sa, ea = self.gen(arg)
            stmts += sa
            parts.append(ea)
        return stmts, "[" + ", ".join(parts) + "]"

    def _gen_dict(self, node: tuple) -> tuple[list[str], str]:
        items = node[1:]
        if len(items) % 2:
            raise TranspileError(
                "E300",
                f"dict needs an even argument count (k/v pairs), got {len(items)}",
                op="dict",
                hint="write ('dict', k1, v1, k2, v2, ...)",
            )
        stmts: list[str] = []
        pairs: list[str] = []
        for k, v in zip(items[0::2], items[1::2]):
            sk, ek = self.gen(k)
            sv, ev = self.gen(v)
            stmts += sk + sv
            pairs.append(f"{ek}: {ev}")
        return stmts, "{" + ", ".join(pairs) + "}"

    # -- statement position: gen_stmt(node) -> [lines] ------------------

    def gen_stmt(self, node) -> list[str]:
        """Compile ``node`` in statement position (value discarded)."""
        if isinstance(node, tuple) and node:
            op = node[0]
            if op == "while":
                return self._stmt_while(node)
            if op == "for":
                return self._stmt_for(node)
            if op == "if":
                return self._stmt_if(node)
            if op == "seq":
                lines: list[str] = []
                for it in node[1:]:
                    lines += self.gen_stmt(it)
                return lines
            if op == "let":
                return self._stmt_let(node)
            if op == "def":
                return self._stmt_def(node)
            if op == "return":
                self._check_return(node)
                self._arity(node, 1, 1)
                s, e = self.gen(node[1])
                return s + [f"return {e}"]
            if op == "assert":
                s, _, line = self._gen_assert(node)
                return s + [line]
        s, e = self.gen(node)
        op = node[0] if isinstance(node, tuple) and node else None
        if op in _SIDE_EFFECT_OPS:
            # print / call may have side effects: keep the expression.
            return s + [e]
        return s

    def gen_block(self, node) -> list[str]:
        """Statement-position body (while/for/if branches, loop bodies)."""
        if isinstance(node, tuple) and node and node[0] == "seq":
            lines: list[str] = []
            for it in node[1:]:
                lines += self.gen_stmt(it)
            return lines
        return self.gen_stmt(node)

    def _stmt_while(self, node: tuple) -> list[str]:
        self._arity(node, 2, 2)
        sc, ec = self.gen(node[1])
        with self._return_scope(True):
            blk = self.gen_block(node[2])
        lines = sc + [f"while {ec}:"]
        lines += _indent(blk) if blk else ["    pass"]
        return lines

    def _stmt_for(self, node: tuple) -> list[str]:
        self._arity(node, 3, 3)
        _, name, iterable, body = node
        if not isinstance(name, str):
            raise TranspileError(
                "E300",
                f"for loop variable must be a string, got {name!r}",
                op="for",
                hint="write ('for', 'i', iterable, body)",
            )
        si, ei = self.gen(iterable)
        with self._scope():
            py = self._bind(name)
            with self._return_scope(True):
                blk = self.gen_block(body)
        lines = si + [f"for {py} in {ei}:"]
        lines += _indent(blk) if blk else ["    pass"]
        return lines

    def _stmt_if(self, node: tuple) -> list[str]:
        """Statement-position n-ary if: native if/elif/else (return works in branches)."""
        args = node[1:]
        if len(args) < 2:
            raise TranspileError(
                "E300", f"if needs 2 or more arguments, got {len(args)}",
                op="if",
            )
        lines: list[str] = []
        i, n = 0, len(args)
        first = True
        while i + 1 < n:
            sc, ec = self.gen(args[i])
            kw = "if" if first else "elif"
            lines += sc + [f"{kw} {ec}:"]
            with self._return_scope(True):
                tb = self.gen_block(args[i + 1])
            lines += _indent(tb) if tb else ["    pass"]
            first = False
            i += 2
        if i < n:  # trailing else
            lines += ["else:"]
            with self._return_scope(True):
                eb = self.gen_block(args[i])
            lines += _indent(eb) if eb else ["    pass"]
        return lines


def to_python(ast) -> str:
    """Transpile a Terse AST to executable Python source.

    Emits the runtime prelude, then module-level statements, ending with
    ``_terse_result = <final expression>``. The output is self-checked with
    ``compile()``; a failure raises ``TranspileError`` (E300).
    """
    comp = _Compiler()
    stmts, expr = comp.gen(ast)
    out = ["# Generated by terse.to_python v0.1 -- do not edit by hand.", ""]
    out.append(_PRELUDE.rstrip("\n"))
    out.append("")
    out.extend(stmts)
    out.append(f"_terse_result = {expr}")
    src = "\n".join(out) + "\n"
    try:
        compile(src, "<terse>", "exec")
    except SyntaxError as exc:
        raise TranspileError(
            "E300",
            f"transpiler emitted invalid Python: {exc}",
            hint="this is a transpiler bug -- report the input AST",
        ) from exc
    return src
