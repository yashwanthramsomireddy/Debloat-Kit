"""
DebloatKit v2.0 — Debloater Engine
Handles disable/enable/uninstall/restore operations and backup management.
Author: Yashwanth Ram Somireddy | TeamExyKings | MIT License
"""

import json
import os
import sys
import threading
from datetime import datetime
from typing import Optional, Callable
from core.adb_manager import ADBManager
from core.app_scanner import AppEntry


def default_backup_dir() -> str:
    """Backups must live somewhere permanent.
    Source run  -> <project>/backups
    Built exe   -> <folder of DebloatKit.exe>/backups (PyInstaller's temp dir is wiped on
                   exit, so __file__ must NOT be used); falls back to %LOCALAPPDATA% if
                   that folder is read-only."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        d = os.path.join(base, "backups")
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".w")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return d
        except OSError:
            return os.path.join(os.environ.get("LOCALAPPDATA", base), "DebloatKit", "backups")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")


class DebloatResult:
    def __init__(self):
        self.pkg: str = ""
        self.name: str = ""
        self.action: str = ""
        self.success: bool = False
        self.message: str = ""
        self.path_used: str = "A"


class Debloater:
    def __init__(self, adb: ADBManager, backup_dir: str = None, log_callback: Optional[Callable] = None):
        self.adb = adb
        self.log_callback = log_callback
        self.backup_dir = backup_dir or default_backup_dir()
        os.makedirs(self.backup_dir, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, msg: str, level: str = "info"):
        if self.log_callback:
            self.log_callback(msg, level)

    # ─── Backup / Restore ────────────────────────────────────────────────────

    def create_backup(self, entries: list[AppEntry], label: str = "") -> str:
        """Save current package states to a timestamped JSON backup."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        label_safe = label.replace(" ", "_") if label else "manual"
        filename = f"backup_{label_safe}_{ts}.json"
        path = os.path.join(self.backup_dir, filename)

        data = {
            "created": datetime.now().isoformat(),
            "label": label or "Manual backup",
            "device": self.adb.device_info.model if self.adb.device_info else "Unknown",
            "packages": [e.to_dict() for e in entries]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        self.log(f"Backup saved: {filename}", "success")
        return path

    def auto_backup(self, entries: list[AppEntry]) -> str:
        """Silent auto-backup before any debloat action."""
        return self.create_backup(entries, label="auto_before_debloat")

    def list_backups(self) -> list[dict]:
        """List all backup files sorted by newest first."""
        backups = []
        for f in os.listdir(self.backup_dir):
            if f.endswith(".json"):
                path = os.path.join(self.backup_dir, f)
                try:
                    with open(path, "r") as fp:
                        data = json.load(fp)
                    backups.append({
                        "filename": f,
                        "path": path,
                        "label": data.get("label", f),
                        "created": data.get("created", ""),
                        "device": data.get("device", "Unknown"),
                        "count": len(data.get("packages", []))
                    })
                except Exception:
                    pass
        backups.sort(key=lambda x: x["created"], reverse=True)
        return backups

    def load_backup(self, path: str) -> list[AppEntry]:
        """Load entries from a backup file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [AppEntry.from_dict(p) for p in data.get("packages", [])]

    def delete_backup(self, path: str):
        if os.path.exists(path):
            os.remove(path)
            self.log(f"Backup deleted: {os.path.basename(path)}", "info")

    # ─── Core Actions ─────────────────────────────────────────────────────────

    def _execute(self, entries: list[AppEntry], action: str,
                 progress_callback: Optional[Callable] = None,
                 result_callback: Optional[Callable] = None) -> list[DebloatResult]:
        """
        Execute disable/enable/uninstall/restore on a list of entries.
        action: 'disable' | 'enable' | 'uninstall' | 'restore'
        """
        results = []
        total = len(entries)
        if total == 0:
            self.log("No packages selected.", "warning")
            return results

        if not self.adb.device_info:
            self.log("No device connected. Cannot proceed.", "error")
            return results

        self.log(f"Starting {action} on {total} package(s)...", "info")

        for i, entry in enumerate(entries):
            if progress_callback:
                progress_callback((i + 1) / total, f"{action.title()}: {entry.name}")

            r = DebloatResult()
            r.pkg = entry.pkg
            r.name = entry.name
            r.action = action
            r.path_used = entry.path

            with self._lock:
                if action == "disable":
                    ok, msg = self.adb.disable_package(entry.pkg)
                    if not ok and msg == "security_exception" and entry.path == "B":
                        # Auto fallback to Path B uninstall
                        self.log(f"Auto-switching to Path B for {entry.pkg}", "warning")
                        ok, msg = self.adb.uninstall_package_path_b(entry.pkg)
                        r.path_used = "B"
                    r.success = ok
                    r.message = msg
                    if ok:
                        entry.state = "disabled"

                elif action == "uninstall":
                    if entry.path == "B":
                        ok, msg = self.adb.uninstall_package_path_b(entry.pkg)
                    else:
                        ok, msg = self.adb.uninstall_package(entry.pkg)
                    r.success = ok
                    r.message = msg
                    if ok:
                        entry.state = "uninstalled"

                elif action == "enable":
                    # Smart restore — use correct command based on previous state
                    ok, msg = self.adb.restore_package(entry.pkg, entry.state)
                    r.success = ok
                    r.message = msg
                    if ok:
                        entry.state = "enabled"

                elif action == "restore":
                    ok, msg = self.adb.restore_package(entry.pkg, entry.state)
                    r.success = ok
                    r.message = msg
                    if ok:
                        entry.state = "enabled"

            results.append(r)
            if result_callback:
                result_callback(entry, r)

        success_count = sum(1 for r in results if r.success)
        fail_count = total - success_count
        self.log(
            f"{action.title()} complete — "
            f"✓ {success_count} succeeded · ✗ {fail_count} failed",
            "success" if fail_count == 0 else "warning"
        )
        return results

    def disable_packages(self, entries: list[AppEntry],
                         all_entries: list[AppEntry],
                         progress_callback: Optional[Callable] = None,
                         result_callback: Optional[Callable] = None) -> list[DebloatResult]:
        """Disable selected packages. Auto-backups first."""
        self.auto_backup(all_entries)
        return self._execute(entries, "disable", progress_callback, result_callback)

    def uninstall_packages(self, entries: list[AppEntry],
                           all_entries: list[AppEntry],
                           progress_callback: Optional[Callable] = None,
                           result_callback: Optional[Callable] = None) -> list[DebloatResult]:
        """Uninstall selected packages. Auto-backups first."""
        self.auto_backup(all_entries)
        return self._execute(entries, "uninstall", progress_callback, result_callback)

    def enable_packages(self, entries: list[AppEntry],
                        progress_callback: Optional[Callable] = None,
                        result_callback: Optional[Callable] = None) -> list[DebloatResult]:
        return self._execute(entries, "enable", progress_callback, result_callback)

    def restore_packages(self, entries: list[AppEntry],
                         progress_callback: Optional[Callable] = None,
                         result_callback: Optional[Callable] = None) -> list[DebloatResult]:
        return self._execute(entries, "restore", progress_callback, result_callback)

    def panic_restore(self, backup_path: str,
                      progress_callback: Optional[Callable] = None,
                      result_callback: Optional[Callable] = None) -> list[DebloatResult]:
        """Restore all packages from a backup file."""
        self.log(f"PANIC RESTORE from: {os.path.basename(backup_path)}", "warning")
        entries = self.load_backup(backup_path)
        to_restore = [e for e in entries if e.state in ("disabled", "uninstalled")]
        if not to_restore:
            self.log("Nothing to restore — all packages already enabled in backup.", "info")
            return []
        self.log(f"Restoring {len(to_restore)} packages...", "warning")
        return self._execute(to_restore, "restore", progress_callback, result_callback)

    def get_latest_backup_path(self) -> Optional[str]:
        backups = self.list_backups()
        return backups[0]["path"] if backups else None

    def soundalive_fix(self):
        """Post-debloat SoundAlive audio engine fix."""
        self.adb.soundalive_fix()

    def get_summary(self, results: list[DebloatResult]) -> dict:
        return {
            "total": len(results),
            "success": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "path_a": sum(1 for r in results if r.path_used == "A"),
            "path_b": sum(1 for r in results if r.path_used == "B")
        }
