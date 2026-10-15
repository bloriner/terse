"""Terse v0.1 — an AI-native code language (machine-designed prefix HIR).

Package layout (built by parallel workers against one shared AST contract):
    terse.errors      shared error types (this is the one stable import)
    terse.to_python   AST -> readable Python transpiler
    terse.parser      .terse surface syntax -> AST   (parser worker)
    terse.interp      tree-walking interpreter       (interpreter worker)

AST contract (tuples):
    node    = ("opname", arg1, ...)
    literal = raw int | float | str | bool | None
    varref  = ("var", "name")
"""

__version__ = "0.3.0"

# NOTE: interp/to_python are exposed lazily (PEP 562) so importing this
# package never risks a circular import regardless of worker import order.
from .errors import ParseError, TerseError, TerseRuntimeError, TranspileError
from .parser import parse, try_parse


def __getattr__(name):
    # NOTE: resolved callables are cached in the package globals. This is
    # required, not just an optimization: `from .to_python import to_python`
    # imports the *submodule* `terse.to_python`, and the import machinery
    # then sets the package attribute `terse.to_python` to that module,
    # which would permanently shadow this __getattr__ fallback (making the
    # second and later `terse.to_python` accesses return the module instead
    # of the function). Caching the function under the same name fixes it.
    if name == "run":
        from .interp import run

        globals()["run"] = run
        return run
    if name == "run_source":
        from .interp import run_source

        globals()["run_source"] = run_source
        return run_source
    if name == "to_python":
        from .to_python import to_python

        globals()["to_python"] = to_python
        return to_python
    raise AttributeError(f"module 'terse' has no attribute {name!r}")


__all__ = [
    "__version__",
    "parse",
    "try_parse",
    "run",
    "run_source",
    "to_python",
    "TerseError",
    "ParseError",
    "TerseRuntimeError",
    "TranspileError",
]
