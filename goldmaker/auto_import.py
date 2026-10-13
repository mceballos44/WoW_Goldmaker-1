"""Automatic character import from CharExporter's SavedVariables file.

WoW writes ``CharExporter.lua`` when you log out or ``/reload``. Each time the app runs it
checks the file's last-written time and, only when it changed since the last check, reads
it and imports every character with the normal rules:

* an older (or identical) snapshot never replaces a newer one,
* manually entered profession stats are never touched,
* the file is only READ - nothing is ever written into the WoW folder.

Where to look: the WoW path saved in Settings (``wow_path``), else the usual install
folders that exist on this PC.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import charexporter, database, wow_files

SETTING_ENABLED = "auto_import_characters"     # "1" (default) or "0"
SETTING_SEEN = "charexporter_files_seen"       # {"<file path>": mtime}


@dataclass
class AutoImportReport:
    searched: list = field(default_factory=list)      # folders looked in
    files: list = field(default_factory=list)         # CharExporter.lua files found
    files_read: list = field(default_factory=list)    # files that had changed and were read
    results: list = field(default_factory=list)       # (character_key, status)
    errors: list = field(default_factory=list)

    @property
    def changed(self) -> list:
        """Characters that were actually stored (new or newer snapshot)."""
        return [key for key, status in self.results if status in ("new", "updated")]


def is_enabled(conn) -> bool:
    return database.get_setting(conn, SETTING_ENABLED, "1") != "0"


def set_enabled(conn, enabled: bool) -> None:
    database.set_setting(conn, SETTING_ENABLED, "1" if enabled else "0")


def search_locations(conn) -> list:
    """The saved WoW path if there is one, else the default install folders found on this PC."""
    saved = (database.get_setting(conn, "wow_path", "") or "").strip()
    if saved:
        return [saved]
    return [str(p) for p in wow_files.default_wow_roots()]


def _seen(conn) -> dict:
    raw = database.get_setting(conn, SETTING_SEEN)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def check_and_import(conn, locations: Optional[list] = None, force: bool = False) -> AutoImportReport:
    """Import characters from every CharExporter.lua whose last-written time changed.

    ``force`` re-reads files even if unchanged (snapshot rules still apply, so nothing
    older replaces anything newer).
    """
    report = AutoImportReport(searched=list(locations if locations is not None else search_locations(conn)))
    files: dict = {}
    for location in report.searched:
        for path in wow_files.find_charexporter_files(location):
            files[str(path.resolve())] = path
    report.files = sorted(files)

    seen = _seen(conn)
    for key, path in files.items():
        try:
            mtime = Path(path).stat().st_mtime
        except OSError as exc:
            report.errors.append(f"{path}: {exc}")
            continue
        if not force and seen.get(key) == mtime:
            continue
        try:
            parsed = charexporter.parse_savedvariables(wow_files.read_text_file(path))
        except (OSError, ValueError) as exc:          # CharExporterError is a ValueError
            report.errors.append(f"{path}: {exc}")
            continue
        report.files_read.append(key)
        for character in parsed.characters:
            if not character.has_identity:
                report.errors.append(f"{path}: a character without name/realm was skipped.")
                continue
            report.results.append((character.character_key,
                                   database.save_character_snapshot(conn, character)))
        seen[key] = mtime

    if report.files_read:
        database.set_setting(conn, SETTING_SEEN, json.dumps(seen))
    return report
