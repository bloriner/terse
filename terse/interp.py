"""Terse v0.1 tree-walking interpreter.

Evaluates the Terse AST: nodes are tuples ``("op", arg1, arg2, ...)``,
literals are raw Python values (``int``/``float``/``str``/``bool``/``None``),
and variable references are ``("var", "name")``.

Semantics (v0.1):
  * ``run(ast, gas=100_000)`` -> ``(value, output)``. Every AST node
    evaluation consumes 1 gas; when gas is exhausted ``E205`` is raised.
  * Lexical scoping via chained environments. ``let`` evaluates its value
    in the current env and its body in a child env with the name bound;
    a closure bound by ``let`` is rebound to see its own name (letrec),
    so named recursion works: ``(let fib (fn (n) ... (call fib ...) ...) ...)``.
    ``fn`` captures the defining env in a closure; ``call`` evaluates the
    function expression (must be a closure, else ``E206``), checks arity
    (else ``E207``), binds params in a fresh child of the closure's env,
    and evaluates the body there.
  * ``return`` raises an internal signal caught at the nearest enclosing
    ``call``. A ``return`` with no enclosing call simply yields its value
    from ``run``.
  * ``and``/``or`` take 2+ operands, short-circuit left to right, and
    return the deciding operand's value (Python-like). Truthiness is
    Python-like: ``0``/``""``/``[]``/``{}``/``None``/``False`` are falsy.
  * ``if`` without an else yields ``None`` on a falsy condition.
    ``while``/``for`` yield ``None``. ``seq`` yields its last value
    (``None`` when empty).
  * Collections are functional: ``put``/``push`` return NEW collections;
    the originals are never mutated.
  * ``div`` is true division. ``add`` works on numbers (int+float->float),
    ``str``+``str`` (concat) and ``list``+``list`` (concat); anything else
    is ``E208``.
  * ``print`` appends space-joined ``str(arg)`` + ``"\\n"`` to the output
    buffer and yields ``None``.

Error codes (``terse.errors.TerseRuntimeError``):
  E200 unbound-variable, E201 division-by-zero, E202 key-or-index-error,
  E203 conversion-error, E204 assertion-failed, E205 gas-exhausted,
  E206 not-callable, E207 arity-mismatch, E208 type-error.

SANDBOX: the interpreter implements only the ops above. It imports nothing
but ``terse.errors``, touches no files or network, and never uses Python
``eval``/``exec`` — a Terse program cannot escape the language. (The only
``import`` of anything else is the lazy ``terse.parser`` import inside
``run_source``, which just parses text into the same AST.)
"""

from __future__ import annotations

from typing import Any

from .errors import TerseRuntimeError

__all__ = ["run", "run_source"]


# ---------------------------------------------------------------------------
# Internal machinery: environments, closures, return unwinding
# ---------------------------------------------------------------------------


class _ReturnSignal(Exception):
    """Control-flow signal: ``return`` unwinds to the nearest ``call``.

    Caught only at the ``call`` site (and, as a fallback, in ``run`` for a
    top-level ``return``). Never caught by ``if``/``while``/``for``/``seq``/
    ``let`` bodies, so it always reaches the nearest enclosing call.
    """

    def __init__(self, value: Any):
        super().__init__("terse return")
        self.value = value


class _Closure:
    """A Terse function value: params + body + captured (lexical) env."""

    __slots__ = ("params", "body", "env")

    def __init__(self, params: list[str], body: Any, env: "_Env"):
        self.params = params
        self.body = body
        self.env = env

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<terse function ({', '.join(self.params)})>"


