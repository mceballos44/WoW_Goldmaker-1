"""Minimal, safe parser for Lua data files such as WoW SavedVariables.

SavedVariables files are plain Lua assignments of table literals, e.g.::

    CharExporterDB = {
        ["global"] = { ["Name-Realm"] = { ["timestamp"] = 1760000000 } },
    }

This parser only understands *data*: tables, strings, numbers, booleans, nil
and comments. No code is ever executed, so reading a file cannot run anything.

Conversion rules
----------------
* A table whose keys are exactly 1..n becomes a Python ``list``.
* Any other non-empty table becomes a ``dict``.
* An empty table becomes ``{}`` (Lua cannot tell empty list from empty dict).
* ``nil`` becomes ``None``.
"""

from __future__ import annotations

import re
from typing import Any

MAX_DEPTH = 200


class LuaParseError(ValueError):
    """The text is not valid Lua data."""


_NUM_RE = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_SPECIAL_NUM_RE = re.compile(r"-?(?:1\.#(?:INF|IND|QNAN)\d*|inf|nan)(?![A-Za-z0-9_])", re.I)
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LONG_OPEN_RE = re.compile(r"\[(=*)\[")
_DEC_ESCAPE_RE = re.compile(r"\d{1,3}")
_STRING_RUN = {
    '"': re.compile(r'[^"\\\n]+'),
    "'": re.compile(r"[^'\\\n]+"),
}
_SIMPLE_ESCAPES = {
    "a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11,
    "\\": 92, '"': 34, "'": 39, "\n": 10,
}
_WHITESPACE = " \t\r\n\f\v"


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.n = len(text)
        self.pos = 0

    # --- helpers ---------------------------------------------------------------
    def error(self, message: str):
        line = self.text.count("\n", 0, self.pos) + 1
        raise LuaParseError(f"line {line}: {message}")

    def skip_ws(self) -> None:
        t, n = self.text, self.n
        while self.pos < n:
            c = t[self.pos]
            if c in _WHITESPACE:
                self.pos += 1
            elif c == "-" and t.startswith("--", self.pos):
                self.pos += 2
                m = _LONG_OPEN_RE.match(t, self.pos)
                if m:
                    close = "]" + m.group(1) + "]"
                    end = t.find(close, m.end())
                    if end < 0:
                        self.error("unterminated block comment")
                    self.pos = end + len(close)
                else:
                    end = t.find("\n", self.pos)
                    self.pos = n if end < 0 else end + 1
            else:
                break

    def expect(self, char: str) -> None:
        self.skip_ws()
        if self.pos >= self.n or self.text[self.pos] != char:
            self.error(f"expected '{char}'")
        self.pos += 1

    # --- values -----------------------------------------------------------------
    def parse_value(self, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            self.error("tables are nested too deeply")
        self.skip_ws()
        if self.pos >= self.n:
            self.error("unexpected end of file")
        t = self.text
        c = t[self.pos]

        if c == "{":
            return self.parse_table(depth)
        if c in "\"'":
            return self.parse_string(c)
        if c == "[" and _LONG_OPEN_RE.match(t, self.pos):
            return self.parse_long_string()

        m = _SPECIAL_NUM_RE.match(t, self.pos)
        if m:
            self.pos = m.end()
            token = m.group().lower()
            if "inf" in token:
                return float("-inf") if token.startswith("-") else float("inf")
            return float("nan")

        if c.isdigit() or c in "-.":
            m = _NUM_RE.match(t, self.pos)
            if not m:
                self.error(f"invalid number near '{t[self.pos:self.pos + 10]}'")
            self.pos = m.end()
            token = m.group()
            if "x" in token.lower():
                return int(token, 16)
            if any(ch in token for ch in ".eE"):
                return float(token)
            return int(token)

        m = _NAME_RE.match(t, self.pos)
        if m:
            word = m.group()
            if word in ("true", "false", "nil"):
                self.pos = m.end()
                return {"true": True, "false": False, "nil": None}[word]
        self.error(f"unexpected token near '{t[self.pos:self.pos + 15]}'")

    def parse_string(self, quote: str) -> str:
        t, n = self.text, self.n
        self.pos += 1
        buf = bytearray()
        run = _STRING_RUN[quote]
        while True:
            if self.pos >= n:
                self.error("unterminated string")
            c = t[self.pos]
            if c == quote:
                self.pos += 1
                break
            if c == "\n":
                self.error("newline inside string")
            if c == "\\":
                self.pos += 1
                if self.pos >= n:
                    self.error("unterminated string")
                e = t[self.pos]
                m = _DEC_ESCAPE_RE.match(t, self.pos)
                if m:
                    value = int(m.group())
                    if value > 255:
                        self.error("decimal escape too large")
                    buf.append(value)
                    self.pos = m.end()
                elif e == "x":
                    digits = t[self.pos + 1:self.pos + 3]
                    try:
                        buf.append(int(digits, 16))
                    except ValueError:
                        self.error("invalid hex escape")
                    self.pos += 3
                elif e == "z":
                    self.pos += 1
                    while self.pos < n and t[self.pos] in _WHITESPACE:
                        self.pos += 1
                elif e in _SIMPLE_ESCAPES:
                    buf.append(_SIMPLE_ESCAPES[e])
                    self.pos += 1
                else:
                    buf += e.encode("utf-8")
                    self.pos += 1
                continue
            m = run.match(t, self.pos)
            buf += m.group().encode("utf-8")
            self.pos = m.end()
        return buf.decode("utf-8", errors="replace")

    def parse_long_string(self) -> str:
        m = _LONG_OPEN_RE.match(self.text, self.pos)
        close = "]" + m.group(1) + "]"
        start = m.end()
        end = self.text.find(close, start)
        if end < 0:
            self.error("unterminated long string")
        self.pos = end + len(close)
        content = self.text[start:end]
        if content.startswith("\r\n"):
            content = content[2:]
        elif content.startswith("\n"):
            content = content[1:]
        return content

    def parse_table(self, depth: int) -> Any:
        t, n = self.text, self.n
        self.pos += 1  # '{'
        entries: list = []
        next_index = 1
        while True:
            self.skip_ws()
            if self.pos >= n:
                self.error("unterminated table")
            c = t[self.pos]
            if c == "}":
                self.pos += 1
                break

            if c == "[" and not _LONG_OPEN_RE.match(t, self.pos):
                self.pos += 1
                key = self.parse_value(depth + 1)
                self.expect("]")
                self.expect("=")
                value = self.parse_value(depth + 1)
                if key is None:
                    self.error("table key cannot be nil")
                entries.append((key, value))
            else:
                m = _NAME_RE.match(t, self.pos)
                handled = False
                if m:
                    saved = self.pos
                    self.pos = m.end()
                    self.skip_ws()
                    if self.pos < n and t[self.pos] == "=" and not t.startswith("==", self.pos):
                        self.pos += 1
                        entries.append((m.group(), self.parse_value(depth + 1)))
                        handled = True
                    else:
                        self.pos = saved
                if not handled:
                    entries.append((next_index, self.parse_value(depth + 1)))
                    next_index += 1

            self.skip_ws()
            if self.pos < n and t[self.pos] in ",;":
                self.pos += 1
        return _finish_table(entries)


def _finish_table(entries: list) -> Any:
    if not entries:
        return {}
    keys = [k for k, _ in entries]
    if all(isinstance(k, int) and not isinstance(k, bool) for k in keys):
        if sorted(keys) == list(range(1, len(keys) + 1)):
            lookup = dict(entries)
            return [lookup[i] for i in range(1, len(keys) + 1)]
    return dict(entries)


def parse_lua_assignments(text: str) -> dict:
    """Parse top-level ``Name = value`` statements into ``{Name: value}``."""
    parser = _Parser(text.lstrip("\ufeff"))
    result: dict = {}
    while True:
        parser.skip_ws()
        if parser.pos >= parser.n:
            return result
        m = _NAME_RE.match(parser.text, parser.pos)
        if not m:
            parser.error("expected a variable name")
        parser.pos = m.end()
        parser.expect("=")
        result[m.group()] = parser.parse_value()
        parser.skip_ws()
        if parser.pos < parser.n and parser.text[parser.pos] == ";":
            parser.pos += 1


def parse_lua_value(text: str) -> Any:
    """Parse a single Lua value (e.g. a table literal)."""
    parser = _Parser(text.lstrip("\ufeff"))
    value = parser.parse_value()
    parser.skip_ws()
    if parser.pos < parser.n:
        parser.error("unexpected text after value")
    return value
