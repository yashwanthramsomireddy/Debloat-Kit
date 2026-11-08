"""
DebloatKit v2.0 — ADB Manager
Handles device detection, connection polling, and ADB command execution.
Author: Yashwanth Ram Somireddy | TeamExyKings | MIT License
"""

import subprocess
import threading
import time
from typing import Optional, Callable


class DeviceInfo:
    def __init__(self):
        self.serial: str          = ""
        self.model: str           = ""
        self.brand: str           = ""
        self.android_version: str = ""
        self.oneui_version: str   = ""
        self.api_level: str       = "0"
        self.battery_level: int   = 100
        self.is_rooted: bool      = False
        self.era: int             = 2   # 1=Legacy, 2=Mid, 3=Modern

    def get_era_label(self) -> str:
        return {
            1: "Legacy TouchWiz (S5–S9)",
            2: "One UI 1.x–5.x (S10–S23)",
            3: "Modern One UI 6.x–9 (S24–S26 Ultra)"
        }.get(self.era, "Unknown Era")


class ADBManager:
    def __init__(self, adb_path: str = "adb", log_callback: Optional[Callable] = None):
        self.adb_path        = adb_path
        self.log_callback    = log_callback
        self.device_info: Optional[DeviceInfo] = None

        # Polling state
        self._polling             = False
        self._poll_thread: Optional[threading.Thread] = None
        self._status_callback: Optional[Callable]     = None
        self._connected_callback: Optional[Callable]  = None
        self._disconnected_callback: Optional[Callable] = None
        self._last_status         = "no_device"
        self._detect_lock         = threading.Lock()   # prevent concurrent detect_device calls

    def log(self, message: str, level: str = "info"):
        if self.log_callback:
            try:
                self.log_callback(message, level)
            except Exception:
                pass

    # ─── Low-level runner ────────────────────────────────────────────────────

    def run(self, args: list, timeout: int = 8) -> tuple[bool, str]:
        """Run an adb command. Returns (success, output)."""
        try:
            cmd    = [self.adb_path] + args
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout,
                creationflags=0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                # CREATE_NO_WINDOW on Windows — prevents console flash
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            return (result.returncode == 0), (out or err)
        except subprocess.TimeoutExpired:
            return False, "ADB command timed out"
        except FileNotFoundError:
            return False, f"ADB not found at '{self.adb_path}'. Set path in Settings."
        except Exception as e:
            return False, str(e)

    def run_shell(self, cmd: str, timeout: int = 10) -> tuple[bool, str]:
        if not self.device_info:
            return False, "No device"
        return self.run(["-s", self.device_info.serial, "shell", cmd], timeout=timeout)

    def run_serial(self, args: list, serial: str, timeout: int = 8) -> tuple[bool, str]:
        return self.run(["-s", serial] + args, timeout=timeout)

    # ─── Device discovery ─────────────────────────────────────────────────────

    def get_connected_devices(self) -> list[dict]:
        ok, output = self.run(["devices"], timeout=5)
        if not ok:
            return []
        devices = []
        for line in output.splitlines()[1:]:
            line = line.strip()
            if "\t" in line:
                serial, status = line.split("\t", 1)
                devices.append({"serial": serial.strip(), "status": status.strip()})
        return devices

    def get_device_status(self) -> str:
        """Returns: no_device | unauthorized | ready"""
        try:
            devices = self.get_connected_devices()
        except Exception:
            return "no_device"
        if not devices:
            return "no_device"
        for d in devices:
            if d["status"] == "unauthorized":
                return "unauthorized"
            if d["status"] == "device":
                return "ready"
        return "no_device"

    def _getprop_batch(self, serial: str, props: list[str]) -> dict[str, str]:
        """Fetch multiple getprops in a single adb shell call."""
        script = "; ".join(f"echo ___{p}___$(getprop {p})" for p in props)
        ok, out = self.run_serial(["shell", script], serial, timeout=12)
        result  = {p: "" for p in props}
        if ok:
            for line in out.splitlines():
                for p in props:
                    marker = f"___{p}___"
                    if marker in line:
                        result[p] = line.split(marker, 1)[-1].strip()
        return result

    def detect_device(self) -> Optional[DeviceInfo]:
        """Full device info detection — single ADB round trip for props."""
        with self._detect_lock:
            devices = self.get_connected_devices()
            ready   = [d for d in devices if d["status"] == "device"]
            if not ready:
                return None

            info        = DeviceInfo()
            info.serial = ready[0]["serial"]
            self.device_info = info

            # Fetch all props in ONE shell call
            props = self._getprop_batch(info.serial, [
                "ro.product.model",
                "ro.product.brand",
                "ro.build.version.release",
                "ro.build.version.sdk",
                "ro.build.version.oneui",
            ])

            info.model           = props.get("ro.product.model")    or "Unknown Model"
            info.brand           = props.get("ro.product.brand")    or "Samsung"
            info.android_version = props.get("ro.build.version.release") or "?"
            info.api_level       = props.get("ro.build.version.sdk")     or "0"
            info.oneui_version   = props.get("ro.build.version.oneui")   or ""

            # Era from API level
            try:
                api = int(info.api_level)
                info.era = 1 if api <= 26 else (2 if api <= 33 else 3)
            except ValueError:
                info.era = 2

            # Battery — best-effort, don't block on failure
            try:
                ok, bat = self.run_serial(
                    ["shell", "dumpsys battery | grep level"], info.serial, timeout=5
                )
                if ok and "level:" in bat:
                    info.battery_level = int(bat.split("level:")[-1].strip().split()[0])
            except Exception:
                pass

            # Root check — best-effort
            try:
                ok, whoami = self.run_serial(["shell", "whoami"], info.serial, timeout=4)
                info.is_rooted = ok and "root" in whoami.lower()
            except Exception:
                pass

            self.log(
                f"Device: {info.brand} {info.model} | Android {info.android_version} "
                f"| API {info.api_level} | Era {info.era}",
                "success"
            )
            if info.battery_level < 20:
                self.log(f"⚠ Battery at {info.battery_level}% — charge before debloating.", "warning")
            if info.is_rooted:
                self.log("⚠ Root detected — operations still use --user 0 (safe).", "warning")

            return info

    # ─── Polling ──────────────────────────────────────────────────────────────

    def start_polling(self, on_status_change: Callable,
                      on_connected: Callable,
                      on_disconnected: Callable,
                      interval: int = 4):
        self._status_callback      = on_status_change
        self._connected_callback   = on_connected
        self._disconnected_callback= on_disconnected
        self._polling              = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval,), daemon=True
        )
        self._poll_thread.start()

    def stop_polling(self):
        self._polling = False

    def _poll_loop(self, interval: int):
        while self._polling:
            try:
                status = self.get_device_status()
            except Exception:
                status = "no_device"

            if status != self._last_status:
                prev_status       = self._last_status
                self._last_status = status

                # Always fire status change callback (runs on poll thread → app uses after())
                if self._status_callback:
                    try:
                        self._status_callback(status)
                    except Exception:
                        pass

                if status == "ready" and prev_status != "ready":
                    # Detect device info and fire connected callback
                    try:
                        info = self.detect_device()
                        if info and self._connected_callback:
                            self._connected_callback(info)
                    except Exception as e:
                        self.log(f"Device detection error: {e}", "error")

                elif status == "no_device" and prev_status == "ready":
                    self.device_info = None
                    if self._disconnected_callback:
                        try:
                            self._disconnected_callback()
                        except Exception:
                            pass

            time.sleep(interval)

    # ─── Package management ────────────────────────────────────────────────────

    def list_packages(self, flags: str = "") -> list[str]:
        ok, output = self.run_shell(f"pm list packages {flags}")
        if not ok:
            return []
        pkgs = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkgs.append(line[8:].strip())
        return pkgs

    def disable_package(self, pkg: str) -> tuple[bool, str]:
        """Path A: pm disable-user --user 0"""
        self.log(f"Disabling {pkg}...", "info")
        ok, out = self.run_shell(f"pm disable-user --user 0 {pkg}", timeout=15)
        out_l = out.lower()

        if "disabled" in out_l or "component disabled" in out_l:
            self.log(f"✓ Disabled: {pkg}", "success")
            return True, "disabled"

        if "SecurityException" in out or "Exception" in out:
            self.log(f"⚠ SecurityException for {pkg} — use Uninstall instead", "warning")
            return False, "security_exception"

        # Verify — some ROMs return empty on success
        _, verify = self.run_shell(f"pm list packages -d | grep -w {pkg}", timeout=8)
        if pkg in verify:
            self.log(f"✓ Disabled (verified): {pkg}", "success")
            return True, "disabled"

        self.log(f"✗ Failed to disable {pkg}: {out}", "error")
        return False, out

    def enable_package(self, pkg: str) -> tuple[bool, str]:
        """Re-enable a DISABLED package (was frozen with pm disable-user)."""
        self.log(f"Re-enabling (enable) {pkg}...", "info")
        ok, out = self.run_shell(f"pm enable --user 0 {pkg}", timeout=15)
        if ok and ("enabled" in out.lower() or "component enabled" in out.lower()):
            self.log(f"✓ Enabled: {pkg}", "success")
            return True, "enabled"
        # Some ROMs return empty on success — verify by checking package state
        ok2, out2 = self.run_shell(f"pm list packages -e | grep {pkg}", timeout=8)
        if ok2 and pkg in out2:
            self.log(f"✓ Enabled (verified): {pkg}", "success")
            return True, "enabled"
        self.log(f"✗ Failed to enable {pkg}: {out}", "error")
        return False, out

    def reinstall_package(self, pkg: str) -> tuple[bool, str]:
        """Restore an UNINSTALLED --user 0 package (was removed with pm uninstall -k)."""
        self.log(f"Restoring (install-existing) {pkg}...", "info")

        # Primary: pm install-existing --user 0 (most compatible)
        ok, out = self.run_shell(f"pm install-existing --user 0 {pkg}", timeout=20)
        if ok and "installed for user" in out.lower():
            self.log(f"✓ Restored: {pkg}", "success")
            return True, "enabled"

        # Fallback 1: cmd package install-existing
        ok2, out2 = self.run_shell(f"cmd package install-existing --user 0 {pkg}", timeout=20)
        if ok2 and ("installed" in out2.lower() or "success" in out2.lower()):
            self.log(f"✓ Restored (cmd): {pkg}", "success")
            return True, "enabled"

        # Fallback 2: pm enable (works if package is just disabled, not fully uninstalled)
        ok3, out3 = self.run_shell(f"pm enable --user 0 {pkg}", timeout=15)
        if ok3 and "enabled" in out3.lower():
            self.log(f"✓ Restored (enable fallback): {pkg}", "success")
            return True, "enabled"

        self.log(f"✗ Failed to restore {pkg}: {out} / {out2}", "error")
        return False, f"{out} | {out2}"

    def restore_package(self, pkg: str, previous_state: str) -> tuple[bool, str]:
        """Smart restore — uses correct command based on how package was disabled."""
        if previous_state == "disabled":
            return self.enable_package(pkg)
        else:
            # uninstalled — try reinstall first, then enable
            ok, msg = self.reinstall_package(pkg)
            if not ok:
                ok, msg = self.enable_package(pkg)
            return ok, msg

    def uninstall_package(self, pkg: str) -> tuple[bool, str]:
        self.log(f"[PATH A] Uninstalling {pkg} for user 0...", "info")
        ok, out = self.run_shell(f"pm uninstall -k --user 0 {pkg}", timeout=20)
        if ok and "success" in out.lower():
            self.log(f"✓ Uninstalled (reversible): {pkg}", "success")
            return True, "uninstalled"
        self.log(f"✗ Failed to uninstall {pkg}: {out}", "error")
        return False, out

    def uninstall_package_path_b(self, pkg: str) -> tuple[bool, str]:
        self.log(f"[PATH B] Force-uninstalling locked {pkg}...", "warning")
        ok, out = self.run_shell(f"pm uninstall --user 0 {pkg}", timeout=20)
        if ok and "success" in out.lower():
            self.log(f"✓ Path B uninstalled: {pkg}", "success")
            return True, "uninstalled"
        self.log(f"✗ Path B failed for {pkg}: {out}", "error")
        return False, out

    def soundalive_fix(self) -> bool:
        self.log("Running SoundAlive flush...", "info")
        self.run_shell("am force-stop com.sec.android.app.soundalive", timeout=8)
        self.run_shell("pm clear com.sec.android.app.soundalive", timeout=8)
        self.log("SoundAlive flushed. Reboot recommended.", "warning")
        return True

    def is_adb_available(self) -> bool:
        ok, _ = self.run(["version"], timeout=5)
        return ok
