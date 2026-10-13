import os
import time

from goldmaker import wow_files


def _make(base, *parts):
    path = base.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('CharExporterDB = {}', encoding="utf-8")
    return path


def test_finds_from_every_starting_folder(tmp_path):
    f = _make(tmp_path / "World of Warcraft", "_retail_", "WTF", "Account", "ACCT1", "SavedVariables", "CharExporter.lua")
    root = tmp_path / "World of Warcraft"
    for start in (root, root / "_retail_", root / "_retail_" / "WTF", root / "_retail_" / "WTF" / "Account",
                  root / "_retail_" / "WTF" / "Account" / "ACCT1", f):
        assert wow_files.find_charexporter_files(start) == [f] or \
               [p.resolve() for p in wow_files.find_charexporter_files(start)] == [f.resolve()]


def test_multiple_accounts_newest_first(tmp_path):
    base = tmp_path / "Account"
    old = _make(base, "A", "SavedVariables", "CharExporter.lua")
    new = _make(base, "B", "SavedVariables", "CharExporter.lua")
    os.utime(old, (time.time() - 1000, time.time() - 1000))
    found = wow_files.find_charexporter_files(base)
    assert [p.resolve() for p in found] == [new.resolve(), old.resolve()]


def test_missing_or_empty_returns_nothing(tmp_path):
    assert wow_files.find_charexporter_files(tmp_path / "nope") == []
    assert wow_files.find_charexporter_files(tmp_path) == []


def test_quoted_paths_are_accepted(tmp_path):
    f = _make(tmp_path, "Account", "A", "SavedVariables", "CharExporter.lua")
    assert wow_files.find_charexporter_files(f'"{f}"')


def test_read_text_handles_bom(tmp_path):
    f = tmp_path / "x.lua"
    f.write_bytes(b"\xef\xbb\xbfA = 1")
    assert wow_files.read_text_file(f) == "A = 1"
