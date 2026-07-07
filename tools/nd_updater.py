"""nd_updater.py — in-app update for the frozen Windows exe.

The exe is distributed via GitHub Releases (a one-file `KiCad Manager.exe` per `v*`
tag; see `.github/workflows/build-exe.yml`). This module lets a running exe notice a
newer release, download it, and swap itself out — so the user never has to manually
re-download and reinstall.

Design:
  * Pure, side-effect-free helpers (`parse_version`, `is_newer`, `pick_asset`,
    `check_for_update` given an injected fetcher) so the logic is unit-tested headless.
  * The network + Windows process bits are thin, guarded, and best-effort: any failure
    returns None / False and never blocks or crashes launch.
  * A running one-file exe cannot overwrite itself on Windows, so `apply_update_windows`
    hands the swap to a detached `cmd` that waits for THIS process to exit, moves the
    new exe over the old one, relaunches it, and deletes itself.

Private-repo support: if a token is available (env `GITHUB_TOKEN`/`GH_TOKEN`, or a baked
`app_secrets.GITHUB_TOKEN_DEFAULT`) it is sent on both the API call and the asset
download (via the API asset URL with an octet-stream accept), so private releases work.
Public releases need no token.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.request import Request, urlopen

try:                                    # build identity (CI-stamped); safe in dev
    from app_build import VERSION as _BUILD_VERSION, REPO as _REPO, ASSET_NAME as _ASSET
except Exception:                       # noqa: BLE001
    _BUILD_VERSION, _REPO, _ASSET = "dev", "s-haidari/Hardware", "KiCad Manager.exe"

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
_UA = "KiCadManager-Updater"
_TIMEOUT = 12


# ── pure helpers (unit-tested) ────────────────────────────────────────────────
def current_version() -> str:
    """The version baked into this build ("dev" in a source checkout)."""
    return _BUILD_VERSION


def parse_version(s: str) -> Tuple[int, ...]:
    """A lenient numeric version tuple. Strips a leading 'v', splits on '.', and reads
    the leading digits of each segment (so 'v2.1.0', '2.1', '2.1.0-rc1' all parse).
    A non-numeric / empty string parses to (0, 0, 0) — lower than any real release."""
    s = (s or "").strip().lstrip("vV")
    parts = []
    for tok in s.split("."):
        digits = ""
        for ch in tok:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(latest: str, current: str) -> bool:
    """True when `latest` is a strictly newer version than `current`. A `current` of
    'dev' (or anything non-numeric) is treated as 0.0.0, so a dev build always sees a
    real release as newer — but see `check_for_update`, which skips the auto-nag in dev."""
    return parse_version(latest) > parse_version(current)


def pick_asset(release: dict, name: str = _ASSET) -> Tuple[Optional[str], Optional[str], int]:
    """From a GitHub release JSON, return (browser_download_url, api_asset_url, size)
    for the asset named `name`, or (None, None, 0) if absent."""
    for a in (release.get("assets") or []):
        if a.get("name") == name:
            return a.get("browser_download_url"), a.get("url"), int(a.get("size") or 0)
    return None, None, 0


def evaluate_release(release: dict, current: str, asset_name: str = _ASSET) -> Optional[dict]:
    """Pure decision step: given a release JSON and the current version, return the
    update descriptor if it is newer and carries the asset, else None."""
    tag = release.get("tag_name") or ""
    if not tag or not is_newer(tag, current):
        return None
    dl, api_url, size = pick_asset(release, asset_name)
    if not dl and not api_url:
        return None
    return {"version": tag, "url": dl, "api_url": api_url, "size": size,
            "notes": (release.get("body") or "").strip()}


# ── network (best-effort) ─────────────────────────────────────────────────────
def _token() -> Optional[str]:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip() or None
    try:
        import app_secrets
        val = getattr(app_secrets, "GITHUB_TOKEN_DEFAULT", None)
        return (val or None) if isinstance(val, str) else None
    except Exception:  # noqa: BLE001
        return None


def _get_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": _UA}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=_TIMEOUT) as r:  # noqa: S310 (trusted GitHub host)
        return json.loads(r.read().decode("utf-8"))


def check_for_update(current: Optional[str] = None, *,
                     fetch: Optional[Callable[[str], dict]] = None,
                     allow_dev: bool = False) -> Optional[dict]:
    """Return an update descriptor {version,url,api_url,size,notes} if the latest
    release is newer than `current`, else None. Best-effort: any error returns None.

    `fetch` is an injection point for tests (defaults to the live GitHub API). In a dev
    checkout (`current == 'dev'`) the auto-check is skipped unless `allow_dev=True`, so
    developers are never nagged, while a manual "Check for updates" can pass allow_dev."""
    cur = current or current_version()
    if cur == "dev" and not allow_dev:
        return None
    getter = fetch or _get_json
    try:
        release = getter(API_LATEST.format(repo=_REPO))
    except Exception:  # noqa: BLE001 — offline / rate-limited / private without token
        return None
    if not isinstance(release, dict):
        return None
    return evaluate_release(release, cur)


def download(update: dict, dest: Path,
             progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """Stream the release asset to `dest`. Prefers the authenticated API asset URL when
    a token is present (works for private repos), else the public download URL. Calls
    `progress(done, total)` as bytes arrive. Raises on failure (caller guards)."""
    tok = _token()
    if tok and update.get("api_url"):
        url = update["api_url"]
        headers = {"Accept": "application/octet-stream",
                   "Authorization": f"Bearer {tok}", "User-Agent": _UA}
    else:
        url = update["url"]
        headers = {"User-Agent": _UA}
    if not url:
        raise ValueError("update descriptor has no download URL")
    dest = Path(dest)
    total = int(update.get("size") or 0)
    done = 0
    req = Request(url, headers=headers)
    with urlopen(req, timeout=_TIMEOUT * 4) as r, open(dest, "wb") as f:  # noqa: S310
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dest


# ── apply (Windows one-file self-replace) ─────────────────────────────────────
def exe_path() -> Optional[Path]:
    """The running one-file exe path, or None when not frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def staged_path(target: Optional[Path] = None) -> Path:
    """Where a downloaded update is staged before the swap (a sibling of the exe, or a
    temp file in dev so tests never need a frozen exe)."""
    target = target or exe_path()
    if target is not None:
        return target.with_name(target.name + ".new")
    return Path(tempfile.gettempdir()) / "KiCad Manager.exe.new"


