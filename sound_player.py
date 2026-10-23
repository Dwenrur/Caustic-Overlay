"""Message sound for Caustic Overlay.

Plays WAV, MP3 or WMA files with a volume control, using Windows' built-in
MCI player (winmm.dll) through ctypes, so there's nothing to install.
All calls must come from the Tk thread (MCI devices belong to the thread
that opened them).
"""
from __future__ import annotations

import ctypes
import logging
import shutil
import sys
import time
from pathlib import Path

LOG = logging.getLogger("caustic")
SOUND_TYPES = (".wav", ".mp3", ".wma")


class SoundError(Exception):
    pass


class SoundPlayer:
    ALIAS = "caustic_msg_sound"

    def __init__(self, mci=None):
        """`mci` lets tests pass a fake winmm; on Windows the real one is used."""
        self._send_raw = None
        self._error_text = None
        if mci is not None:
            self._send_raw, self._error_text = mci
        elif sys.platform == "win32":
            from ctypes import wintypes
            winmm = ctypes.WinDLL("winmm")
            send = winmm.mciSendStringW
            send.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
            send.restype = wintypes.DWORD
            get_error = winmm.mciGetErrorStringW
            get_error.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.UINT]
            get_error.restype = wintypes.BOOL
            self._send_raw = lambda cmd, buf, size: send(cmd, buf, size, None)

            def error_text(code):
                buf = ctypes.create_unicode_buffer(256)
                return buf.value if get_error(code, buf, 256) else f"MCI error {code}"
            self._error_text = error_text
        self.loaded: str | None = None
        self._volume: int | None = None
        self._last_play = -1e9
        self.error: str | None = None

    @property
    def available(self) -> bool:
        return self._send_raw is not None

    def _send(self, command: str) -> str:
        buf = ctypes.create_unicode_buffer(256)
        code = self._send_raw(command, buf, 256)
        if code:
            raise SoundError(self._error_text(code))
        return buf.value

    def _load(self, path: str) -> None:
        if self.loaded == path:
            return
        self.close()
        # "mpegvideo" handles WAV, MP3 and WMA, and (unlike "waveaudio") supports volume.
        self._send(f'open "{path}" type mpegvideo alias {self.ALIAS}')
        self.loaded = path
        self._volume = None

    def _set_volume(self, percent: float) -> None:
        level = int(round(max(0.0, min(100.0, float(percent))) * 10))     # MCI volume is 0-1000
        if level != self._volume:
            self._send(f"setaudio {self.ALIAS} volume to {level}")
            self._volume = level

    def play(self, path: str | Path, volume: float, cooldown: float = 0.0) -> bool:
        """Play from the start. Returns False if skipped (cooldown, volume 0) or failed (see .error)."""
        if not self.available or float(volume) <= 0:
            return False
        now = time.monotonic()
        if now - self._last_play < cooldown:
            return False
        try:
            self._load(str(path))
            self._set_volume(volume)
            self._send(f"play {self.ALIAS} from 0")
        except SoundError as exc:
            self.error = f"can't play {Path(str(path)).name}: {exc}"
            LOG.warning("sound: %s", self.error)
            self.close()
            return False
        self._last_play = now
        self.error = None
        return True

    def check(self, path: str | Path) -> str | None:
        """Try to open a file. Returns None if Windows can play it, else the reason."""
        if not self.available:
            return None
        try:
            self._load(str(path))
            return None
        except SoundError as exc:
            self.close()
            return str(exc)

    def close(self) -> None:
        if self.loaded is not None and self.available:
            try:
                self._send(f"close {self.ALIAS}")
            except SoundError:
                pass
        self.loaded = None
        self._volume = None


def import_sound(source: Path, sounds_dir: Path) -> Path:
    """Copy a user's sound into the app's sounds folder (so it keeps working if the
    original is moved). Returns the copy's path; reuses an identical existing copy."""
    sounds_dir.mkdir(parents=True, exist_ok=True)
    target = sounds_dir / source.name
    stem, suffix, n = source.stem, source.suffix, 1
    while target.exists():
        if target.stat().st_size == source.stat().st_size and target.read_bytes() == source.read_bytes():
            return target
        n += 1
        target = sounds_dir / f"{stem} ({n}){suffix}"
    shutil.copyfile(source, target)
    return target
