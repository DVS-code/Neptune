"""Check GitHub Releases for a newer Neptune and install it.

Ported from the ForzaCryptoTool updater (`UpdateService.cs`), which uses the same shape:
ask the GitHub API for the latest release, compare its tag to the running version, and — only if
the user says yes — download the new exe and swap it in via a small detached script that waits for
this process to exit, overwrites the file, and relaunches.

FAIL-SILENT BY DESIGN. Any network, parse or permission problem resolves to "no update". A version
check must never block startup, never raise into the UI, and never stop someone using the tool
offline.

⚠️ The swap only applies to a FROZEN build. Running from source has no single exe to replace, so the
check still reports but the install path is refused and the releases page is opened instead.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request

from neptune import __version__

REPO = 'DVS-code/Neptune'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
RELEASES_PAGE = f'https://github.com/{REPO}/releases/latest'
TIMEOUT_SECONDS = 15


UP_TO_DATE = 'up_to_date'
UPDATE_AVAILABLE = 'update_available'
NO_RELEASES = 'no_releases'
NETWORK_ERROR = 'network_error'


def parse_version(text: str) -> tuple[int, ...] | None:
    """Turn a release tag ('v1.0.2', '1.0.2', '1.1') into a comparable tuple.

    Returns None for anything unparseable so a malformed tag is ignored rather than treated as
    newer than everything.
    """
    if not text:
        return None
    cleaned = text.strip().lstrip('vV')
    cleaned = cleaned.split('-', 1)[0].split('+', 1)[0]
    if not re.fullmatch(r'\d+(\.\d+)*', cleaned):
        return None
    return tuple(int(part) for part in cleaned.split('.'))


def _pad(version: tuple[int, ...], length: int) -> tuple[int, ...]:
    return version + (0,) * (length - len(version))


def is_newer(candidate: tuple[int, ...], current: tuple[int, ...]) -> bool:
    size = max(len(candidate), len(current))
    return _pad(candidate, size) > _pad(current, size)


def frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


class UpdateInfo:
    """A release that is newer than the running build."""

    def __init__(self, version: str, tag: str, notes: str, url: str, asset: str):
        self.version = version
        self.tag = tag
        self.notes = notes
        self.url = url
        self.asset = asset


def check(timeout: int = TIMEOUT_SECONDS) -> tuple[str, UpdateInfo | None]:
    """Ask GitHub for the latest release. Never raises."""
    try:
        request = urllib.request.Request(API_LATEST, headers={

            'User-Agent': f'Neptune/{__version__}',
            'Accept': 'application/vnd.github+json',
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8', 'replace'))
    except Exception:


        return NETWORK_ERROR, None

    if payload.get('draft') or payload.get('prerelease'):
        return NO_RELEASES, None

    tag = payload.get('tag_name') or ''
    latest = parse_version(tag)
    current = parse_version(__version__)
    if latest is None or current is None:
        return NO_RELEASES, None
    if not is_newer(latest, current):
        return UP_TO_DATE, None


    url = name = None
    for asset in payload.get('assets') or []:
        asset_name = asset.get('name') or ''
        asset_url = asset.get('browser_download_url') or ''
        if asset_name.lower().endswith('.exe'):
            url, name = asset_url, asset_name
            break
        if url is None and asset_name.lower().endswith('.zip'):
            url, name = asset_url, asset_name
    if not url or not name:
        return NO_RELEASES, None

    return UPDATE_AVAILABLE, UpdateInfo(
        '.'.join(str(part) for part in latest), tag,
        payload.get('body') or '', url, name)


def check_async(callback) -> None:
    """Run `check` off the interface thread and hand the result back.

    ⚠️ `callback` is invoked on a WORKER thread. A Qt caller must marshal onto the GUI thread
    before touching widgets.
    """
    def run():
        try:
            callback(*check())
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name='neptune-update-check').start()


def open_releases_page() -> None:
    try:
        import webbrowser
        webbrowser.open(RELEASES_PAGE)
    except Exception:
        pass


def download_and_apply(info: UpdateInfo, progress=None) -> bool:
    """Download the new exe and stage the swap. Returns True when the app should now exit.

    The running exe cannot overwrite itself while it is running, so a detached script waits for this
    process to exit, copies the new file over the old one, relaunches, and deletes itself.
    """
    if not frozen() or not info.asset.lower().endswith('.exe'):

        open_releases_page()
        return False

    try:
        current = os.path.abspath(sys.executable)
        folder = os.path.dirname(current)
        staged = os.path.join(folder, f'Neptune.update-{info.version}.exe')

        request = urllib.request.Request(info.url, headers={
            'User-Agent': f'Neptune/{__version__}',
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get('Content-Length') or 0)
            done = 0
            with open(staged, 'wb') as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None and total > 0:
                        try:
                            progress(int(done * 100 / total))
                        except Exception:
                            pass

        if not os.path.isfile(staged) or os.path.getsize(staged) == 0:
            return False

        _write_swap_script(current, staged)
        return True
    except Exception:
        return False


def _write_swap_script(current: str, staged: str) -> None:
    """A detached .cmd that waits for us to exit, swaps the exe, relaunches, then self-deletes."""
    pid = os.getpid()
    handle, path = tempfile.mkstemp(prefix='neptune_update_', suffix='.cmd')
    os.close(handle)
    body = (
        '@echo off\r\n'
        ':waitloop\r\n'
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
        'if not errorlevel 1 (\r\n'
        '  ping -n 2 127.0.0.1 >nul\r\n'
        '  goto waitloop\r\n'
        ')\r\n'
        'set /a tries=0\r\n'
        ':copyloop\r\n'
        f'copy /y "{staged}" "{current}" >nul\r\n'
        'if errorlevel 1 (\r\n'
        '  set /a tries+=1\r\n'
        '  if %tries% lss 10 ( ping -n 2 127.0.0.1 >nul & goto copyloop )\r\n'
        ')\r\n'
        f'del /q "{staged}" >nul 2>&1\r\n'
        f'start "" "{current}"\r\n'
        'del /q "%~f0" >nul 2>&1\r\n'
    )
    with open(path, 'w', encoding='ascii', errors='replace') as script:
        script.write(body)

    creation = 0x08000000 | 0x00000008
    subprocess.Popen(['cmd.exe', '/c', path], creationflags=creation,
                     close_fds=True)