def _swap_script(pid: int, new_exe: Path, target: Path) -> str:
    """The detached batch that waits for pid to exit, replaces the exe, relaunches it,
    and deletes itself. Kept pure so it can be unit-tested."""
    return (
        "@echo off\r\n"
        ":waitloop\r\n"
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >NUL\r\n"
        "  goto waitloop\r\n"
        ")\r\n"
        f'move /Y "{new_exe}" "{target}" >NUL\r\n'
        f'start "" "{target}"\r\n'
        'del "%~f0"\r\n'
    )


def apply_update_windows(new_exe: Path, target: Optional[Path] = None,
                         pid: Optional[int] = None) -> bool:
    """Launch a detached helper that swaps `new_exe` over the running exe once this
    process exits, then relaunches. Returns True when the helper was launched (the
    caller should then quit the app). Windows-only; returns False elsewhere or when not
    frozen (a running exe can't overwrite itself in place)."""
    target = target or exe_path()
    if target is None or os.name != "nt" or not Path(new_exe).exists():
        return False
    pid = os.getpid() if pid is None else pid
    bat = Path(tempfile.gettempdir()) / f"km_update_{pid}.bat"
    try:
        bat.write_text(_swap_script(pid, Path(new_exe), Path(target)), encoding="utf-8")
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP so the helper outlives us.
        flags = 0x00000008 | 0x00000200
        subprocess.Popen(["cmd", "/c", str(bat)],  # noqa: S603,S607
                         creationflags=flags, close_fds=True)
        return True
    except Exception:  # noqa: BLE001
        return False
