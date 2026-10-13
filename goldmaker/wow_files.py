"""Locate and read the CharExporter SavedVariables file on this computer."""

from __future__ import annotations

from pathlib import Path

FILE_NAME = "CharExporter.lua"
MAX_FILE_BYTES = 100 * 1024 * 1024

_DEFAULT_ROOTS = (
    r"C:\Program Files (x86)\World of Warcraft",
    r"C:\Program Files\World of Warcraft",
    r"C:\World of Warcraft",
    r"C:\Games\World of Warcraft",
    r"D:\World of Warcraft",
    r"D:\Games\World of Warcraft",
)


def default_wow_roots() -> list:
    """Common install folders that actually exist on this machine."""
    return [Path(p) for p in _DEFAULT_ROOTS if Path(p).is_dir()]


def find_charexporter_files(path) -> list:
    """Find CharExporter.lua starting from any of these locations:

    * the file itself
    * an account folder   (...\\WTF\\Account\\<ACCOUNT>)
    * the Account folder  (...\\WTF\\Account)
    * the WTF folder, the _retail_ folder, or the game root
    Newest file first.
    """
    base = Path(str(path).strip().strip('"')).expanduser()
    if base.is_file():
        return [base]
    if not base.is_dir():
        return []
    patterns = (
        f"SavedVariables/{FILE_NAME}",
        f"*/SavedVariables/{FILE_NAME}",
        f"Account/*/SavedVariables/{FILE_NAME}",
        f"WTF/Account/*/SavedVariables/{FILE_NAME}",
        f"_retail_/WTF/Account/*/SavedVariables/{FILE_NAME}",
    )
    found = {}
    for pattern in patterns:
        for match in base.glob(pattern):
            if match.is_file():
                found[match.resolve()] = match
    return sorted(found.values(), key=lambda f: f.stat().st_mtime, reverse=True)


def read_text_file(path) -> str:
    file = Path(path)
    size = file.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"{file.name} is {size / 1e6:.0f} MB, which is larger than the "
                         f"{MAX_FILE_BYTES // 1_000_000} MB limit.")
    return file.read_bytes().decode("utf-8-sig", errors="replace")
