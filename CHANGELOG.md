# Changelog — DebloatKit

All notable changes to DebloatKit will be documented here.
Format: [Semantic Versioning](https://semver.org) — `MAJOR.MINOR.PATCH`

---

## [2.0] — 2026-10-22

> **Initial public release**
> Works with Samsung Galaxy devices — Android 5.0 (TouchWiz) through Android 16 (One UI 9)

### Added

#### Core Engine
- ADB device detection via `adb devices` — polls every 4 seconds
- Automatic device era detection from API level (Legacy / Mid One UI / Modern One UI)
- Device info display — model, brand, Android version, One UI version, battery level
- Battery warning when level below 20% before debloating
- Root detection with advisory message

#### Package Database
- 110+ curated packages with risk ratings across 3 device eras
- Risk tiers: SAFE · RECOMMENDED · CAUTION · CORE · LOCKED · KEEP
- Categories: System Apps · Core Apps · User Apps · 3rd Party · Keep
- Sources: personal debloat list, XDA Community research, Google/AOSP documentation

#### Debloat Operations
- **Path A** — `pm disable-user --user 0` for standard packages
- **Path A** — `pm uninstall -k --user 0` for user-profile removal (keeps APK)
- **Path B** — `pm uninstall --user 0` + `cmd package install-existing` for security-locked packages (One UI 6+)
- **Path C** — SoundAlive audio engine flush for post-diagmonagent removal
- Auto SecurityException detection with Path B fallback suggestion

#### Safety Features
- Auto silent backup before every debloat/uninstall action → timestamped JSON in `backups/`
- Panic Restore — re-enable all packages from most recent backup in one click
- Re-enable Selected — smart restore: `pm enable` for disabled apps, `pm install-existing --user 0` for uninstalled apps
- Core Apps confirmation dialog — individual ⛔ warning per system-critical package

#### User Interface
- 7-tab layout: System · Core · User · 3rd Party · Log · Settings · About
- PurgeKit design language — `#0a0a0a` pitch black, `#00e676` green accent
- Risk badges with color coding per row
- Package state indicators — Enabled / Disabled / Uninstalled (live updates)
- Subcategory grouping with per-group ✓ / ✕ select buttons
- Per-tab search and subcategory filter
- Select All / Deselect All per tab
- Disable Selected / Uninstall Selected action buttons per tab
- Post-action summary card — succeeded / failed / backup path
- USB Debugging step-by-step guide (auto-shown on Unauthorized device)
- 4 themes — Green (default), Blue, Purple, White
- Floating log panel toggled from the bottom bar
- Full log tab with export to .txt
- SoundAlive fix prompt after diagmonagent removal
- About tab with donate section (PayPal, Razorpay)
- Legal disclaimer on About tab

#### Performance & Window (v2.0)
- Default window is now a proper **portrait window** (640×940, centred, fitted to the screen, resizable, min 560×640) instead of maximised
- Package lists are **virtualised** — one canvas draws only the visible rows, so 5 or 500+ packages cost the same; tab switches are ~3–15 ms
- Log / Settings / About pages are built **once** (in the background after start-up) and switched with `tkraise()` — nothing is destroyed or rebuilt on tab change, no flicker
- Per-tab search text, subcategory filter and scroll position are remembered
- Theme switch builds the new UI off-screen and swaps it in one step (no blank/rebuild flash, no duplicate ADB polling thread)
- Worker threads no longer touch Tk — results are handed to the UI thread through a queue (fixes stray flicker/minimise during scan)
- Log output is batched; log boxes are capped
- Search is debounced; Select All / Deselect act on the filtered view; Core apps are never bulk-selected
- Actions only run on apps whose state fits (e.g. Re-enable skips already-enabled apps)

#### Fixes in this build
- `reinstall_package` was defined twice in `adb_manager.py`; the weaker second definition silently overrode the correct one and broke re-enabling uninstalled apps
- Built `.exe` now bundles `assets/` (logo shows in the app) and stores `backups/` next to the exe instead of PyInstaller's temp folder (which is wiped on exit)
- Removed leftover Dry Run plumbing from the engine
- README/CHANGELOG corrected (no `--uac-admin`, installer files live in the project root)

#### Settings
- ADB executable path override with Browse + Test ADB
- Backup folder path override
- Open Backup Folder shortcut
- Theme switcher
- Panic Restore shortcut

---

## Planned — Future Versions

### [2.1] — Planned
- [ ] Package size display via `dumpsys package`
- [ ] Search across all tabs simultaneously
- [ ] Export selected package list as `.txt` / shell script
- [ ] Dark/Light auto-detect from Windows system theme
- [ ] App icon (`assets/icon.ico`)

### [2.2] — Planned
- [ ] Wireless ADB support (Android 11+ — no USB needed)
- [ ] Multiple device support — switch between connected devices
- [ ] Custom package list import — paste your own `.txt` list
- [ ] One-click presets — Minimal Clean · Bixby Nuke · Privacy Mode · Deep Clean

### [3.0] — Planned
- [ ] Galaxy Store package integration for wearables awareness
- [ ] Per-device backup profiles
- [ ] Auto-update for package database from GitHub
- [ ] Installer / setup `.exe`