class _Env:
    """Chained lexical environment: a dict plus a link to the parent scope."""

    __slots__ = ("bindings", "parent")

    def __init__(self, parent: "_Env | None" = None):
        self.bindings: dict[str, Any] = {}
        self.parent = parent

    def child(self) -> "_Env":
        return _Env(parent=self)

    def lookup(self, name: str, op: str) -> Any:
        env: _Env | None = self
        while env is not None:
            if name in env.bindings:
                return env.bindings[name]
            env = env.parent
        # A typo'd op name (e.g. (ad 1 2)) now parses as a head-position
        # call, so it surfaces here. Offer the repair loop its hint.
        hint = f"no binding for '{name}' in scope; bind it with let or a function parameter"
        try:
            from terse.parser import OPS  # lazy: avoid circular import
            import difflib

            cands = difflib.get_close_matches(name, OPS, n=3, cutoff=0.6)
            # Prefer prefix matches ('ad' -> 'add', not 'and'), then ratio.
            cands.sort(key=lambda c: (not c.startswith(name),
                                      -difflib.SequenceMatcher(None, name, c).ratio()))
            if cands:
                hint = f"no binding for '{name}' in scope; did you mean '{cands[0]}'?"
        except ImportError:
            pass
        raise TerseRuntimeError(
            "E200",
            f"unbound variable '{name}'",
            op=op,
            pos=None,
            hint=hint,
        )


def _typename(v: Any) -> str:
    """Short, stable type name for error messages (bool checked before int)."""
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    if isinstance(v, _Closure):
        return "function"
    return type(v).__name__


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


