import math

import pytest

from goldmaker.lua_parser import LuaParseError, parse_lua_assignments, parse_lua_value


def test_simple_assignment_and_types():
    data = parse_lua_assignments('X = { ["a"] = 1, b = 2.5, ["c"] = true, d = false, e = nil, f = "hi" }')
    assert data["X"] == {"a": 1, "b": 2.5, "c": True, "d": False, "e": None, "f": "hi"}


def test_array_tables_become_lists():
    assert parse_lua_value("{ 10, 20, 30 }") == [10, 20, 30]
    assert parse_lua_value("{ [1] = 'a', [2] = 'b' }") == ["a", "b"]


def test_sparse_or_mixed_tables_stay_dicts():
    assert parse_lua_value("{ [1] = 'a', [3] = 'c' }") == {1: "a", 3: "c"}
    assert parse_lua_value("{ 'x', name = 'y' }") == {1: "x", "name": "y"}


def test_empty_table_is_empty_dict():
    assert parse_lua_value("{}") == {}


def test_comments_are_ignored():
    text = """
    -- leading comment
    X = {  -- trailing
        { ["n"] = 1, }, -- [1]
        --[[ block
        comment ]]
        { ["n"] = 2, }, -- [2]
        --[==[ another ]==]
    }
    """
    assert parse_lua_assignments(text)["X"] == [{"n": 1}, {"n": 2}]


def test_numbers():
    assert parse_lua_value("{-5, 1.5e3, 0x1F, .5, 3.}") == [-5, 1500.0, 31, 0.5, 3.0]
    value = parse_lua_value("{ 1.#INF, -1.#INF, 1.#IND }")
    assert value[0] == math.inf and value[1] == -math.inf and math.isnan(value[2])


def test_string_escapes_and_utf8():
    assert parse_lua_value(r'"a\"b\\c\nd"') == 'a"b\\c\nd'
    assert parse_lua_value(r'"caf\195\169"') == "café"          # decimal byte escapes -> UTF-8
    assert parse_lua_value('"Zoë|Hitem:1|h[X]|h"') == "Zoë|Hitem:1|h[X]|h"
    assert parse_lua_value("'it\\'s'") == "it's"


def test_long_strings():
    assert parse_lua_value("[[hello]]") == "hello"
    assert parse_lua_value("[==[a]]b]==]") == "a]]b"
    assert parse_lua_value("[[\nfirst newline dropped]]") == "first newline dropped"


def test_multiple_assignments_and_semicolons():
    data = parse_lua_assignments("A = 1; B = { 2 }\nC = 'x'")
    assert data == {"A": 1, "B": [2], "C": "x"}


def test_bom_is_tolerated():
    assert parse_lua_assignments("\ufeffA = 1") == {"A": 1}


@pytest.mark.parametrize("bad", [
    "X = {", 'X = "unterminated', "X = { [1 = 2 }", "X = foo", "X = {1,,}x", "X", "= 5",
    "X = --[[ never closed", "X = '\\999'",
])
def test_invalid_input_raises(bad):
    with pytest.raises(LuaParseError):
        parse_lua_assignments(bad)


def test_error_reports_line_number():
    with pytest.raises(LuaParseError, match="line 3"):
        parse_lua_assignments("A = 1\nB = 2\nC = @")


def test_deep_nesting_is_rejected_not_crashed():
    with pytest.raises(LuaParseError):
        parse_lua_value("{" * 500 + "}" * 500)


def test_code_is_never_executed():
    # A function call or arbitrary expression is simply a parse error.
    with pytest.raises(LuaParseError):
        parse_lua_assignments('X = os.execute("calc")')
