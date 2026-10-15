"""Terse command-line interface.

Usage:
    python -m terse run prog.terse [--gas N]   parse, run, print program output
    python -m terse transpile prog.terse       print the transpiled Python
    python -m terse check prog.terse           parse only; print OK or an error

`run` writes the program's printed output to stdout and, when the program's
value is not None, `=> <repr(value)>` to stderr. Any TerseError (parse,
runtime, transpile) is reported as structured JSON on stderr with exit 1.
"""
from __future__ import annotations

import argparse
import json
import sys

from terse import parse, run, to_python
from terse.errors import TerseError

DEFAULT_GAS = 1_000_000


def _read_source(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        print(
            json.dumps({
                "code": "E000",
                "phase": "cli",
                "op": None,
                "pos": None,
                "message": f"cannot read file {path!r}: {exc.strerror or exc}",
                "expected": "a readable .terse file",
                "got": path,
                "hint": "check the file path",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)


def _report_terse_error(err: TerseError) -> int:
    print(json.dumps(err.to_dict()), file=sys.stderr)
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    src = _read_source(args.file)
    try:
        value, output = run(parse(src), gas=args.gas)
    except TerseError as err:
        return _report_terse_error(err)
    sys.stdout.write(output)
    if value is not None:
        print(f"=> {value!r}", file=sys.stderr)
    return 0


def cmd_transpile(args: argparse.Namespace) -> int:
    src = _read_source(args.file)
    try:
        print(to_python(parse(src)), end="")
    except TerseError as err:
        return _report_terse_error(err)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    src = _read_source(args.file)
    try:
        parse(src)
    except TerseError as err:
        return _report_terse_error(err)
    print("OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terse",
        description="Terse v0.1 — an AI-native code language (prefix HIR).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="parse and run a .terse program")
    p_run.add_argument("file", help="path to the .terse source file")
    p_run.add_argument(
        "--gas", type=int, default=DEFAULT_GAS,
        help=f"evaluation gas budget (default {DEFAULT_GAS})",
    )
    p_run.set_defaults(func=cmd_run)

    p_tr = sub.add_parser("transpile", help="transpile a .terse program to Python")
    p_tr.add_argument("file", help="path to the .terse source file")
    p_tr.set_defaults(func=cmd_transpile)

    p_check = sub.add_parser("check", help="parse a .terse program (no execution)")
    p_check.add_argument("file", help="path to the .terse source file")
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
