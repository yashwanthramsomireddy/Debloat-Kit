"""
DebloatKit v2.0 — App Scanner
Scans installed packages and cross-references with the master package database.
Author: Yashwanth Ram Somireddy | TeamExyKings | MIT License
"""

import json
import os
import re
from typing import Optional, Callable
from core.adb_manager import ADBManager, DeviceInfo


class AppEntry:
    def __init__(self):
        self.pkg: str          = ""
        self.name: str         = ""
        self.category: str     = "system"
        self.subcategory: str  = ""
        self.risk: str         = "SAFE"
        self.path: str         = "A"
        self.description: str  = ""
        self.state: str        = "enabled"
        self.in_db: bool       = False
        self.checked: bool     = False

    def to_dict(self) -> dict:
        return {
            "pkg": self.pkg, "name": self.name,
            "category": self.category, "subcategory": self.subcategory,
            "risk": self.risk, "path": self.path,
            "description": self.description, "state": self.state
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppEntry":
        e = cls()
        e.pkg         = d.get("pkg", "")
        e.name        = d.get("name", e.pkg)
        e.category    = d.get("category", "system")
        e.subcategory = d.get("subcategory", "")
        e.risk        = d.get("risk", "SAFE")
        e.path        = d.get("path", "A")
        e.description = d.get("description", "")
        e.state       = d.get("state", "enabled")
        return e


class AppScanner:
    def __init__(self, adb: ADBManager, db_path: str = None,
                 log_callback: Optional[Callable] = None):
        self.adb          = adb
        self.log_callback = log_callback
        self.db_path      = db_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "packages.json"
        )
        self._db_index: dict[str, dict] = {}
        self._load_db()

    def log(self, msg: str, level: str = "info"):
        if self.log_callback:
            self.log_callback(msg, level)

    def _load_db(self):
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                raw = f.read()
            raw  = re.sub(r"//.*", "", raw)
            data = json.loads(raw)
            pkgs = data.get("packages", [])
            self._db_index = {p["pkg"]: p for p in pkgs}
            self.log(f"Package DB loaded: {len(self._db_index)} entries", "info")
        except Exception as e:
            self.log(f"Failed to load package DB: {e}", "error")
            self._db_index = {}

    # ─── Single-shot scan ─────────────────────────────────────────────────────

    def scan(self, device_info: DeviceInfo,
             progress_callback: Optional[Callable] = None) -> dict[str, list[AppEntry]]:
        """
        ONE ADB call gets all package states at once.
        Much faster than 4 separate pm list calls.
        """
        result = {"system": [], "core": [], "user": [], "thirdparty": [], "keep": []}

        if progress_callback:
            progress_callback(0.05, "Querying all packages from device...")

        self.log("Starting scan — single ADB query...", "info")

        # ── One shell command that dumps ALL package info ─────────────────────
        # pm list packages -f gives: package:/path/to.apk=com.pkg.name
        # We run 3 queries combined in ONE shell call using && and labeled markers
        # This is much faster than 3 separate adb shell calls
        batch_cmd = (
            "echo __SYS_START__ && pm list packages -s && "
            "echo __USER_START__ && pm list packages -3 && "
            "echo __DIS_START__ && pm list packages -d"
        )
        ok, out = self.adb.run_shell(batch_cmd, timeout=45)

        if not ok:
            self.log(f"Scan failed: {out}", "error")
            if progress_callback:
                progress_callback(1.0, "Scan failed — check ADB connection")
            return result

        if progress_callback:
            progress_callback(0.5, "Parsing package list...")

        # ── Parse the batched output ──────────────────────────────────────────
        system_pkgs:   set[str] = set()
        user_pkgs:     set[str] = set()
        disabled_pkgs: set[str] = set()

        current_section = None
        for line in out.splitlines():
            line = line.strip()
            if line == "__SYS_START__":
                current_section = "sys"
            elif line == "__USER_START__":
                current_section = "usr"
            elif line == "__DIS_START__":
                current_section = "dis"
            elif line.startswith("package:"):
                pkg = line[8:].strip()
                if current_section == "sys":
                    system_pkgs.add(pkg)
                elif current_section == "usr":
                    user_pkgs.add(pkg)
                elif current_section == "dis":
                    disabled_pkgs.add(pkg)

        self.log(
            f"Raw: {len(system_pkgs)} system · {len(user_pkgs)} user · {len(disabled_pkgs)} disabled",
            "info"
        )

        if progress_callback:
            progress_callback(0.7, "Cross-referencing database...")

        era     = device_info.era
        all_pkgs = system_pkgs | user_pkgs

        for pkg in all_pkgs:
            db_rec = self._db_index.get(pkg)

            # Era filter for known packages
            if db_rec:
                eras = db_rec.get("eras", [1, 2, 3])
                if era not in eras:
                    continue

            state = "disabled" if pkg in disabled_pkgs else "enabled"
            entry = self._make_entry(pkg, db_rec, state, pkg in system_pkgs)

            if entry.risk == "KEEP":
                result["keep"].append(entry)
            else:
                result[entry.category].append(entry)

        # Sort alphabetically
        for cat in result:
            result[cat].sort(key=lambda x: x.name.lower())

        if progress_callback:
            progress_callback(1.0, "Scan complete")

        counts = {k: len(v) for k, v in result.items()}
        self.log(
            f"Done — System:{counts['system']} Core:{counts['core']} "
            f"User:{counts['user']} 3rdParty:{counts['thirdparty']} Keep:{counts['keep']}",
            "success"
        )
        return result

    def _make_entry(self, pkg: str, db_rec: Optional[dict],
                    state: str, is_system: bool) -> AppEntry:
        e         = AppEntry()
        e.pkg     = pkg
        e.state   = state
        e.checked = False

        if db_rec:
            e.name        = db_rec.get("name", pkg)
            e.category    = db_rec.get("category", "system")
            e.subcategory = db_rec.get("subcategory", "")
            e.risk        = db_rec.get("risk", "SAFE")
            e.path        = db_rec.get("path", "A")
            e.description = db_rec.get("description", "")
            e.in_db       = True
        else:
            # Unknown package — auto-categorize by prefix
            e.name    = pkg.split(".")[-1].replace("_", " ").title()
            e.in_db   = False
            e.risk    = "SAFE"
            e.path    = "A"
            e.description = "Unknown package — not in DebloatKit database"

            p = pkg.lower()
            if "google" in p or "android" in p:
                e.category    = "user"
                e.subcategory = "Google"
            elif "samsung" in p or "sec." in pkg.lower():
                e.category    = "system"
                e.subcategory = "Samsung (Unknown)"
            elif "facebook" in p or "fb." in p:
                e.category    = "thirdparty"
                e.subcategory = "Facebook"
            elif "microsoft" in p:
                e.category    = "thirdparty"
                e.subcategory = "Microsoft"
            elif is_system:
                e.category    = "system"
                e.subcategory = "Other System"
            else:
                e.category    = "thirdparty"
                e.subcategory = "Other"

        return e

    def reload_db(self):
        self._load_db()
