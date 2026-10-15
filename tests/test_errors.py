"""Error-protocol tests: to_dict shape, str rendering, and the code registry."""
import pytest

from terse.errors import (
    ERROR_CODES,
    ParseError,
    TerseError,
    TerseRuntimeError,
    TranspileError,
)

ALL_KEYS = {"code", "phase", "op", "pos", "message", "expected", "got", "hint"}


def _sample_error():
    return ParseError(
        "E103",
        "op 'add' got 1 argument(s)",
        op="add",
        pos={"line": 1, "col": 1, "index": 1},
        expected="exactly 2 argument(s)",
        got="1 argument(s)",
        hint="(add ...) takes exactly 2 argument(s); adjust the argument count",
    )


def test_to_dict_has_exactly_the_documented_keys():
    d = _sample_error().to_dict()
    assert set(d.keys()) == ALL_KEYS


def test_to_dict_values_round_trip():
    d = _sample_error().to_dict()
    assert d["code"] == "E103"
    assert d["phase"] == "parse"
    assert d["op"] == "add"
    assert d["pos"] == {"line": 1, "col": 1, "index": 1}
    assert d["message"] == "op 'add' got 1 argument(s)"
    assert d["expected"] == "exactly 2 argument(s)"
    assert d["got"] == "1 argument(s)"
    assert "adjust the argument count" in d["hint"]


def test_to_dict_is_json_serializable():
    import json

    json.dumps(_sample_error().to_dict())  # must not raise


def test_to_dict_copies_pos():
    err = _sample_error()
    d = err.to_dict()
    d["pos"]["line"] = 999
    assert err.pos["line"] == 1  # the error itself is unaffected


def test_str_contains_code():
    assert "E103" in str(_sample_error())


def test_str_format():
    s = str(_sample_error())
    assert s.startswith("[E103 parse]")
    assert "adjust the argument count" in s


def test_phases():
    assert ParseError("E100", "m").phase == "parse"
    assert TerseRuntimeError("E200", "m").phase == "runtime"
    assert TranspileError("E300", "m").phase == "transpile"


def test_all_are_terse_errors():
    for cls in (ParseError, TerseRuntimeError, TranspileError):
        assert issubclass(cls, TerseError)
        assert issubclass(cls, Exception)


def test_optional_fields_default_to_none():
    err = TerseError("E999", "custom")
    assert err.op is None
    assert err.pos is None
    assert err.expected is None
    assert err.got is None
    assert err.hint is None
    assert set(err.to_dict().keys()) == ALL_KEYS


def test_registry_covers_every_code():
    expected = (
        ["E100", "E101", "E103", "E104", "E105"]
        + ["E200", "E201", "E202", "E203", "E204", "E205", "E206", "E207", "E208"]
        + ["E300"]
    )
    for code in expected:
        assert code in ERROR_CODES, f"{code} missing from ERROR_CODES"
        assert isinstance(ERROR_CODES[code], str) and ERROR_CODES[code]


def test_registry_has_no_gaps_or_extras():
    # E102 (unknown-op) was retired in v0.2: unknown heads are calls now.
    assert len(ERROR_CODES) == 5 + 9 + 1 == 15