class _Interpreter:
    """Single-run evaluator: owns the gas budget and the print buffer."""

    def __init__(self, gas: int):
        self.gas = gas
        self.output: list[str] = []
        # op -> bound handler(op, args, env)
        self._handlers = {
            "var": self._op_var,
            "add": self._op_add,
            "sub": self._op_sub,
            "mul": self._op_mul,
            "div": self._op_div,
            "mod": self._op_mod,
            "pow": self._op_pow,
            "neg": self._op_neg,
            "dec": self._op_dec,
            "eq": self._op_eq,
            "ne": self._op_ne,
            "lt": self._op_lt,
            "le": self._op_le,
            "gt": self._op_gt,
            "ge": self._op_ge,
            "and": self._op_and,
            "or": self._op_or,
            "not": self._op_not,
            "if": self._op_if,
            "while": self._op_while,
            "for": self._op_for,
            "seq": self._op_seq,
            "let": self._op_let,
            "fn": self._op_fn,
            "def": self._op_def,
            "fold": self._op_fold,
            "call": self._op_call,
            "return": self._op_return,
            "assert": self._op_assert,
            "list": self._op_list,
            "dict": self._op_dict,
            "get": self._op_get,
            "put": self._op_put,
            "len": self._op_len,
            "range": self._op_range,
            "push": self._op_push,
            "inc": self._op_inc,
            "map": self._op_map,
            "filter": self._op_filter,
            "rev": self._op_rev,
            "keep": self._op_keep,
            "cat": self._op_cat,
            "str": self._op_str,
            "int": self._op_int,
            "float": self._op_float,
            "upper": self._op_upper,
            "lower": self._op_lower,
            "split": self._op_split,
            "join": self._op_join,
            "print": self._op_print,
        }

    # -- core ------------------------------------------------------------

    def evaluate(self, node: Any, env: _Env) -> Any:
        """Evaluate one AST node. Literals (non-tuples) evaluate to themselves."""
        if self.gas <= 0:
            raise TerseRuntimeError(
                "E205",
                "gas exhausted",
                op=None,
                pos=None,
                hint="increase gas or simplify the program",
            )
        self.gas -= 1
        if not isinstance(node, tuple):
            return node  # literal: int | float | str | bool | None
        if not node or not isinstance(node[0], str):
            raise TerseRuntimeError(
                "E208",
                f"malformed AST node {node!r}: expected (\"op\", ...)",
                op="?",
                pos=None,
                expected='("op", ...)',
                got=_typename(node),
            )
        op, args = node[0], node[1:]
        handler = self._handlers.get(op)
        if handler is None:
            raise TerseRuntimeError(
                "E208",
                f"unknown op '{op}'",
                op=op,
                pos=None,
                hint="check the op name against the v0.1 op list",
            )
        return handler(op, args, env)

    # -- small helpers ---------------------------------------------------

    def _arity(self, op: str, args: tuple, lo: int, hi: int | None = None) -> None:
        """Enforce argument count; violations are E207 (arity-mismatch)."""
        hi = lo if hi is None else hi
        n = len(args)
        if not (lo <= n <= hi):
            want = str(lo) if lo == hi else f"{lo}..{hi}"
            raise TerseRuntimeError(
                "E207",
                f"wrong number of arguments: expected {want}, got {n}",
                op=op,
                pos=None,
                hint=f"'{op}' takes {want} argument(s)",
            )

    def _as_number(self, op: str, v: Any, role: str) -> int | float:
        if not isinstance(v, (int, float)):
            raise TerseRuntimeError(
                "E208",
                f"expected number {role}, got {_typename(v)}",
                op=op,
                pos=None,
                expected="number",
                got=_typename(v),
                hint=f"'{op}' only works on numbers",
            )
        return v

    def _as_str(self, op: str, v: Any, role: str) -> str:
        if not isinstance(v, str):
            raise TerseRuntimeError(
                "E208",
                f"expected string {role}, got {_typename(v)}",
                op=op,
                pos=None,
                expected="str",
                got=_typename(v),
                hint=f"'{op}' only works on strings",
            )
        return v

    def _arith2(self, op: str, args: tuple, env: _Env, func) -> Any:
        """Binary numeric op with ZeroDivisionError mapped to E201."""
        self._arity(op, args, 2)
        a = self._as_number(op, self.evaluate(args[0], env), "as left operand")
        b = self._as_number(op, self.evaluate(args[1], env), "as right operand")
        try:
            return func(a, b)
        except ZeroDivisionError:
            raise TerseRuntimeError(
                "E201",
                "division by zero",
                op=op,
                pos=None,
                hint="check that the divisor cannot be zero",
            ) from None

    def _compare(self, op: str, args: tuple, env: _Env, func, symbol: str) -> bool:
        self._arity(op, args, 2)
        a = self.evaluate(args[0], env)
        b = self.evaluate(args[1], env)
        try:
            return func(a, b)
        except TypeError:
            raise TerseRuntimeError(
                "E208",
                f"cannot compare {_typename(a)} {symbol} {_typename(b)}",
                op=op,
                pos=None,
                expected="comparable operands",
                got=f"{_typename(a)}, {_typename(b)}",
            ) from None

    # -- variables / binding ---------------------------------------------

    def _op_var(self, op, args, env):
        self._arity(op, args, 1)
        name = args[0]
        if not isinstance(name, str):
            raise TerseRuntimeError(
                "E208",
                f"variable name must be a string, got {_typename(name)}",
                op=op,
                pos=None,
                expected="str",
                got=_typename(name),
            )
        return env.lookup(name, op)

    def _op_let(self, op, args, env):
        self._arity(op, args, 3)
        name = args[0]
        if not isinstance(name, str):
            raise TerseRuntimeError(
                "E208",
                f"let binding name must be a string, got {_typename(name)}",
                op=op,
                pos=None,
                expected="str",
                got=_typename(name),
            )
        value = self.evaluate(args[1], env)
        child = env.child()
        child.bindings[name] = value
        # letrec: a closure bound by `let` sees its own name, so named
        # recursion works: (let fib (fn (n) ... (call fib ...) ...) ...)
        if isinstance(value, _Closure):
            value.env = child
        return self.evaluate(args[2], child)

    def _op_def(self, op, args, env):
        # ("def", name, params, body): bind a named (recursive) function in
        # the current environment. Evaluates to null; meant for top-level.
        self._arity(op, args, 3)
        name = args[0]
        if not isinstance(name, str):
            raise TerseRuntimeError(
                "E208",
                f"def function name must be a string, got {_typename(name)}",
                op=op,
                pos=None,
                expected="str",
                got=_typename(name),
            )
        params = args[1]
        if not isinstance(params, list) or not all(
            isinstance(p, str) for p in params
        ):
            raise TerseRuntimeError(
                "E208",
                "def params must be a list of strings",
                op=op,
                pos=None,
                expected="list[str]",
                got=_typename(params),
            )
        closure = _Closure(params, args[2], env)
        env.bindings[name] = closure
        # letrec: the closure sees its own name for recursion.
        closure.env = env
        return None

    def _op_seq(self, op, args, env):
        result = None
        for a in args:
            result = self.evaluate(a, env)
        return result

    # -- functions --------------------------------------------------------

    def _op_fn(self, op, args, env):
        self._arity(op, args, 2)
        params = args[0]
        if not isinstance(params, list) or not all(
            isinstance(p, str) for p in params
        ):
            raise TerseRuntimeError(
                "E208",
                "fn params must be a list of strings",
                op=op,
                pos=None,
                expected="list of str",
                got=_typename(params),
                hint="write params as a raw list, e.g. [\"x\", \"y\"], not as AST",
            )
        return _Closure(list(params), args[1], env)

    def _apply(self, fn, argvals: list, op: str):
        """Apply a closure to evaluated argument values (shared by call/map/filter)."""
        if not isinstance(fn, _Closure):
            raise TerseRuntimeError(
                "E206",
                f"not callable: {_typename(fn)}",
                op=op,
                pos=None,
                expected="function",
                got=_typename(fn),
                hint="call only works on fn closures",
            )
        if len(argvals) != len(fn.params):
            raise TerseRuntimeError(
                "E207",
                f"wrong number of arguments: expected {len(fn.params)}, "
                f"got {len(argvals)}",
                op=op,
                pos=None,
                hint="the argument count must match the fn parameter list",
            )
        callenv = _Env(parent=fn.env)  # lexical: parent is the DEFINING env
        for p, v in zip(fn.params, argvals):
            callenv.bindings[p] = v
        try:
            return self.evaluate(fn.body, callenv)
        except _ReturnSignal as sig:
            return sig.value

    def _op_call(self, op, args, env):
        self._arity(op, args, 1, 255)
        fn = self.evaluate(args[0], env)
        argvals = [self.evaluate(a, env) for a in args[1:]]
        return self._apply(fn, argvals, op)

    def _coerce_listlike(self, op, coll, what: str):
        """Accept a list or string for map/filter/rev; return a Python list."""
        if isinstance(coll, str):
            return list(coll)
        if isinstance(coll, list):
            return coll
        raise TerseRuntimeError(
            "E208",
            f"{what} expects a list or string, got {_typename(coll)}",
            op=op,
            pos=None,
            expected="list or str",
            got=_typename(coll),
        )

    def _op_map(self, op, args, env):
        self._arity(op, args, 2)
        fn = self.evaluate(args[0], env)
        items = self._coerce_listlike(op, self.evaluate(args[1], env), "map")
        return [self._apply(fn, [it], op) for it in items]

    def _op_filter(self, op, args, env):
        self._arity(op, args, 2)
        pred = self.evaluate(args[0], env)
        items = self._coerce_listlike(op, self.evaluate(args[1], env), "filter")
        return [it for it in items if self._apply(pred, [it], op)]

    def _op_rev(self, op, args, env):
        self._arity(op, args, 1)
        v = self.evaluate(args[0], env)
        if isinstance(v, str):
            return v[::-1]
        if isinstance(v, list):
            return v[::-1]
        raise TerseRuntimeError(
            "E208",
            f"rev expects a list or string, got {_typename(v)}",
            op=op,
            pos=None,
            expected="list or str",
            got=_typename(v),
        )

    def _op_keep(self, op, args, env):
        # Filter+map fusion: non-null f(x) values. (Clojure's keep.)
        self._arity(op, args, 2)
        fn = self.evaluate(args[0], env)
        items = self._coerce_listlike(op, self.evaluate(args[1], env), "keep")
        out = []
        for it in items:
            v = self._apply(fn, [it], op)
            if v is not None:
                out.append(v)
        return out

    def _op_return(self, op, args, env):
        self._arity(op, args, 1)
        raise _ReturnSignal(self.evaluate(args[0], env))

    # -- arithmetic -------------------------------------------------------

    def _op_add(self, op, args, env):
        self._arity(op, args, 2)
        a = self.evaluate(args[0], env)
        b = self.evaluate(args[1], env)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a + b  # int+float -> float, like Python
        if isinstance(a, str) and isinstance(b, str):
            return a + b
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        raise TerseRuntimeError(
            "E208",
            f"cannot add {_typename(a)} and {_typename(b)}",
            op=op,
            pos=None,
            expected="number+number, str+str or list+list",
            got=f"{_typename(a)}, {_typename(b)}",
        )

    def _op_sub(self, op, args, env):
        return self._arith2(op, args, env, lambda a, b: a - b)

    def _op_mul(self, op, args, env):
        return self._arith2(op, args, env, lambda a, b: a * b)

    def _op_div(self, op, args, env):
        return self._arith2(op, args, env, lambda a, b: a / b)  # true division

    def _op_mod(self, op, args, env):
        return self._arith2(op, args, env, lambda a, b: a % b)

    def _op_pow(self, op, args, env):
        return self._arith2(op, args, env, lambda a, b: a**b)

    def _op_neg(self, op, args, env):
        self._arity(op, args, 1)
        return -self._as_number(op, self.evaluate(args[0], env), "as operand")

    def _op_dec(self, op, args, env):
        self._arity(op, args, 1)
        return self._as_number(op, self.evaluate(args[0], env), "as operand") - 1

    # -- comparison -------------------------------------------------------

    def _op_eq(self, op, args, env):
        self._arity(op, args, 2)
        return self.evaluate(args[0], env) == self.evaluate(args[1], env)

    def _op_ne(self, op, args, env):
        self._arity(op, args, 2)
        return self.evaluate(args[0], env) != self.evaluate(args[1], env)

    def _op_lt(self, op, args, env):
        return self._compare(op, args, env, lambda a, b: a < b, "<")

    def _op_le(self, op, args, env):
        return self._compare(op, args, env, lambda a, b: a <= b, "<=")

    def _op_gt(self, op, args, env):
        return self._compare(op, args, env, lambda a, b: a > b, ">")

    def _op_ge(self, op, args, env):
        return self._compare(op, args, env, lambda a, b: a >= b, ">=")

    # -- logic ------------------------------------------------------------

    def _op_and(self, op, args, env):
        if len(args) < 2:
            self._arity(op, args, 2, 255)
        result = None
        for a in args:
            result = self.evaluate(a, env)
            if not result:  # Python-like truthiness; short-circuit
                return result
        return result

    def _op_or(self, op, args, env):
        if len(args) < 2:
            self._arity(op, args, 2, 255)
        result = None
        for a in args:
            result = self.evaluate(a, env)
            if result:  # Python-like truthiness; short-circuit
                return result
        return result

    def _op_not(self, op, args, env):
        self._arity(op, args, 1)
        return not self.evaluate(args[0], env)

    # -- control flow -----------------------------------------------------

    def _op_if(self, op, args, env):
        # N-ary if: (if c1 e1 c2 e2 ... [else]). Pairs of condition/branch;
        # a trailing odd argument is the else. All-false with no else -> null.
        if len(args) < 2:
            raise TerseRuntimeError(
                "E207",
                f"wrong number of arguments: expected 2 or more, got {len(args)}",
                op=op,
            )
        i, n = 0, len(args)
        while i + 1 < n:
            if self.evaluate(args[i], env):
                return self.evaluate(args[i + 1], env)
            i += 2
        if i < n:  # trailing else
            return self.evaluate(args[i], env)
        return None

    def _op_while(self, op, args, env):
        self._arity(op, args, 2)
        while self.evaluate(args[0], env):
            self.evaluate(args[1], env)
        return None

    def _op_for(self, op, args, env):
        self._arity(op, args, 3)
        name = args[0]
        if not isinstance(name, str):
            raise TerseRuntimeError(
                "E208",
                f"for loop variable must be a string, got {_typename(name)}",
                op=op,
                pos=None,
                expected="str",
                got=_typename(name),
            )
        iterable = self.evaluate(args[1], env)
        if isinstance(iterable, str):
            items = list(iterable)  # iterate characters
        elif isinstance(iterable, list):
            items = iterable
        else:
            raise TerseRuntimeError(
                "E208",
                f"for expects a list or string to iterate, got {_typename(iterable)}",
                op=op,
                pos=None,
                expected="list or str",
                got=_typename(iterable),
            )
        child = env.child()
        for item in items:
            child.bindings[name] = item
            self.evaluate(args[2], child)
        return None

    def _op_fold(self, op, args, env):
        # ("fold", [acc_name, elem_name], init, coll, body): the recursion
        # scheme for "loop with state". acc starts at init; each iteration
        # binds elem to the next item and acc to the running value; the
        # body's value becomes the next acc. Returns the final acc.
        self._arity(op, args, 4)
        names = args[0]
        if (
            not isinstance(names, list)
            or len(names) != 2
            or not all(isinstance(nm, str) for nm in names)
        ):
            raise TerseRuntimeError(
                "E208",
                "fold bindings must be a list of two strings (acc x)",
                op=op,
                pos=None,
                expected="list[str] of length 2",
                got=_typename(names),
            )
        acc_name, elem_name = names
        acc = self.evaluate(args[1], env)
        coll = self.evaluate(args[2], env)
        if isinstance(coll, str):
            items = list(coll)
        elif isinstance(coll, list):
            items = coll
        else:
            raise TerseRuntimeError(
                "E208",
                f"fold expects a list or string to iterate, got {_typename(coll)}",
                op=op,
                pos=None,
                expected="list or str",
                got=_typename(coll),
            )
        child = env.child()
        for item in items:
            child.bindings[elem_name] = item
            child.bindings[acc_name] = acc
            acc = self.evaluate(args[3], child)
        return acc

    def _op_assert(self, op, args, env):
        self._arity(op, args, 1, 2)
        if not self.evaluate(args[0], env):
            suffix = ""
            if len(args) == 2:
                # evaluated only on failure, so a printing message has no
                # side effect when the assertion holds
                suffix = f": {self.evaluate(args[1], env)}"
            raise TerseRuntimeError(
                "E204",
                f"assertion failed{suffix}",
                op=op,
                pos=None,
            )
        return None

    # -- collections ------------------------------------------------------

    def _op_list(self, op, args, env):
        return [self.evaluate(a, env) for a in args]

    def _op_dict(self, op, args, env):
        if len(args) % 2 != 0:
            raise TerseRuntimeError(
                "E207",
                f"dict needs an even argument count (key/value pairs), got {len(args)}",
                op=op,
                pos=None,
                hint="pass alternating keys and values",
            )
        d: dict[Any, Any] = {}
        for i in range(0, len(args), 2):
            k = self.evaluate(args[i], env)
            v = self.evaluate(args[i + 1], env)
            try:
                d[k] = v
            except TypeError:
                raise TerseRuntimeError(
                    "E208",
                    f"unhashable dict key of type {_typename(k)}",
                    op=op,
                    pos=None,
                    expected="hashable key",
                    got=_typename(k),
                ) from None
        return d

    def _op_get(self, op, args, env):
        self._arity(op, args, 2)
        coll = self.evaluate(args[0], env)
        key = self.evaluate(args[1], env)
        if isinstance(coll, dict):
            try:
                return coll[key]
            except KeyError:
                raise TerseRuntimeError(
                    "E202",
                    f"missing dict key {key!r}",
                    op=op,
                    pos=None,
                    expected="existing key",
                    got=repr(key),
                    hint="use get only with keys present in the dict",
                ) from None
            except TypeError:
                raise TerseRuntimeError(
                    "E202",
                    f"unhashable dict key of type {_typename(key)}",
                    op=op,
                    pos=None,
                    expected="hashable key",
                    got=_typename(key),
                ) from None
        if isinstance(coll, (list, str)):
            if not isinstance(key, int):
                raise TerseRuntimeError(
                    "E202",
                    f"index must be an int, got {_typename(key)}",
                    op=op,
                    pos=None,
                    expected="int index",
                    got=_typename(key),
                )
            try:
                return coll[key]  # negative indices allowed, Python-like
            except IndexError:
                raise TerseRuntimeError(
                    "E202",
                    f"index {key} out of range for {_typename(coll)} "
                    f"of length {len(coll)}",
                    op=op,
                    pos=None,
                    expected=f"index in -{len(coll)}..{len(coll) - 1}",
                    got=str(key),
                ) from None
        raise TerseRuntimeError(
            "E202",
            f"get expects a dict, list or string, got {_typename(coll)}",
            op=op,
            pos=None,
            expected="dict, list or str",
            got=_typename(coll),
        )

    def _op_put(self, op, args, env):
        """Functional update: returns a NEW collection, never mutates."""
        self._arity(op, args, 3)
        coll = self.evaluate(args[0], env)
        k = self.evaluate(args[1], env)
        v = self.evaluate(args[2], env)
        if isinstance(coll, dict):
            try:
                new = dict(coll)
                new[k] = v
                return new
            except TypeError:
                raise TerseRuntimeError(
                    "E208",
                    f"unhashable dict key of type {_typename(k)}",
                    op=op,
                    pos=None,
                    expected="hashable key",
                    got=_typename(k),
                ) from None
        if isinstance(coll, list):
            if not isinstance(k, int):
                raise TerseRuntimeError(
                    "E202",
                    f"list index must be an int, got {_typename(k)}",
                    op=op,
                    pos=None,
                    expected="int index",
                    got=_typename(k),
                )
            if not 0 <= k < len(coll):
                raise TerseRuntimeError(
                    "E202",
                    f"index {k} out of range for list of length {len(coll)}",
                    op=op,
                    pos=None,
                    expected=f"index in 0..{len(coll) - 1}",
                    got=str(k),
                    hint="put replaces an existing element; use push to append",
                )
            new = list(coll)
            new[k] = v
            return new
        raise TerseRuntimeError(
            "E208",
            f"put expects a dict or list, got {_typename(coll)}",
            op=op,
            pos=None,
            expected="dict or list",
            got=_typename(coll),
        )

    def _op_inc(self, op, args, env):
        # ("inc", coll, key[, by]): persistent counter increment. Returns a
        # NEW dict with key's value increased by `by` (default 1). A missing
        # key counts as 0, so no pre-seeding is needed.
        self._arity(op, args, 2, 3)
        coll = self.evaluate(args[0], env)
        k = self.evaluate(args[1], env)
        by = self.evaluate(args[2], env) if len(args) == 3 else 1
        if not isinstance(coll, dict):
            raise TerseRuntimeError(
                "E208",
                f"inc expects a dict, got {_typename(coll)}",
                op=op,
                pos=None,
                expected="dict",
                got=_typename(coll),
            )
        if not isinstance(by, (int, float)) or isinstance(by, bool):
            raise TerseRuntimeError(
                "E208",
                f"inc 'by' must be a number, got {_typename(by)}",
                op=op,
                pos=None,
                expected="number",
                got=_typename(by),
            )
        try:
            cur = coll.get(k, 0)
        except TypeError:
            raise TerseRuntimeError(
                "E208",
                f"unhashable dict key of type {_typename(k)}",
                op=op,
                pos=None,
                expected="hashable key",
                got=_typename(k),
            ) from None
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            raise TerseRuntimeError(
                "E208",
                f"inc target must be a number, got {_typename(cur)}",
                op=op,
                pos=None,
                expected="number",
                got=_typename(cur),
            )
        new = dict(coll)
        new[k] = cur + by
        return new

    def _op_len(self, op, args, env):
        self._arity(op, args, 1)
        v = self.evaluate(args[0], env)
        if isinstance(v, (list, dict, str)):
            return len(v)
        raise TerseRuntimeError(
            "E208",
            f"len expects a list, dict or string, got {_typename(v)}",
            op=op,
            pos=None,
            expected="list, dict or str",
            got=_typename(v),
        )

    def _op_range(self, op, args, env):
        self._arity(op, args, 1, 3)
        vals = []
        for i, a in enumerate(args):
            v = self.evaluate(a, env)
            if not isinstance(v, int):
                raise TerseRuntimeError(
                    "E208",
                    f"range argument {i + 1} must be an int, got {_typename(v)}",
                    op=op,
                    pos=None,
                    expected="int",
                    got=_typename(v),
                )
            vals.append(v)
        try:
            return list(range(*vals))
        except ValueError as e:  # e.g. range(0, 10, 0)
            raise TerseRuntimeError(
                "E208", f"bad range arguments: {e}", op=op, pos=None
            ) from None

    def _op_push(self, op, args, env):
        self._arity(op, args, 2)
        lst = self.evaluate(args[0], env)
        v = self.evaluate(args[1], env)
        if not isinstance(lst, list):
            raise TerseRuntimeError(
                "E208",
                f"push expects a list, got {_typename(lst)}",
                op=op,
                pos=None,
                expected="list",
                got=_typename(lst),
            )
        return [*lst, v]

    # -- strings ----------------------------------------------------------

    def _op_cat(self, op, args, env):
        return "".join(str(self.evaluate(a, env)) for a in args)

    def _op_str(self, op, args, env):
        self._arity(op, args, 1)
        return str(self.evaluate(args[0], env))

    def _op_int(self, op, args, env):
        self._arity(op, args, 1)
        v = self.evaluate(args[0], env)
        try:
            # int(3.7) -> 3 (truncation toward zero), int("42") -> 42;
            # int("3.7") and int("abc") fail, Python-like.
            return int(v)
        except (ValueError, TypeError, OverflowError):
            raise TerseRuntimeError(
                "E203",
                f"cannot convert {_typename(v)} {v!r} to int",
                op=op,
                pos=None,
                expected="int-convertible value",
                got=_typename(v),
            ) from None

    def _op_float(self, op, args, env):
        self._arity(op, args, 1)
        v = self.evaluate(args[0], env)
        try:
            return float(v)
        except (ValueError, TypeError, OverflowError):
            raise TerseRuntimeError(
                "E203",
                f"cannot convert {_typename(v)} {v!r} to float",
                op=op,
                pos=None,
                expected="float-convertible value",
                got=_typename(v),
            ) from None

    def _op_upper(self, op, args, env):
        self._arity(op, args, 1)
        return self._as_str(op, self.evaluate(args[0], env), "as operand").upper()

    def _op_lower(self, op, args, env):
        self._arity(op, args, 1)
        return self._as_str(op, self.evaluate(args[0], env), "as operand").lower()

    def _op_split(self, op, args, env):
        self._arity(op, args, 2)
        s = self._as_str(op, self.evaluate(args[0], env), "as input")
        sep = self._as_str(op, self.evaluate(args[1], env), "as separator")
        try:
            return s.split(sep)
        except ValueError as e:  # empty separator
            raise TerseRuntimeError(
                "E208", f"split failed: {e}", op=op, pos=None
            ) from None

    def _op_join(self, op, args, env):
        self._arity(op, args, 2)
        sep = self._as_str(op, self.evaluate(args[0], env), "as separator")
        lst = self.evaluate(args[1], env)
        if not isinstance(lst, list):
            raise TerseRuntimeError(
                "E208",
                f"join expects a list, got {_typename(lst)}",
                op=op,
                pos=None,
                expected="list",
                got=_typename(lst),
            )
        return sep.join(str(x) for x in lst)

    def _op_print(self, op, args, env):
        self.output.append(
            " ".join(str(self.evaluate(a, env)) for a in args) + "\n"
        )
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(ast: Any, gas: int = 1_000_000) -> tuple[Any, str]:
    """Evaluate a Terse AST, returning ``(value, output)``.

    ``value`` is the value of the root node; ``output`` is everything
    ``print`` wrote, as one string. Every node evaluation consumes 1 gas;
    exhaustion raises ``TerseRuntimeError`` E205.
    """
    interp = _Interpreter(gas)
    env = _Env()
    try:
        value = interp.evaluate(ast, env)
    except _ReturnSignal as sig:
        # `return` outside any call: just yield its value.
        value = sig.value
    return value, "".join(interp.output)


def run_source(src: str, gas: int = 1_000_000) -> tuple[Any, str]:
    """Parse Terse source text and run it. Returns ``(value, output)``.

    The parser is imported lazily (inside this function) so that
    ``terse.interp`` never depends on ``terse.parser`` at module load time
    — avoiding a circular import if the parser ever imports the interpreter
    (e.g. for constant folding). Assumes ``terse.parser`` exposes
    ``parse(src) -> AST``.
    """
    from terse.parser import parse  # lazy: parser is another worker's module

    return run(parse(src), gas=gas)
