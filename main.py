"""Caustic Overlay - Windows desktop overlay (v17).

Merges Twitch, Kick and YouTube chat into one transparent overlay.

v17 changes:
- Messages can fade out after a delay (Settings -> Appearance -> Fade after;
  default 30 s, "Never" turns it off). Optionally the background panel fades
  too, so the overlay disappears completely while chat is quiet.

v16.1 changes:
- New icon: a green, dripping "C" (window, taskbar, tray, installer, desktop shortcut).

v16 changes:
- New name: Caustic Overlay (was MultiChat Overlay). The installer removes the old
  MultiChat Overlay program, and settings + sounds move over automatically.

v15 changes:
- Can be built into a real Windows program + installer (build_installer.bat).
  When installed, settings, the log and your own sounds live in
  %APPDATA%\\MultiChat Overlay, so they survive updates and uninstalling.
- Only one copy runs at a time: starting it again brings the open one forward.
- Crashes inside the app are written to connection_log.txt.

v14 changes:
- Message sound: plays when new chat arrives. Built-in chime, or your own
  WAV / MP3 / WMA file. Volume, on/off and "at most one sound every N seconds"
  in Settings -> Sound. Tray menu has a quick "Message sound" on/off.

v13.1 fixes:
- Kick: Kick changed its chat key in mid-October 2026, so it refused the old
  one and closed the connection. Now asks Kick for the current key on each
  connect (built-in fallback updated), and shows Kick's real reason if it refuses.
- YouTube: the footer now says why it isn't connected (e.g. "not live yet,
  checks every minute"), and live detection is sturdier.
- connection_log.txt (next to main.py) records every connection step, so
  problems can be diagnosed from one file.

v13 changes:
- Live chat from Twitch, Kick and YouTube (chat_sources.py). Built-in Python
  only, no logins, no API keys. Set channels in Settings -> Connections.
- Header shows each platform's connection state; problems appear in the footer.
- Messages deleted by moderators (or from banned/timed-out users) disappear.
- YouTube gets its own colour (Settings -> Appearance).

v12.2 changes:
- Resize grip: an 18 px square in the bottom-right corner (three diagonal
  lines). Grab it to make the window wider and taller at once. It hands off to
  Windows' own resize, so it behaves exactly like the frame corner. Hidden in
  chat-only mode, since the window can't be clicked then anyway.

v12.1 fixes:
- "Show border" crashed with "name 'wintypes' is not defined" halfway through
  restoring the frame, which left the title bar drawn but not clickable.
  Fixed the missing import; if a restore step ever fails again, Windows is
  still told to rebuild the frame, so the title bar stays clickable.
- Border problems are reported as "Border error", not as a click-through error.

v12 changes:
- Chat-only mode: turning click-through on hides the Windows title bar and
  edges, the purple outline, the header and the footer, so only the chat shows.
  The chat stays in the same spot on screen.
- System tray icon (bottom-right, by the clock). Right-click it for:
  Show border (on/off), Settings..., Exit. Double-click it to show the border.
  "Show border" turns click-through off, so the window can be moved again.
- App icon for the window, taskbar button and tray.

v11 changes:
- Text stays fully solid at any opacity. On Windows the overlay is two windows:
    * a click-through "backdrop" window behind, which draws the panel and the
      chat cards at the chosen opacity, and
    * the main window in front, whose background colour is keyed out
      (Tk's -transparentcolor) so only text, borders and buttons are drawn.
  If the colour key isn't available, it falls back to v10's whole-window fade.
- No more flashing when changing settings. Chat is drawn on a single Canvas
  (drawn off-screen, then shown in one go) instead of destroying and rebuilding
  dozens of Label widgets, and OS window attributes are only touched when
  their value actually changes.

v10 changes:
- Newest messages are anchored to the bottom; overflow drops the OLDEST.
- Click-through targets Tk's real top-level frame window (wm_frame).
- One redraw per slider tick; whole-number slider labels.
- Hotkey failure shown in the footer; hotkey thread shuts down cleanly.
- Color picker opens on top of the Settings window.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from ctypes import wintypes   # module-wide, so every Win32Window method can use it
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
import webbrowser
from tkinter import colorchooser, filedialog, messagebox, ttk
from pathlib import Path

APP_NAME = "Caustic Overlay"
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
import chat_sources  # noqa: E402  (lives next to this file)
import sound_player  # noqa: E402
# Two kinds of files:
# - RESOURCE_DIR: files that ship with the app (icon, built-in chime). Read-only.
# - DATA_DIR: files the app writes (settings, log, your own sounds).
# Run from the folder (run.bat): both are this folder, exactly as before.
# Installed .exe: resources are inside the program; data goes to %APPDATA%\Caustic Overlay,
# which is always writable and isn't touched by updates or uninstalling.
FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) if FROZEN else BASE_DIR
if FROZEN:
    _APPDATA = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    DATA_DIR = _APPDATA / "Caustic Overlay"
    OLD_DATA_DIR = _APPDATA / "MultiChat Overlay"        # where v15 (old name) kept its data
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    DATA_DIR = BASE_DIR
    OLD_DATA_DIR = None
CONFIG_PATH = DATA_DIR / "settings.json"
MIGRATED_MARKER = ".moved-from-multichat-overlay"
ICON_PATH = RESOURCE_DIR / "icon.ico"
APP_ID = "CausticOverlay.App"
APP_VERSION = "v17"
LOG_PATH = DATA_DIR / "connection_log.txt"
SOUNDS_DIR = DATA_DIR / "sounds"                    # your own sounds are copied here
BUILTIN_SOUND = RESOURCE_DIR / "sounds" / "chime.wav"

DEFAULTS = {
    "style": "custom",              # custom or minimal
    "opacity": 0.88,                 # background opacity 0.00 - 1.00 (text stays solid)
    "font_family": "Segoe UI",
    "font_size": 11,
    "text_color": "#F4F4F5",
    "panel_color": "#171923",
    "accent_color": "#A78BFA",
    "twitch_color": "#BF94FF",
    "kick_color": "#53FC18",
    "youtube_color": "#FF5A5F",
    "message_spacing": 8,
    "max_messages": 15,
    "window_width": 360,
    "window_height": 520,
    "window_x": 40,
    "window_y": 80,
    "always_on_top": True,
    "click_through": False,
    "show_timestamps": False,
    "show_platform_labels": True,
    "twitch_channel": "",
    "kick_channel": "",
    "youtube_channel": "",          # @handle, channel link, or video link
    "kick_chatroom_id": "",         # filled automatically, or pasted if kick.com blocks the lookup
    "kick_chatroom_slug": "",       # which Kick channel the saved chatroom ID belongs to
    "kick_pusher_key": "",          # blank = built-in default; only change if Kick changes it
    "sound_enabled": True,          # play a sound when new chat arrives
    "sound_volume": 60,             # 0-100 %
    "sound_file": "",               # "" = built-in chime; else a path (relative to this folder if inside it)
    "sound_cooldown": 1.0,          # seconds: at most one sound this often, so busy chat isn't a drumroll
    "fade_after": 30,               # seconds a message stays before fading out; 0 = never fade
    "fade_background": False,       # also fade the panel when every message has faded
}

# Everything platform-specific the UI needs, in display order.
PLATFORMS = {
    "twitch":  {"name": "Twitch",  "label": "TWITCH",  "short": "T", "color_key": "twitch_color",  "channel_key": "twitch_channel"},
    "kick":    {"name": "Kick",    "label": "KICK",    "short": "K", "color_key": "kick_color",    "channel_key": "kick_channel"},
    "youtube": {"name": "YouTube", "label": "YOUTUBE", "short": "Y", "color_key": "youtube_color", "channel_key": "youtube_channel"},
}
CONNECTION_KEYS = ("twitch_channel", "kick_channel", "youtube_channel", "kick_chatroom_id", "kick_pusher_key")
STATUS_GLYPH = {"connecting": "…", "live": "●", "waiting": "○", "error": "✕"}
ERROR_COLOR = "#F87171"
SETTINGS_SIZE = (430, 760)
MAX_STORED_MESSAGES = 200
FADE_SECONDS = 1.5                  # how long the fade-out itself takes
FADE_FRAME = 0.06                   # seconds between fade animation frames (~16 fps)

CARD_COLOR = "#202330"
MUTED_COLOR = "#A1A1AA"
CARD_PADX, CARD_PADY = 7, 5

IS_WINDOWS = sys.platform == "win32"
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000          # title bar + thin border
WS_THICKFRAME = 0x00040000       # resize edges
FRAME_BITS = WS_CAPTION | WS_THICKFRAME
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
WM_NCLBUTTONDOWN = 0x00A1
HTBOTTOMRIGHT = 17
GRIP_SIZE = 18                   # px, the resize grip square in the bottom-right corner
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 0x4D43


class Win32Window:
    """Thin ctypes wrapper for the few window calls the overlay needs.

    Uses the pointer-sized Get/SetWindowLongPtrW on 64-bit Python and declares
    argument types so handles aren't truncated.
    """
    def __init__(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._get = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        self._set = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        self._get.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get.restype = ctypes.c_ssize_t
        self._set.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._set.restype = ctypes.c_ssize_t
        self._layered = user32.SetLayeredWindowAttributes
        # wintypes.BYTE is signed in ctypes; the Win32 BYTE alpha is 0-255, so use c_ubyte.
        self._layered.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
        self._layered.restype = wintypes.BOOL
        self._setpos = user32.SetWindowPos
        self._setpos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self._setpos.restype = wintypes.BOOL
        self._adjust = user32.AdjustWindowRectEx
        self._adjust.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._adjust.restype = wintypes.BOOL
        self._client_rect = user32.GetClientRect
        self._client_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._client_rect.restype = wintypes.BOOL
        self._to_screen = user32.ClientToScreen
        self._to_screen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self._to_screen.restype = wintypes.BOOL
        self._release_capture = user32.ReleaseCapture
        self._release_capture.argtypes = []
        self._release_capture.restype = wintypes.BOOL
        self._post = user32.PostMessageW
        self._post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._post.restype = wintypes.BOOL

    def get_exstyle(self, hwnd: int) -> int:
        return int(self._get(hwnd, GWL_EXSTYLE))

    def set_exstyle(self, hwnd: int, value: int) -> None:
        self._set(hwnd, GWL_EXSTYLE, value)

    def get_style(self, hwnd: int) -> int:
        return int(self._get(hwnd, GWL_STYLE)) & 0xFFFFFFFF

    def set_style(self, hwnd: int, value: int) -> None:
        self._set(hwnd, GWL_STYLE, value)

    def client_rect_on_screen(self, hwnd: int) -> tuple[int, int, int, int]:
        """(x, y, width, height) of the window's client area in screen coordinates."""
        rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
        self._client_rect(hwnd, ctypes.byref(rect))
        self._to_screen(hwnd, ctypes.byref(origin))
        return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top

    def outer_rect_for_client(self, x: int, y: int, w: int, h: int, style: int, exstyle: int) -> tuple[int, int, int, int]:
        """Outer window rect (x, y, w, h) that puts the client area exactly at (x, y, w, h)."""
        rect = wintypes.RECT(x, y, x + w, y + h)
        self._adjust(ctypes.byref(rect), style, False, exstyle)
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def move_resize(self, hwnd: int, x: int, y: int, w: int, h: int, frame_changed: bool = False) -> None:
        flags = SWP_NOZORDER | SWP_NOACTIVATE | (SWP_FRAMECHANGED if frame_changed else 0)
        if not self._setpos(hwnd, None, x, y, w, h, flags):
            raise ctypes.WinError(ctypes.get_last_error())

    def refresh_frame(self, hwnd: int) -> None:
        """Make Windows re-read the window style and rebuild the title bar/edges in place.

        Without this, a changed style is drawn but its title bar buttons don't respond.
        """
        self._setpos(hwnd, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def begin_corner_resize(self, hwnd: int, x_root: int, y_root: int) -> None:
        """Start Windows' own bottom-right resize, exactly as if the frame corner was grabbed.

        Tk captured the mouse on the button press, so release it first; then tell
        the window the press happened on its bottom-right resize corner.
        """
        self._release_capture()
        lparam = ((y_root & 0xFFFF) << 16) | (x_root & 0xFFFF)   # MAKELPARAM(x, y)
        if not self._post(hwnd, WM_NCLBUTTONDOWN, HTBOTTOMRIGHT, lparam):
            raise ctypes.WinError(ctypes.get_last_error())

    def set_layered(self, hwnd: int, alpha: float, colorkey: tuple[int, int, int] | None = None) -> None:
        """Set alpha (and optionally a colour key) on a layered window.

        A layered window is not drawn at all until this has been called.
        Passing only LWA_ALPHA turns an existing colour key OFF, so the key
        must be passed again whenever alpha is set on a keyed window.
        """
        flags, cref = LWA_ALPHA, 0
        if colorkey is not None:
            r, g, b = colorkey
            cref = r | (g << 8) | (b << 16)       # COLORREF is 0x00BBGGRR
            flags |= LWA_COLORKEY
        byte_alpha = max(0, min(255, int(round(alpha * 255))))
        if not self._layered(hwnd, cref, byte_alpha, flags):
            raise ctypes.WinError(ctypes.get_last_error())

    def place_below(self, hwnd: int, above_hwnd: int) -> None:
        """Put hwnd directly underneath above_hwnd in the z-order, without activating it."""
        self._setpos(hwnd, above_hwnd, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


_win32: Win32Window | None = None


def win32() -> Win32Window:
    global _win32
    if _win32 is None:
        _win32 = Win32Window()
    return _win32


def update_exstyle(hwnd: int, add: int = 0, remove: int = 0) -> int:
    """Change extended style bits, skipping the OS call if nothing would change."""
    api = win32()
    old = api.get_exstyle(hwnd)
    new = (old | add) & ~remove
    if new != old:
        api.set_exstyle(hwnd, new)
    return new


def load_settings() -> dict:
    settings = DEFAULTS.copy()
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                settings.update({k: v for k, v in loaded.items() if k in DEFAULTS})
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def save_settings(settings: dict) -> None:
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError as exc:
        messagebox.showerror(APP_NAME, f"Could not save settings:\n{exc}")


class GlobalHotkey(threading.Thread):
    """Registers Ctrl+Shift+X globally on Windows to toggle click-through."""
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self.ready = threading.Event()
        self.registered = False
        self.thread_id = None

    def run(self):
        if not IS_WINDOWS:
            self.ready.set()
            return
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self.registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, ord("X")))
        self.ready.set()
        if not self.registered:
            return
        msg = ctypes.wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:  # 0 = WM_QUIT (sent by stop()), -1 = error
                break
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.callback()
        # A hotkey registered with hwnd=NULL can only be unregistered by the thread that registered it.
        user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        """Ask the hotkey thread to exit its message loop (safe to call from any thread)."""
        if IS_WINDOWS and self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """Full (Vista+) layout of the struct Shell_NotifyIconW takes."""
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("hWnd", ctypes.wintypes.HWND),
        ("uID", ctypes.wintypes.UINT),
        ("uFlags", ctypes.wintypes.UINT),
        ("uCallbackMessage", ctypes.wintypes.UINT),
        ("hIcon", ctypes.wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.wintypes.DWORD),
        ("dwStateMask", ctypes.wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.wintypes.UINT),          # union with uTimeout
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", ctypes.wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("style", ctypes.wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HICON),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm", ctypes.wintypes.HICON),
    ]


class TrayIcon(threading.Thread):
    """Notification-area (system tray) icon with a right-click menu.

    Runs its own hidden window + message loop on a background thread. It never
    touches Tk directly: menu choices are handed to `on_command(name)`, which
    must only queue them for the Tk thread.

    Commands: "show_border", "hide_border", "settings", "exit".
    """
    WM_TRAY = 0x8000 + 1                    # WM_APP + 1: our callback message
    ID_BORDER, ID_SETTINGS, ID_EXIT, ID_SOUND = 1001, 1002, 1003, 1004

    def __init__(self, on_command, border_hidden, icon_path: Path, tooltip: str = APP_NAME, sound_on=None):
        super().__init__(daemon=True)
        self.on_command = on_command
        self.border_hidden = border_hidden  # callable -> bool, read when the menu opens
        self.sound_on = sound_on            # callable -> bool, or None for no sound item
        self.icon_path = icon_path
        self.tooltip = tooltip
        self.ready = threading.Event()
        self.ok = False
        self.error: str | None = None
        self.hwnd = None

    def run(self):
        try:
            self._run()
        except Exception as exc:  # never let a tray problem take the overlay down
            self.error = str(exc) or exc.__class__.__name__
        finally:
            self.ready.set()

    def stop(self, timeout: float = 1.0):
        """Remove the icon and end the thread (safe to call from any thread)."""
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE
            self.join(timeout)

    def _run(self):
        from ctypes import wintypes as w
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = w.ATOM
        user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        user32.CreateWindowExW.restype = w.HWND
        user32.DestroyWindow.argtypes = [w.HWND]
        user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
        user32.LoadImageW.restype = w.HANDLE
        user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPVOID]
        user32.LoadIconW.restype = w.HICON
        user32.DestroyIcon.argtypes = [w.HICON]
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
        user32.RegisterWindowMessageW.restype = w.UINT
        user32.CreatePopupMenu.restype = w.HMENU
        user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
        user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID]
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.DestroyMenu.argtypes = [w.HMENU]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        user32.SetForegroundWindow.argtypes = [w.HWND]
        user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        user32.GetMessageW.restype = w.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = w.BOOL
        kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel32.GetModuleHandleW.restype = w.HMODULE

        NIM_ADD, NIM_DELETE = 0, 2
        NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
        WM_DESTROY, WM_CLOSE, WM_NULL = 0x0002, 0x0010, 0x0000
        WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0203, 0x0205, 0x007B
        MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
        TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
        SM_CXSMICON, SM_CYSMICON = 49, 50
        IDI_APPLICATION = 32512

        hinst = kernel32.GetModuleHandleW(None)
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        hicon, own_icon = None, False
        if self.icon_path.exists():
            hicon = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON,
                                      user32.GetSystemMetrics(SM_CXSMICON),
                                      user32.GetSystemMetrics(SM_CYSMICON), LR_LOADFROMFILE)
            own_icon = bool(hicon)
        if not hicon:
            hicon = user32.LoadIconW(None, IDI_APPLICATION)

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self.WM_TRAY
        nid.hIcon = hicon
        nid.szTip = self.tooltip[:127]

        def add_icon():
            return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

        def show_menu(hwnd):
            hidden = bool(self.border_hidden())
            menu = user32.CreatePopupMenu()
            try:
                user32.AppendMenuW(menu, MF_STRING | (0 if hidden else MF_CHECKED), self.ID_BORDER, "Show border")
                if self.sound_on is not None:
                    user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.sound_on() else 0),
                                       self.ID_SOUND, "Message sound")
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING, self.ID_SETTINGS, "Settings…")
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING, self.ID_EXIT, "Exit")
                pt = w.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                # Needed so the menu closes when you click elsewhere (Microsoft KB135788).
                user32.SetForegroundWindow(hwnd)
                choice = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                                               pt.x, pt.y, 0, hwnd, None)
                user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            finally:
                user32.DestroyMenu(menu)
            if choice == self.ID_BORDER:
                self.on_command("show_border" if hidden else "hide_border")
            elif choice == self.ID_SOUND:
                self.on_command("toggle_sound")
            elif choice == self.ID_SETTINGS:
                self.on_command("settings")
            elif choice == self.ID_EXIT:
                self.on_command("exit")

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg == self.WM_TRAY:
                    event = lparam & 0xFFFF
                    if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                        show_menu(hwnd)
                    elif event == WM_LBUTTONDBLCLK:
                        self.on_command("show_border")
                    return 0
                if msg == taskbar_created and taskbar_created:
                    add_icon()  # Explorer restarted: put the icon back
                    return 0
                if msg == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)   # keep a reference or Python frees the callback
        class_name = "CausticOverlayTray"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:  # 1410 = already registered
            raise ctypes.WinError(ctypes.get_last_error())
        # A hidden top-level window (never shown) receives the tray messages.
        hwnd = user32.CreateWindowExW(0, class_name, APP_NAME + " tray", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = hwnd
        nid.hWnd = hwnd
        if not add_icon():
            user32.DestroyWindow(hwnd)
            self.hwnd = None
            raise RuntimeError("Windows refused to add the tray icon")
        self.ok = True
        self.ready.set()

        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None
        if own_icon:
            user32.DestroyIcon(hicon)
        user32.UnregisterClassW(class_name, hinst)


class CausticOverlay:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        # Always launch interactive so the overlay cannot accidentally trap the user in click-through mode.
        self.settings["click_through"] = False
        self.persisted_settings = self.settings.copy()
        self.messages: list[dict] = []
        self.settings_window: tk.Toplevel | None = None
        self.drag_origin = None
        # Commands from the hotkey and tray threads (strings) and chat events from
        # the connection threads (tuples). Tk isn't thread-safe, so background
        # threads only queue things here and the Tk thread acts on them.
        self._ui_queue: queue.Queue = queue.Queue()
        self.sources: dict[str, chat_sources.ChatSource] = {}
        self.conn_status: dict[str, tuple[str, str]] = {}   # platform -> (state, detail)
        self._conn_generation = 0
        self._showing_demo = False
        self.sound = sound_player.SoundPlayer()
        self._new_chat = 0                  # chat messages accepted since the last queue drain
        self._settings_sound_var: tk.BooleanVar | None = None
        self.tray: TrayIcon | None = None
        self._chrome_hidden = False          # chat-only mode (frame/outline/header/footer hidden)
        self._saved_frame_bits = 0
        self._border_error: str | None = None
        self._clickthrough_requested = False
        self._clickthrough_error: str | None = None
        self._preview_after_id = None
        self._resize_after_id = None
        self.hotkey: GlobalHotkey | None = None
        self._setup_done = False
        # Last values pushed to the OS / widgets. Re-sending identical values is
        # what made v10 flash, so everything is compared against this first.
        self._applied: dict = {}
        self._card_rects: list[tuple[int, int, int, int]] = []
        self._card_colors: list[str] = []
        self._mix_cache: dict = {}
        self._fade_after_id = None
        self._backdrop_alpha_now: float | None = None
        self._font_cache: dict = {}
        self._item_seq = 0
        self.backdrop: tk.Toplevel | None = None
        self.backdrop_canvas: tk.Canvas | None = None
        self._backdrop_visible = False
        self._backdrop_geo = None

        self.root.title(APP_NAME)
        self.root.geometry(self._geometry())
        self.root.minsize(260, 240)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if IS_WINDOWS and ICON_PATH.exists():
            try:
                self.root.iconbitmap(default=str(ICON_PATH))   # title bar + taskbar icon
            except tk.TclError:
                pass

        # Native Windows frame for reliable resizing and hit-testing.
        self.root.overrideredirect(False)
        self.root.resizable(True, True)

        # Two-window mode needs Tk's -transparentcolor, which exists on Windows only.
        self.two_window = IS_WINDOWS and self._try_transparent_key()

        self._build_ui()
        if self.two_window:
            self._build_backdrop()
        self._apply_window_attributes()
        if not self._configured_platforms():
            self._load_demo_messages()      # nothing to connect to yet: show sample chat

        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.bind("<Map>", self._on_root_map, add="+")
        self.root.bind("<Unmap>", self._on_root_unmap, add="+")
        self.root.bind("<FocusIn>", lambda e: self._restack_backdrop(), add="+")
        self.root.after(150, self._finish_window_setup)
        self.root.after(100, self._poll_ui_queue)

        if IS_WINDOWS:
            self.hotkey = GlobalHotkey(lambda: self._ui_queue.put("toggle"))
            self.hotkey.start()
            self.tray = TrayIcon(self._ui_queue.put, lambda: self._chrome_hidden, ICON_PATH,
                                 sound_on=lambda: bool(self.settings.get("sound_enabled")))
            self.tray.start()

    # ------------------------------------------------------------------ colours

    def _geometry(self) -> str:
        s = self.settings
        return f"{int(s['window_width'])}x{int(s['window_height'])}+{int(s['window_x'])}+{int(s['window_y'])}"

    def _opacity(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.settings["opacity"])))
        except (ValueError, TypeError):
            return 0.88

    def _rgb(self, color: str) -> tuple[int, int, int]:
        r, g, b = self.root.winfo_rgb(color)       # 16-bit channels; accepts names too
        return r >> 8, g >> 8, b >> 8

    def _key_color(self) -> str:
        """Colour that Windows makes fully transparent in the front window.

        It's the panel colour nudged by one step of blue. Anti-aliased text edges
        blend toward it, so they blend toward (almost) the panel colour, and no
        visible fringe appears. Anything drawn in the exact panel colour (e.g. the
        header buttons) stays solid, because it doesn't match the key.
        """
        r, g, b = self._rgb(self.settings["panel_color"])
        b = b + 1 if b < 255 else b - 1
        return f"#{r:02x}{g:02x}{b:02x}"

    def _surface_color(self) -> str:
        """Background for the front window: keyed out in two-window mode, else the panel."""
        return self._key_color() if self.two_window else self.settings["panel_color"]

    def _try_transparent_key(self) -> bool:
        try:
            self.root.attributes("-transparentcolor", self._key_color())
            return True
        except tk.TclError:
            return False

    # ----------------------------------------------------------------------- UI

    def _build_ui(self):
        surface = self._surface_color()
        panel = self.settings["panel_color"]
        accent = self.settings["accent_color"]
        text = self.settings["text_color"]
        self.root.configure(bg=surface)
        self.outer = tk.Frame(self.root, bg=surface, bd=0, highlightthickness=1,
                              highlightbackground=accent, highlightcolor=accent)
        self.outer.pack(fill="both", expand=True)

        self.header = tk.Frame(self.outer, bg=surface, height=34)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        self.title_label = tk.Label(self.header, text="CAUSTIC", bg=surface,
                                    fg=accent, font=("Segoe UI", 9, "bold"), anchor="w")
        self.title_label.pack(side="left", padx=(10, 6), fill="y")
        # Connection status: one small label per platform, e.g. "TWITCH ●".
        self.status_label = tk.Frame(self.header, bg=surface)
        self.status_label.pack(side="left", fill="both", expand=True)
        self.status_row = tk.Frame(self.status_label, bg=surface)
        self.status_row.pack(side="right", fill="y")
        self.status_items: dict[str, tk.Label] = {}
        for platform, info in PLATFORMS.items():
            label = tk.Label(self.status_row, text="", bg=surface, fg=MUTED_COLOR, font=("Segoe UI", 7, "bold"))
            self.status_items[platform] = label
        self._refresh_status_labels()
        # Buttons use the exact panel colour (not the key), so they stay solid and
        # clickable everywhere, not just on the glyph's pixels.
        self.settings_button = tk.Button(self.header, text="⚙", command=self.open_settings, bd=0,
                                         bg=panel, fg=text, activebackground=accent,
                                         relief="flat", cursor="hand2")
        self.settings_button.pack(side="right", padx=(0, 4), pady=3)
        self.close_button = tk.Button(self.header, text="×", command=self.close, bd=0,
                                      bg=panel, fg=text, activebackground="#7F1D1D",
                                      relief="flat", cursor="hand2")
        self.close_button.pack(side="right", padx=(0, 6), pady=3)

        self.divider = tk.Frame(self.outer, bg=accent, height=1)
        self.divider.pack(fill="x")

        # Footer is packed BEFORE the chat area so it always keeps its space.
        self.footer = tk.Frame(self.outer, bg=surface, height=22)
        self.footer.pack(fill="x", side="bottom")
        self.footer.pack_propagate(False)
        # Resize grip in the bottom-right corner: a solid square (not the see-through
        # key colour, so the whole square catches the mouse), with the classic
        # diagonal "grip" lines. Pressing it hands off to Windows' own resize.
        self.grip = tk.Canvas(self.footer, width=GRIP_SIZE, height=GRIP_SIZE, bg=panel,
                              bd=0, highlightthickness=0)
        # Diagonal ↘↖ cursor: "size_nw_se" is the Windows one; "bottom_right_corner" works everywhere.
        for cursor in ("size_nw_se", "bottom_right_corner"):
            try:
                self.grip.configure(cursor=cursor)
                break
            except tk.TclError:
                continue
        self.grip.pack(side="right", anchor="se")
        self._draw_grip()
        self.grip.bind("<ButtonPress-1>", self._start_grip_resize)
        self.grip.bind("<B1-Motion>", self._grip_motion)
        self.grip.bind("<ButtonRelease-1>", self._end_grip_resize)
        self._grip_origin = None

        self.footer_label = tk.Label(self.footer, text="PREVIEW MODE  ·  Ctrl+Shift+X: click-through",
                                     bg=surface, fg=MUTED_COLOR, font=("Segoe UI", 7), anchor="w")
        self.footer_label.pack(fill="both", padx=10)

        # One Canvas holds every message. Tk draws a canvas off-screen and then
        # copies it to the window in one go, so redraws don't flicker.
        self.chat_canvas = tk.Canvas(self.outer, bg=surface, bd=0, highlightthickness=0,
                                     width=1, height=1)
        self.chat_canvas.pack(fill="both", expand=True, padx=9, pady=8)
        self.chat_canvas.bind("<Configure>", lambda e: self._schedule_reflow(), add="+")

        self._applied["colors"] = (surface, panel, accent, text)

        # Drag the overlay by its header. Keep buttons usable.
        # (In two-window mode the header background is see-through, so grab the
        # title text or use the normal Windows title bar.)
        for widget in (self.header, self.title_label, self.status_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)

    # ------------------------------------------------------------- resize grip

    def _draw_grip(self):
        g, s = self.grip, GRIP_SIZE
        g.delete("all")
        color = self.settings["accent_color"]
        for offset in (4, 8, 12):   # three diagonal lines, like a classic Windows size grip
            g.create_line(s - 2, s - 2 - offset, s - 2 - offset, s - 2, fill=color, width=2)

    def _start_grip_resize(self, event):
        if IS_WINDOWS:
            hwnd = self._frame_hwnd(self.root)
            if hwnd:
                try:
                    # Same resize as grabbing the frame corner: smooth, native, respects min size.
                    win32().begin_corner_resize(hwnd, event.x_root, event.y_root)
                    return "break"
                except Exception:
                    pass  # fall back to resizing from Tk below
        self._grip_origin = (event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height())
        return "break"

    def _grip_motion(self, event):
        """Fallback resize (non-Windows, or if the native hand-off failed)."""
        if self._grip_origin is None:
            return
        sx, sy, w, h = self._grip_origin
        min_w, min_h = self.root.minsize()
        new_w = max(min_w, w + event.x_root - sx)
        new_h = max(min_h, h + event.y_root - sy)
        self.root.geometry(f"{new_w}x{new_h}")

    def _end_grip_resize(self, event):
        self._grip_origin = None

    def _build_backdrop(self):
        """Click-through window that sits directly behind the main window and
        paints the panel + chat cards at the chosen opacity."""
        panel = self.settings["panel_color"]
        bd = tk.Toplevel(self.root)
        bd.withdraw()
        bd.overrideredirect(True)          # no title bar, no taskbar button
        bd.configure(bg=panel)
        self.backdrop_canvas = tk.Canvas(bd, bg=panel, bd=0, highlightthickness=0)
        self.backdrop_canvas.pack(fill="both", expand=True)
        self.backdrop = bd

    def _recolor_widgets(self):
        surface = self._surface_color()
        panel = self.settings["panel_color"]
        accent = self.settings["accent_color"]
        text = self.settings["text_color"]
        colors = (surface, panel, accent, text)
        if self._applied.get("colors") == colors:
            return
        self._applied["colors"] = colors
        self.root.configure(bg=surface)
        self.outer.configure(bg=surface, highlightbackground=accent, highlightcolor=accent)
        for widget in (self.header, self.title_label, self.status_label, self.status_row, self.footer,
                       self.footer_label, self.chat_canvas, *self.status_items.values()):
            widget.configure(bg=surface)
        self._applied.pop("status", None)
        self._refresh_status_labels()
        for button in (self.settings_button, self.close_button):
            button.configure(bg=panel, fg=text)
        self.grip.configure(bg=panel)
        self._draw_grip()
        self.settings_button.configure(activebackground=accent)
        self.title_label.configure(fg=accent)
        self.divider.configure(bg=accent)
        if self.backdrop is not None:
            self.backdrop.configure(bg=panel)
            self.backdrop_canvas.configure(bg=panel)

    # -------------------------------------------------------- window management

    def _finish_window_setup(self):
        self.root.update_idletasks()
        self._setup_done = True
        if self.two_window:
            self._show_backdrop()
        # Launch interactive. Only clears WS_EX_TRANSPARENT; doesn't touch layering.
        self._set_click_through(bool(self.settings.get("click_through", False)))
        self._render_messages()
        self._check_hotkey_status()
        self.root.after(1000, self._backdrop_keeper)
        self.start_connections()

    def _apply_window_attributes(self):
        """Push topmost / colour key / opacity to the OS, but only values that changed."""
        topmost = bool(self.settings["always_on_top"])
        key = self._key_color() if self.two_window else None
        alpha = self._opacity()
        changed = False

        if self._applied.get("topmost") != topmost:
            self.root.attributes("-topmost", topmost)
            if self.backdrop is not None:
                self.backdrop.attributes("-topmost", topmost)
            self._applied["topmost"] = topmost
            changed = True

        if self.two_window and self._applied.get("key") != key:
            try:
                self.root.attributes("-transparentcolor", key)
            except tk.TclError:
                pass
            self._applied["key"] = key
            changed = True

        if self._applied.get("alpha") != alpha:
            if self.two_window:
                self._style_backdrop()
            else:
                # Fallback: fade the whole window (text included), as in v10.
                self.root.attributes("-alpha", max(0.2, alpha))
            self._applied["alpha"] = alpha
            changed = True

        if changed and self._setup_done:
            # Tk rewrites the extended style when -alpha / -transparentcolor are set,
            # so re-assert click-through and the backdrop's z-order afterwards.
            self._set_click_through(self._clickthrough_requested)
            self._restack_backdrop()

    def _frame_hwnd(self, window: tk.Misc) -> int | None:
        """HWND of a window's real top-level frame.

        On Windows, winfo_id() is Tk's inner *child* window. Hit-testing and
        layering happen on the outer frame; wm_frame() returns its handle.
        """
        try:
            return int(window.wm_frame(), 16) or None
        except (tk.TclError, ValueError):
            return None

    def _show_backdrop(self):
        if self.backdrop is None:
            return
        self._backdrop_geo = None
        self._backdrop_visible = True
        self._sync_backdrop_geometry()
        self.backdrop.deiconify()
        self.backdrop.update_idletasks()
        self._style_backdrop()
        self._restack_backdrop()

    def _style_backdrop(self, alpha: float | None = None):
        """Backdrop is always click-through and never takes focus; its alpha is the opacity setting
        (or lower while the panel is fading out)."""
        if self.backdrop is None or not self._backdrop_visible:
            return
        alpha = self._opacity() if alpha is None else alpha
        self._backdrop_alpha_now = alpha
        if not IS_WINDOWS:
            try:
                self.backdrop.attributes("-alpha", alpha)
            except tk.TclError:
                pass
            return
        hwnd = self._frame_hwnd(self.backdrop)
        if hwnd is None:
            return
        try:
            update_exstyle(hwnd, add=WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            win32().set_layered(hwnd, alpha)
        except Exception as exc:
            self._clickthrough_error = f"backdrop: {exc}"
            self._update_footer()

    def _sync_backdrop_geometry(self):
        """Cover exactly the main window's client area (everything below the title bar)."""
        if self.backdrop is None or not self._backdrop_visible:
            return
        x, y, w, h = self._client_rect()
        geo = f"{w}x{h}+{x}+{y}"
        if geo != self._backdrop_geo:
            self._backdrop_geo = geo
            self.backdrop.geometry(geo)

    def _restack_backdrop(self):
        if not (IS_WINDOWS and self.backdrop is not None and self._backdrop_visible):
            return
        front, back = self._frame_hwnd(self.root), self._frame_hwnd(self.backdrop)
        if front and back:
            try:
                win32().place_below(back, front)
            except Exception:
                pass

    def _backdrop_keeper(self):
        """Cheap safety net: keep the backdrop aligned and directly behind the main window."""
        if not self.root.winfo_exists():
            return
        self._sync_backdrop_geometry()
        self._restack_backdrop()
        self.root.after(1000, self._backdrop_keeper)

    def _on_root_configure(self, event):
        # Only handle top-level moves/resizes, not child Configure events.
        if event.widget is not self.root:
            return
        # Follow moves immediately (no debounce) so the backdrop doesn't trail behind.
        self._sync_backdrop_geometry()

    def _on_root_map(self, event):
        if event.widget is self.root and self._setup_done and self.backdrop is not None:
            self._show_backdrop()

    def _on_root_unmap(self, event):
        # Minimised: hide the backdrop too.
        if event.widget is self.root and self.backdrop is not None:
            self._backdrop_visible = False
            self.backdrop.withdraw()

    def _client_rect(self) -> tuple[int, int, int, int]:
        """Main window's client area (x, y, w, h) on screen.

        On Windows this asks Windows directly. Tk's own numbers assume the title
        bar is still there, so they'd be off by its height in chat-only mode.
        """
        if IS_WINDOWS:
            hwnd = self._frame_hwnd(self.root)
            if hwnd:
                try:
                    return win32().client_rect_on_screen(hwnd)
                except Exception:
                    pass
        return (self.root.winfo_rootx(), self.root.winfo_rooty(),
                self.root.winfo_width(), self.root.winfo_height())

    def _check_hotkey_status(self):
        """Wait (without blocking Tk) for the hotkey and tray threads to report in."""
        if not self.root.winfo_exists():
            return
        waiting = [t for t in (self.hotkey, self.tray) if t is not None and not t.ready.is_set()]
        if waiting:
            self.root.after(100, self._check_hotkey_status)
            return
        self._update_footer()

    def _schedule_reflow(self):
        if self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        self._resize_after_id = self.root.after(30, self._render_after_resize)

    def _render_after_resize(self):
        self._resize_after_id = None
        if self.root.winfo_exists():
            self._render_messages()

    def _start_drag(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event):
        if not self.drag_origin:
            return
        sx, sy, wx, wy = self.drag_origin
        x, y = wx + event.x_root - sx, wy + event.y_root - sy
        self.root.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------------- messages

    def _load_demo_messages(self):
        now = time.strftime("%H:%M")
        # More sample rows make the Visible messages control testable before live chat is connected.
        self.messages = [
            {"platform": "twitch", "username": "ViewerOne", "message": "Hey! How's the stream going?", "time": now},
            {"platform": "kick", "username": "KickViewer", "message": "Nice play 😄", "time": now},
            {"platform": "twitch", "username": "AnotherViewer", "message": "LMAO", "time": now},
            {"platform": "kick", "username": "GreenName", "message": "First time here!", "time": now},
            {"platform": "twitch", "username": "PixelPilot", "message": "That was close!", "time": now},
            {"platform": "kick", "username": "GGPlayer", "message": "Clean move", "time": now},
            {"platform": "twitch", "username": "ChatEnjoyer", "message": "😂😂", "time": now},
            {"platform": "kick", "username": "NewViewer", "message": "What game is this?", "time": now},
            {"platform": "youtube", "username": "Lurker42", "message": "Hello chat", "time": now},
            {"platform": "kick", "username": "GreenName", "message": "Let's gooo", "time": now},
            {"platform": "twitch", "username": "ViewerTwo", "message": "Huge", "time": now},
            {"platform": "kick", "username": "KickFan", "message": "GG", "time": now},
        ]
        arrived = self._clock()
        for msg in self.messages:
            msg["arrived"] = arrived
        self._showing_demo = True

    def _font(self, size: int, bold: bool = False) -> tkfont.Font:
        key = (self.settings["font_family"], size, bold)
        font = self._font_cache.get(key)
        if font is None:
            font = tkfont.Font(root=self.root, family=key[0], size=size, weight="bold" if bold else "normal")
            self._font_cache[key] = font
        return font

    def _render_messages(self):
        """Redraw chat on the canvas, newest at the bottom.

        Everything is canvas items (no child widgets), so a redraw never creates
        or destroys windows and can't flash.
        """
        c = self.chat_canvas
        c.delete("all")
        self._card_rects = []
        self._card_colors = []
        width, height = c.winfo_width(), c.winfo_height()
        if width <= 1 or height <= 1:
            return  # not laid out yet; the canvas <Configure> event will redraw
        minimal = self.settings.get("style") == "minimal"
        font_size = max(8, min(24, int(self.settings["font_size"])))
        spacing = max(0, min(24, int(self.settings["message_spacing"])))
        limit = max(1, min(100, int(self.settings["max_messages"])))
        now = self._clock()
        # Fully faded messages are skipped, so the rest close up at the bottom.
        live = [(m, f) for m in self.messages if (f := self._fade_amount(m, now)) < 1.0]
        visible = live[-limit:]

        if not self.messages:
            c.create_text(0, 6, text="Waiting for chat messages…", anchor="nw",
                          fill=self.settings["text_color"], font=self._font(font_size))
        else:
            # Lay out bottom-up; anything above the top edge is simply clipped,
            # so the oldest messages are the ones that get cut off.
            bottom = height
            for msg, fade in reversed(visible):
                if bottom <= 0:
                    break
                if minimal:
                    top = self._draw_minimal_row(msg, bottom, width, font_size, fade)
                else:
                    top = self._draw_card(msg, bottom, width, font_size, fade)
                bottom = top - spacing

        if not self.two_window and not minimal:
            # Single-window fallback: cards are drawn here, beneath the text.
            for rect, color in zip(self._card_rects, self._card_colors):
                c.tag_lower(c.create_rectangle(*rect, fill=color, width=0))
        self._render_backdrop()
        self._fade_background([f for _m, f in visible])
        self._schedule_fade_tick(now)

    # ------------------------------------------------------------------- fading

    @staticmethod
    def _clock() -> float:
        return time.monotonic()

    def _fade_delay(self) -> float:
        """Seconds a message stays fully visible before it starts to fade (0 = never fade)."""
        try:
            return max(0.0, float(self.settings.get("fade_after", 0)))
        except (TypeError, ValueError):
            return 0.0

    def _fade_amount(self, msg: dict, now: float) -> float:
        """0.0 = fully visible ... 1.0 = completely faded (and no longer drawn)."""
        delay = self._fade_delay()
        if delay <= 0:
            return 0.0
        age = now - msg.get("arrived", now)
        if age <= delay:
            return 0.0
        return min(1.0, (age - delay) / FADE_SECONDS)

    def _mix(self, color: str, target: str, amount: float) -> str:
        """Blend a colour toward `target` (the panel colour). With the panel keyed see-through,
        text blended all the way into it has nothing left to show."""
        if amount <= 0:
            return color
        key = (color, target)
        cache = self._mix_cache
        if key not in cache:
            if len(cache) > 256:
                cache.clear()
            cache[key] = (self._rgb(color), self._rgb(target))
        (r1, g1, b1), (r2, g2, b2) = cache[key]
        a = min(1.0, amount)
        return "#%02x%02x%02x" % (round(r1 + (r2 - r1) * a), round(g1 + (g2 - g1) * a), round(b1 + (b2 - b1) * a))

    def _schedule_fade_tick(self, now: float) -> None:
        """Redraw again only when something is (or is about to start) fading."""
        if self._fade_after_id is not None:
            try:
                self.root.after_cancel(self._fade_after_id)
            except tk.TclError:
                pass
            self._fade_after_id = None
        delay = self._fade_delay()
        if delay <= 0 or not self.messages:
            return
        next_in = None
        for msg in self.messages:
            age = now - msg.get("arrived", now)
            if age < delay:                                   # still solid: wake when it starts fading
                wait = delay - age
            elif age < delay + FADE_SECONDS:                  # fading right now: animate
                wait = FADE_FRAME
            else:
                continue                                      # already gone
            next_in = wait if next_in is None else min(next_in, wait)
        if next_in is not None:
            self._fade_after_id = self.root.after(max(15, int(next_in * 1000)), self._fade_tick)

    def _fade_tick(self) -> None:
        self._fade_after_id = None
        if self.root.winfo_exists():
            self._render_messages()

    def _fade_background(self, fades: list[float]) -> None:
        """Optionally fade the panel too, so the overlay vanishes completely when chat is quiet."""
        if not (self.two_window and self.backdrop is not None and self._backdrop_visible):
            return
        if self.settings.get("fade_background") and self._fade_delay() > 0 and self.messages:
            remaining = 1.0 - min(fades) if fades else 0.0       # the freshest message decides
        else:
            remaining = 1.0
        alpha = round(self._opacity() * remaining, 3)
        if alpha != self._backdrop_alpha_now:
            self._backdrop_alpha_now = alpha
            self._style_backdrop(alpha)

    def _new_tag(self) -> str:
        self._item_seq += 1
        return f"msg{self._item_seq}"

    def _platform_color(self, platform: str) -> str:
        info = PLATFORMS.get(platform)
        return self.settings.get(info["color_key"], MUTED_COLOR) if info else MUTED_COLOR

    def _draw_card(self, msg: dict, bottom: int, width: int, font_size: int, fade: float = 0.0) -> int:
        """Custom style: header line (platform, user, time) + wrapped message. Returns the card's top y."""
        c, tag = self.chat_canvas, self._new_tag()
        panel = self.settings["panel_color"]
        pcolor = self._mix(self._platform_color(msg["platform"]), panel, fade)
        text_color = self._mix(self.settings["text_color"], panel, fade)
        muted = self._mix(MUTED_COLOR, panel, fade)
        small = max(7, font_size - 3)
        name_font = self._font(font_size, bold=True)
        mid = CARD_PADY + name_font.metrics("linespace") // 2   # vertical centre of the header line
        x = CARD_PADX
        if self.settings.get("show_platform_labels", True):
            label = PLATFORMS.get(msg["platform"], {}).get("label", msg["platform"].upper())
            item = c.create_text(x, mid, text=label, anchor="w", fill=pcolor,
                                 font=self._font(small, bold=True), tags=(tag,))
            x = c.bbox(item)[2] + 6
        item = c.create_text(x, mid, text=msg["username"], anchor="w", fill=pcolor,
                             font=name_font, tags=(tag,))
        line_bottom = c.bbox(item)[3]
        if self.settings.get("show_timestamps", False):
            item = c.create_text(width - CARD_PADX, mid, text=msg.get("time", ""), anchor="e",
                                 fill=muted, font=self._font(small), tags=(tag,))
            line_bottom = max(line_bottom, c.bbox(item)[3])
        body = c.create_text(CARD_PADX, line_bottom + 3, text=msg["message"], anchor="nw",
                             width=max(80, width - 2 * CARD_PADX), fill=text_color,
                             font=self._font(font_size), tags=(tag,))
        card_height = c.bbox(body)[3] + CARD_PADY
        top = bottom - card_height
        c.move(tag, 0, top)
        self._card_rects.append((0, top, width, bottom))
        self._card_colors.append(self._mix(CARD_COLOR, panel, fade))
        return top

    def _draw_minimal_row(self, msg: dict, bottom: int, width: int, font_size: int, fade: float = 0.0) -> int:
        """Minimal style: [T] user: message, wrapped with a hanging indent. Returns the row's top y."""
        c, tag = self.chat_canvas, self._new_tag()
        panel = self.settings["panel_color"]
        pcolor = self._mix(self._platform_color(msg["platform"]), panel, fade)
        text_color = self._mix(self.settings["text_color"], panel, fade)
        muted = self._mix(MUTED_COLOR, panel, fade)
        bold = self._font(font_size, bold=True)
        prefix = PLATFORMS.get(msg["platform"], {}).get("short", "?")
        item = c.create_text(0, 0, text=f"[{prefix}]", anchor="nw", fill=pcolor, font=bold, tags=(tag,))
        item = c.create_text(c.bbox(item)[2], 0, text=f" {msg['username']}: ", anchor="nw",
                             fill=pcolor, font=bold, tags=(tag,))
        x = c.bbox(item)[2]
        right = width
        if self.settings.get("show_timestamps", False):
            ts = c.create_text(width, 0, text=f" {msg.get('time', '')}", anchor="ne", fill=muted,
                               font=self._font(max(7, font_size - 3)), tags=(tag,))
            right = c.bbox(ts)[0] - 4
        c.create_text(x, 0, text=msg["message"], anchor="nw", width=max(60, right - x),
                      fill=text_color, font=self._font(font_size), tags=(tag,))
        row_height = c.bbox(tag)[3]
        top = bottom - row_height
        c.move(tag, 0, top)
        return top

    def _render_backdrop(self):
        """Mirror the chat cards onto the backdrop window, in main-window client coordinates."""
        if self.backdrop_canvas is None:
            return
        bc = self.backdrop_canvas
        bc.delete("all")
        if self.settings.get("style") == "minimal":
            return
        ox = self.chat_canvas.winfo_rootx() - self.root.winfo_rootx()
        oy = self.chat_canvas.winfo_rooty() - self.root.winfo_rooty()
        ch = self.chat_canvas.winfo_height()
        colors = self._card_colors or [CARD_COLOR] * len(self._card_rects)
        for (x1, y1, x2, y2), color in zip(self._card_rects, colors):
            y1 = max(y1, 0)   # clip to the chat area so cards don't slide under the header
            if y2 > y1:
                bc.create_rectangle(x1 + ox, y1 + oy, x2 + ox, min(y2, ch) + oy, fill=color, width=0)

    # ---------------------------------------------------------------- settings

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        # Keep a committed snapshot so Cancel/window-close can discard live previews.
        baseline = self.persisted_settings.copy()
        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title(f"{APP_NAME} — Settings")
        win.geometry(f"{SETTINGS_SIZE[0]}x{SETTINGS_SIZE[1]}")
        win.minsize(*SETTINGS_SIZE)
        win.configure(bg="#F4F4F5")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        # Save/Cancel live outside the tabs so they're always visible.
        buttons = ttk.Frame(win, padding=(14, 0, 14, 12))
        buttons.pack(fill="x", side="bottom")
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=8, pady=(8, 8))
        body = ttk.Frame(notebook, padding=12)
        notebook.add(body, text="Appearance")
        conn = ttk.Frame(notebook, padding=12)
        notebook.add(conn, text="Connections")
        sound_tab = ttk.Frame(notebook, padding=12)
        notebook.add(sound_tab, text="Sound")
        self._settings_tabs = (notebook, body, conn, sound_tab)

        style_var = tk.StringVar(value=self.settings["style"])
        style_row = ttk.Frame(body); style_row.pack(fill="x", pady=3)
        ttk.Label(style_row, text="Overlay style").pack(side="left")
        style_combo = ttk.Combobox(style_row, textvariable=style_var, values=("custom", "minimal"), state="readonly", width=12)
        style_combo.pack(side="right")

        opacity_var = tk.DoubleVar(value=self._opacity())
        if self.two_window:
            opacity_scale = self._setting_slider(body, "Background opacity", opacity_var, 0.0, 1.0, "{:.2f}")
        else:
            opacity_scale = self._setting_slider(body, "Opacity", opacity_var, 0.2, 1.0, "{:.2f}")
        font_var = tk.DoubleVar(value=float(self.settings["font_size"]))
        # DoubleVar always returns floats while dragging, so integer settings
        # are formatted with no decimals.
        font_scale = self._setting_slider(body, "Font size", font_var, 8, 24, "{:.0f}")
        spacing_var = tk.DoubleVar(value=float(self.settings["message_spacing"]))
        spacing_scale = self._setting_slider(body, "Message spacing", spacing_var, 0, 24, "{:.0f} px")
        limit_var = tk.DoubleVar(value=float(self.settings["max_messages"]))
        limit_scale = self._setting_slider(body, "Visible messages", limit_var, 1, 40, "{:.0f}")
        fade_var = tk.DoubleVar(value=self._fade_delay())
        fade_scale = self._setting_slider(body, "Fade after", fade_var, 0, 300,
                                          lambda v: "Never" if round(v) <= 0 else f"{round(v)} s")

        ttk.Separator(body).pack(fill="x", pady=9)
        ttk.Label(body, text="Colors", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 5))
        color_vars = {}
        color_buttons = {}
        color_rows = [("panel_color", "Panel"), ("text_color", "Message text"), ("accent_color", "Accent")]
        color_rows += [(info["color_key"], info["name"]) for info in PLATFORMS.values()]
        for key, label in color_rows:
            row = ttk.Frame(body); row.pack(fill="x", pady=2)
            ttk.Label(row, text=label).pack(side="left")
            color_vars[key] = tk.StringVar(value=self.settings[key])
            swatch = tk.Button(row, text=self.settings[key], width=12, bg=self.settings[key],
                               fg=self._contrast_text(self.settings[key]),
                               command=lambda k=key, v=color_vars[key]: self._choose_color(k, v, win),
                               relief="groove")
            swatch.pack(side="right")
            color_buttons[key] = swatch

        ttk.Separator(body).pack(fill="x", pady=9)
        ttk.Label(body, text="Window", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 5))
        top_var = tk.BooleanVar(value=bool(self.settings["always_on_top"]))
        ttk.Checkbutton(body, text="Always on top", variable=top_var).pack(anchor="w")
        labels_var = tk.BooleanVar(value=bool(self.settings["show_platform_labels"]))
        ttk.Checkbutton(body, text="Show platform labels (custom style)", variable=labels_var).pack(anchor="w")
        timestamps_var = tk.BooleanVar(value=bool(self.settings["show_timestamps"]))
        ttk.Checkbutton(body, text="Show timestamps", variable=timestamps_var).pack(anchor="w")
        fade_bg_var = tk.BooleanVar(value=bool(self.settings.get("fade_background", False)))
        ttk.Checkbutton(body, text="Fade the background too when chat goes quiet",
                        variable=fade_bg_var).pack(anchor="w")

        ttk.Label(body, text="Ctrl+Shift+X toggles click-through (chat only, no border). "
                             "Right-click the tray icon → Show border to bring it back.",
                  foreground="#52525B", wraplength=370).pack(anchor="w", pady=(8, 2))

        conn_vars = self._build_connections_tab(conn)
        self._build_sound_tab(sound_tab, win)

        def read_controls():
            self.settings.update({
                "style": style_var.get(), "opacity": round(float(opacity_var.get()), 2),
                "font_size": int(round(font_var.get())),
                "message_spacing": int(round(spacing_var.get())), "max_messages": int(round(limit_var.get())),
                "always_on_top": bool(top_var.get()), "show_platform_labels": bool(labels_var.get()),
                "show_timestamps": bool(timestamps_var.get()),
                "fade_after": int(round(fade_var.get())), "fade_background": bool(fade_bg_var.get()),
            })
            for key, var in color_vars.items():
                self.settings[key] = var.get()

        def preview_now():
            self._preview_after_id = None
            if not win.winfo_exists():
                return
            read_controls()
            self._apply_settings()
            for key, button in color_buttons.items():
                color = color_vars[key].get()
                if button.cget("text") != color:
                    button.configure(bg=color, fg=self._contrast_text(color), text=color)

        def preview(*_):
            # Coalesce: a single slider tick fires the variable trace AND the
            # Scale command. Schedule one redraw for the next idle moment instead
            # of redrawing for each. Idle time occurs between drag motion events,
            # so the overlay still updates live while dragging.
            if self._preview_after_id is None and win.winfo_exists():
                self._preview_after_id = win.after_idle(preview_now)

        def cancel_pending_preview():
            if self._preview_after_id is not None:
                try:
                    win.after_cancel(self._preview_after_id)
                except tk.TclError:
                    pass
                self._preview_after_id = None

        def save_and_close():
            cancel_pending_preview()
            preview_now()
            before = {k: self.persisted_settings.get(k, "") for k in CONNECTION_KEYS}
            for key, var in conn_vars.items():
                self.settings[key] = var.get().strip()
            if self.settings["kick_channel"] != before["kick_channel"] and \
                    self.settings["kick_chatroom_id"] == before["kick_chatroom_id"]:
                # New Kick channel: the old chatroom ID belongs to the old channel.
                self.settings["kick_chatroom_id"] = ""
            if self.settings["kick_chatroom_id"]:
                self.settings["kick_chatroom_slug"] = chat_sources.normalize_kick(self.settings["kick_channel"])
            save_settings(self.settings)
            self.persisted_settings = self.settings.copy()
            self.settings_window = None
            self._settings_sound_var = None
            win.destroy()
            if {k: self.settings.get(k, "") for k in CONNECTION_KEYS} != before:
                self.start_connections()

        def cancel_and_close():
            cancel_pending_preview()
            # Keep the live click-through state; it is toggled by hotkey, not by this dialog.
            click_through = self._clickthrough_requested
            self.settings = baseline.copy()
            self.settings["click_through"] = click_through
            # Keep a Kick chatroom ID that was looked up while this window was open,
            # and a sound on/off made from the tray menu meanwhile (both saved already).
            for key in ("kick_chatroom_id", "kick_chatroom_slug", "sound_enabled"):
                self.settings[key] = self.persisted_settings.get(key, DEFAULTS[key])
            self._apply_settings()
            self.settings_window = None
            self._settings_sound_var = None
            win.destroy()

        ttk.Button(buttons, text="Save", command=save_and_close).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=cancel_and_close).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", cancel_and_close)

        # Every control previews immediately; changes are persisted only by Save.
        for variable in (style_var, opacity_var, font_var, spacing_var, limit_var,
                         top_var, labels_var, timestamps_var, fade_var, fade_bg_var, *color_vars.values()):
            variable.trace_add("write", preview)
        # Explicit Scale callbacks ensure redraws occur during slider drags on Windows/Tk builds
        # where variable traces may not reliably refresh the overlay until the drag ends.
        def scale_changed(value, variable):
            try:
                variable.set(float(value))
            except (ValueError, tk.TclError):
                pass
            preview()

        opacity_scale.configure(command=lambda value: scale_changed(value, opacity_var))
        font_scale.configure(command=lambda value: scale_changed(value, font_var))
        spacing_scale.configure(command=lambda value: scale_changed(value, spacing_var))
        limit_scale.configure(command=lambda value: scale_changed(value, limit_var))
        fade_scale.configure(command=lambda value: scale_changed(value, fade_var))
        win.update_idletasks()
        # Center the larger settings window relative to the overlay, keeping the buttons visible.
        x = max(0, self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2)
        y = max(0, self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2)
        win.geometry(f"{SETTINGS_SIZE[0]}x{SETTINGS_SIZE[1]}+{x}+{y}")

    def _build_sound_tab(self, parent, win) -> None:
        """Message sound: on/off, volume, quiet time, built-in or your own file, Test.

        Changes apply right away (so Test plays what you picked); Save keeps them,
        Cancel puts the old ones back.
        """
        wrap = 370
        enabled_var = tk.BooleanVar(value=bool(self.settings.get("sound_enabled", True)))
        self._settings_sound_var = enabled_var
        ttk.Checkbutton(parent, text="Play a sound when a new chat message arrives",
                        variable=enabled_var).pack(anchor="w", pady=(0, 8))
        volume_var = tk.DoubleVar(value=float(self.settings.get("sound_volume", 60)))
        volume_scale = self._setting_slider(parent, "Volume", volume_var, 0, 100, "{:.0f} %")
        cooldown_var = tk.DoubleVar(value=float(self.settings.get("sound_cooldown", 1.0)))
        self._setting_slider(parent, "Quiet time", cooldown_var, 0, 10, "{:.1f} s")
        ttk.Label(parent, wraplength=wrap, foreground="#52525B",
                  text="At most one sound per quiet time, so a busy chat doesn't turn into a drumroll. "
                       "0 = a sound for every message.").pack(anchor="w", pady=(2, 4))

        ttk.Separator(parent).pack(fill="x", pady=9)
        ttk.Label(parent, text="Sound", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        file_var = tk.StringVar(value=str(self.settings.get("sound_file", "")))
        name_label = ttk.Label(parent, text="", wraplength=wrap)
        name_label.pack(anchor="w", pady=(2, 6))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        status_label = ttk.Label(parent, text="", wraplength=wrap, foreground="#52525B")

        def describe(*_):
            value = file_var.get()
            if not value:
                name_label.configure(text="Built-in chime")
            elif self.sound_path(value) == BUILTIN_SOUND:
                name_label.configure(text=f"{Path(value).name} (file missing, using the built-in chime)")
            else:
                name_label.configure(text=Path(value).name)

        def push(*_):
            try:
                self.settings.update({
                    "sound_enabled": bool(enabled_var.get()),
                    "sound_volume": int(round(volume_var.get())),
                    "sound_cooldown": round(float(cooldown_var.get()), 1),
                    "sound_file": file_var.get(),
                })
            except tk.TclError:
                pass

        def show(text: str, error: bool = False):
            status_label.configure(text=text, foreground="#B91C1C" if error else "#52525B")

        def test():
            push()
            if not self.sound.available:
                show("Sounds play on Windows only.", error=True)
            elif self.play_message_sound(force=True):
                show(f"Playing at {self.settings['sound_volume']} %.")
            elif self.settings["sound_volume"] <= 0:
                show("Volume is at 0 %.")
            else:
                show(self.sound.error or "Couldn't play the sound.", error=True)

        def choose():
            picked = filedialog.askopenfilename(
                parent=win, title="Choose a message sound",
                filetypes=[("Sound files", "*.wav *.mp3 *.wma"), ("All files", "*.*")])
            if not picked:
                return
            source = Path(picked)
            if source.suffix.lower() not in sound_player.SOUND_TYPES:
                show("Pick a WAV, MP3 or WMA file.", error=True)
                return
            try:
                copy = sound_player.import_sound(source, SOUNDS_DIR)
            except OSError as exc:
                show(f"Couldn't copy that file: {exc}", error=True)
                return
            problem = self.sound.check(copy)
            if problem:
                show(f"Windows can't play that file: {problem}", error=True)
                return
            try:
                file_var.set(copy.relative_to(DATA_DIR).as_posix())   # stored relative, so the folder can move
            except ValueError:
                file_var.set(str(copy))
            test()

        def use_builtin():
            file_var.set("")
            test()

        ttk.Button(row, text="Choose sound…", command=choose).pack(side="left")
        ttk.Button(row, text="Built-in chime", command=use_builtin).pack(side="left", padx=6)
        ttk.Button(row, text="▶ Test", command=test).pack(side="left")
        status_label.pack(anchor="w", pady=(8, 0))
        ttk.Label(parent, wraplength=wrap, foreground="#52525B",
                  text="WAV, MP3 or WMA. Your file is copied into the overlay's \"sounds\" folder, "
                       "so it keeps working if you move the original. Short sounds (under a second) work best."
                  ).pack(anchor="w", pady=(10, 0))
        ttk.Label(parent, wraplength=wrap, foreground="#B45309",
                  text="Quick on/off any time: right-click the tray icon → Message sound.").pack(anchor="w", pady=(8, 0))

        for variable in (enabled_var, volume_var, cooldown_var, file_var):
            variable.trace_add("write", push)
        file_var.trace_add("write", describe)
        volume_scale.bind("<ButtonRelease-1>", lambda e: test(), add="+")   # hear the new volume on release
        describe()

    def _build_connections_tab(self, parent) -> dict[str, tk.StringVar]:
        """Channel fields. Applied when Save is clicked (not live like the appearance controls)."""
        wrap = 370
        variables: dict[str, tk.StringVar] = {}

        def field(key: str, label: str, hint: str):
            ttk.Label(parent, text=label, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 1))
            var = tk.StringVar(value=str(self.settings.get(key, "")))
            entry = ttk.Entry(parent, textvariable=var)
            entry.pack(fill="x")
            ttk.Label(parent, text=hint, foreground="#52525B", wraplength=wrap).pack(anchor="w", pady=(1, 2))
            variables[key] = var
            return entry

        field("twitch_channel", "Twitch channel",
              "Channel name or link, e.g. yourname. Read-only, no login needed.")
        field("kick_channel", "Kick channel", "Channel name or link, e.g. yourname.")
        room_row = ttk.Frame(parent)
        room_row.pack(fill="x", pady=(2, 0))
        ttk.Label(room_row, text="Kick chatroom ID").pack(side="left")
        room_var = tk.StringVar(value=str(self.settings.get("kick_chatroom_id", "")))
        ttk.Entry(room_row, textvariable=room_var, width=12).pack(side="left", padx=6)
        variables["kick_chatroom_id"] = room_var

        def find_room_id():
            slug = chat_sources.normalize_kick(variables["kick_channel"].get())
            if slug:
                webbrowser.open(chat_sources.KickChat.CHANNEL_API.format(slug=urllib.parse.quote(slug)))
        ttk.Button(room_row, text="Find ID…", command=find_room_id).pack(side="left")
        ttk.Label(parent, wraplength=wrap, foreground="#52525B",
                  text="Usually filled in automatically. If Kick blocks that, click Find ID…: your browser "
                       "opens a page of text. Find \"chatroom\" and copy the number after \"id\": into this box."
                  ).pack(anchor="w", pady=(1, 2))
        field("youtube_channel", "YouTube channel",
              "@handle, channel link, or a live video link. The overlay checks every minute "
              "and connects when the channel goes live.")

        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="Leave a box empty to turn that platform off. Changes apply when you click Save.",
                  foreground="#B45309", wraplength=wrap).pack(anchor="w")
        return variables

    def _setting_slider(self, parent, label, variable, low, high, fmt):
        row = ttk.Frame(parent); row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=17).pack(side="left")
        value_label = ttk.Label(row, width=7, anchor="e")
        value_label.pack(side="right")
        scale = ttk.Scale(row, from_=low, to=high, variable=variable, orient="horizontal")
        scale.pack(side="left", fill="x", expand=True, padx=(5, 8))
        def update(*_):
            value = float(variable.get())
            value_label.configure(text=fmt(value) if callable(fmt) else fmt.format(value))
        variable.trace_add("write", update); update()
        return scale

    def _choose_color(self, key, var, parent):
        # parent= keeps the dialog above the always-on-top Settings window.
        # Setting the variable triggers the preview, which also recolors the swatch button.
        chosen = colorchooser.askcolor(color=var.get(), title=f"Choose {key.replace('_', ' ')} color",
                                       parent=parent)
        if chosen and chosen[1]:
            var.set(chosen[1])

    @staticmethod
    def _contrast_text(hex_color):
        try:
            c = hex_color.lstrip("#")
            r, g, b = (int(c[i:i+2], 16) for i in (0, 2, 4))
            return "#111111" if (r * 299 + g * 587 + b * 114) / 1000 > 145 else "#FFFFFF"
        except Exception:
            return "#FFFFFF"

    def _apply_settings(self):
        """Apply a settings change with as little OS/window churn as possible."""
        self._recolor_widgets()
        self._apply_window_attributes()
        if self.backdrop is not None and self._backdrop_visible:
            self._sync_backdrop_geometry()
        self._render_messages()

    # ------------------------------------------------------------ click-through

    def _set_click_through(self, enabled: bool):
        self.settings["click_through"] = enabled
        self._clickthrough_requested = enabled
        self._clickthrough_error = None
        if IS_WINDOWS:
            hwnd = self._frame_hwnd(self.root)
            if hwnd is None:
                self._clickthrough_error = "window handle not found"
            else:
                try:
                    if enabled:
                        # Clicks only pass through a window that is BOTH layered and transparent.
                        update_exstyle(hwnd, add=WS_EX_LAYERED | WS_EX_TRANSPARENT)
                        if self.two_window:
                            # Re-send the colour key with alpha: LWA_ALPHA alone would switch the key off.
                            win32().set_layered(hwnd, 1.0, self._rgb(self._key_color()))
                        else:
                            win32().set_layered(hwnd, max(0.2, self._opacity()))
                    else:
                        # Only drop TRANSPARENT; leave layering as Tk configured it.
                        update_exstyle(hwnd, remove=WS_EX_TRANSPARENT)
                except Exception as exc:
                    self._clickthrough_error = str(exc) or exc.__class__.__name__
        # Chat-only mode follows click-through. If click-through failed, keep the
        # border so the window is never left frameless AND clickable.
        self._set_chrome_hidden(enabled and not self._clickthrough_error)
        self._update_footer()

    # ----------------------------------------------------------- chat-only mode

    def _set_chrome_hidden(self, hidden: bool):
        """Hide/show everything except the chat: Windows frame, outline, header, footer.

        The client area keeps its exact spot on screen, so the chat doesn't jump.
        """
        if hidden == self._chrome_hidden:
            return
        x, y, w, h = self._client_rect()
        self._chrome_hidden = hidden
        if hidden:
            for widget in (self.header, self.divider, self.footer):
                widget.pack_forget()
            self.outer.configure(highlightthickness=0)
        else:
            self.outer.configure(highlightthickness=1)
            # Re-pack in the original order: header, divider, footer, then chat.
            self.header.pack(fill="x", before=self.chat_canvas)
            self.divider.pack(fill="x", before=self.chat_canvas)
            self.footer.pack(fill="x", side="bottom", before=self.chat_canvas)
        self.root.update_idletasks()
        if IS_WINDOWS:
            self._set_native_frame(not hidden, x, y, w, h)
        self._sync_backdrop_geometry()
        self._restack_backdrop()
        self._schedule_reflow()

    def _set_native_frame(self, visible: bool, x: int, y: int, w: int, h: int):
        """Remove/restore the Windows title bar + resize edges without moving the client area.

        Done with window styles rather than Tk's overrideredirect, because
        overrideredirect rebuilds the window (new handle, steals focus from the
        game) and removes the taskbar button.
        """
        self._border_error = None
        hwnd = self._frame_hwnd(self.root)
        if not hwnd:
            return
        api = win32()
        style_changed = False
        try:
            style = api.get_style(hwnd)
            if not visible:
                bits = style & FRAME_BITS
                if not bits:
                    return
                self._saved_frame_bits = bits
                api.set_style(hwnd, style & ~FRAME_BITS)
                style_changed = True
                api.move_resize(hwnd, x, y, w, h, frame_changed=True)
            else:
                bits = self._saved_frame_bits or FRAME_BITS
                if style & bits == bits:
                    return
                new_style = style | bits
                api.set_style(hwnd, new_style)
                style_changed = True
                ox, oy, ow, oh = api.outer_rect_for_client(x, y, w, h, new_style, api.get_exstyle(hwnd))
                api.move_resize(hwnd, ox, oy, ow, oh, frame_changed=True)
        except Exception as exc:
            self._border_error = str(exc) or exc.__class__.__name__
            if style_changed:
                # The style already changed: at minimum get Windows to rebuild the
                # frame, so the title bar and its × button actually work.
                try:
                    api.refresh_frame(hwnd)
                except Exception:
                    pass

    def _update_footer(self):
        hotkey_failed = (IS_WINDOWS and self.hotkey is not None and self.hotkey.ready.is_set()
                         and not self.hotkey.registered)
        tray_failed = (IS_WINDOWS and self.tray is not None and self.tray.ready.is_set()
                       and not self.tray.ok)
        conn_errors = [f"{PLATFORMS[p]['name']}: {detail or 'error'}"
                       for p, (state, detail) in self.conn_status.items() if state == "error"]
        # Not errors, but worth saying (e.g. "YouTube: not live yet, checks every minute").
        conn_notes = [f"{PLATFORMS[p]['name']}: {detail}"
                      for p, (state, detail) in self.conn_status.items() if state == "waiting" and detail]
        if self._clickthrough_error:
            text = f"Click-through unavailable: {self._clickthrough_error}"
        elif self._border_error:
            text = f"Border error: {self._border_error}"
        elif conn_errors:
            text = "  ·  ".join(conn_errors)
        elif self.sound.error:
            text = f"Sound: {self.sound.error}"
        elif conn_notes:
            text = "  ·  ".join(conn_notes)
        elif not IS_WINDOWS:
            text = "PREVIEW MODE  ·  click-through needs Windows"
        elif hotkey_failed and tray_failed:
            text = "Ctrl+Shift+X taken and tray icon failed: click-through off"
        elif hotkey_failed:
            text = "PREVIEW MODE  ·  Ctrl+Shift+X taken: use the tray icon"
        elif tray_failed:
            text = f"PREVIEW MODE  ·  Ctrl+Shift+X: click-through (no tray: {self.tray.error})"
        elif self._clickthrough_requested:
            text = "CLICK-THROUGH ON  ·  Ctrl+Shift+X to disable"
        else:
            text = "PREVIEW MODE  ·  Ctrl+Shift+X or tray icon: click-through"
        if self.footer_label.cget("text") != text:
            self.footer_label.configure(text=text)

    def _poll_ui_queue(self):
        """Drain everything the background threads queued, then redraw at most once."""
        changed = False
        handled = 0
        try:
            while handled < 500:              # cap per tick so a chat flood can't freeze the UI
                item = self._ui_queue.get_nowait()
                handled += 1
                if isinstance(item, tuple):
                    changed |= self._handle_event(item)
                else:
                    self._handle_command(item)
                    if item == "exit":
                        return  # window is gone; stop polling
        except queue.Empty:
            pass
        if changed:
            self.messages = self.messages[-MAX_STORED_MESSAGES:]
            self._render_messages()
        if self._new_chat:
            self._new_chat = 0
            self.play_message_sound()
        if self.root.winfo_exists():
            self.root.after(100, self._poll_ui_queue)

    # -------------------------------------------------------------------- sound

    def sound_path(self, value: str | None = None) -> Path:
        """Resolve the sound setting to a file; falls back to the built-in chime if it's missing."""
        value = self.settings.get("sound_file", "") if value is None else value
        if not value:
            return BUILTIN_SOUND
        path = Path(value)
        if not path.is_absolute():
            path = DATA_DIR / path
        return path if path.exists() else BUILTIN_SOUND

    def play_message_sound(self, force: bool = False) -> bool:
        """Play the message sound (respecting on/off + cooldown unless force=True, e.g. the Test button)."""
        if not force and not self.settings.get("sound_enabled", True):
            return False
        try:
            volume = float(self.settings.get("sound_volume", 60))
            cooldown = 0.0 if force else max(0.0, float(self.settings.get("sound_cooldown", 1.0)))
        except (TypeError, ValueError):
            volume, cooldown = 60.0, 1.0
        played = self.sound.play(self.sound_path(), volume, cooldown)
        if self.sound.error != self._applied.get("sound_error"):
            self._applied["sound_error"] = self.sound.error
            self._update_footer()
        return played

    def _handle_event(self, event: tuple) -> bool:
        """Apply one event from a chat connection. Returns True if the chat needs redrawing."""
        kind, platform, payload, *rest = event
        if rest and rest[0] != self._conn_generation:
            return False      # from a connection that was replaced (e.g. channel changed in Settings)
        if kind == "chat":
            if self._showing_demo:                 # first real message: drop the sample chat
                self.messages, self._showing_demo = [], False
            self.messages.append({"platform": platform, **payload, "arrived": self._clock()})
            self._new_chat += 1
            return True
        if kind == "delete":
            before = len(self.messages)
            self.messages = [m for m in self.messages
                             if not (m["platform"] == platform and m.get("id") == payload)]
            return len(self.messages) != before
        if kind == "purge":
            before = len(self.messages)
            self.messages = [m for m in self.messages
                             if not (m["platform"] == platform and m.get("user_key") == payload)]
            return len(self.messages) != before
        if kind == "clear":
            before = len(self.messages)
            self.messages = [m for m in self.messages if m["platform"] != platform]
            return len(self.messages) != before
        if kind == "status":
            if platform not in self.sources:
                return False
            self.conn_status[platform] = payload
            self._refresh_status_labels()
            self._update_footer()
            return False
        if kind == "kick_chatroom":
            slug, room = payload
            if chat_sources.normalize_kick(self.settings.get("kick_channel", "")) == slug:
                for store in (self.settings, self.persisted_settings):
                    store["kick_chatroom_id"], store["kick_chatroom_slug"] = room, slug
                save_settings(self.persisted_settings)
            return False
        return False

    # -------------------------------------------------------------- connections

    def _configured_platforms(self) -> list[str]:
        return [p for p, info in PLATFORMS.items() if str(self.settings.get(info["channel_key"], "")).strip()]

    def start_connections(self):
        """(Re)start one background connection per platform that has a channel set."""
        self.stop_connections()
        generation = self._conn_generation

        def emit(event, generation=generation):
            # Runs on the connection's thread: only queue (tagged with this generation).
            self._ui_queue.put(event + (generation,))

        for platform in self._configured_platforms():
            target = self.settings[PLATFORMS[platform]["channel_key"]]
            options = {}
            if platform == "kick":
                same_channel = self.settings.get("kick_chatroom_slug", "") in ("", chat_sources.normalize_kick(target))
                options["chatroom_id"] = self.settings.get("kick_chatroom_id", "") if same_channel else ""
                options["pusher_key"] = self.settings.get("kick_pusher_key", "")
            source = chat_sources.SOURCES[platform](target, emit, **options)
            self.sources[platform] = source
            self.conn_status[platform] = ("connecting", "")
            source.start()
        self._refresh_status_labels()
        self._update_footer()

    def stop_connections(self):
        self._conn_generation += 1           # anything the old connections still send is ignored
        for source in self.sources.values():
            try:
                source.stop()
            except Exception:
                pass
        self.sources = {}
        self.conn_status = {}
        self._refresh_status_labels()

    def _refresh_status_labels(self):
        """Header: e.g. 'TWITCH ●  KICK …  YOUTUBE ○'. Only configured platforms are shown."""
        if not hasattr(self, "status_items"):
            return
        shown = []
        for platform, label in self.status_items.items():
            state, _detail = self.conn_status.get(platform, ("off", ""))
            if state == "off":
                continue
            color = {"live": self._platform_color(platform), "error": ERROR_COLOR}.get(state, MUTED_COLOR)
            text = f"{PLATFORMS[platform]['label']} {STATUS_GLYPH.get(state, '')}"
            shown.append((platform, label, text, color))
        signature = tuple((p, t, c) for p, _l, t, c in shown)
        if self._applied.get("status") == signature:
            return
        self._applied["status"] = signature
        for label in self.status_items.values():
            label.pack_forget()
        for _platform, label, text, color in shown:
            label.configure(text=text, fg=color)
            label.pack(side="left", padx=(6, 0))

    def _handle_command(self, command: str):
        """Runs on the Tk thread. Commands come from the hotkey and the tray menu."""
        if command == "toggle":
            self._set_click_through(not self._clickthrough_requested)
        elif command == "show_border":
            self._set_click_through(False)
        elif command == "hide_border":
            self._set_click_through(True)
        elif command == "toggle_sound":
            enabled = not bool(self.settings.get("sound_enabled", True))
            for store in (self.settings, self.persisted_settings):
                store["sound_enabled"] = enabled
            save_settings(self.persisted_settings)
            if self._settings_sound_var is not None:
                try:
                    self._settings_sound_var.set(enabled)     # keep an open Settings window in step
                except tk.TclError:
                    pass
        elif command == "settings":
            self.open_settings()
        elif command == "exit":
            self.close()

    # --------------------------------------------------------------- lifecycle

    def add_message(self, platform: str, username: str, message: str, msg_id: str = ""):
        """Add one message directly (Tk thread only). Connections use the queue instead."""
        if platform not in PLATFORMS:
            raise ValueError(f"platform must be one of {', '.join(PLATFORMS)}")
        self._handle_event(("chat", platform, {"id": msg_id, "username": username, "message": message,
                                               "user_key": username.lower(), "time": time.strftime("%H:%M")}))
        self.messages = self.messages[-MAX_STORED_MESSAGES:]
        self._render_messages()

    def close(self):
        # Put the border back first, so the saved position/size are the normal framed ones.
        if self._chrome_hidden:
            try:
                self._set_click_through(False)
            except Exception:
                pass
        # Save window position and size for the next launch.
        try:
            self.persisted_settings["window_x"] = self.root.winfo_x()
            self.persisted_settings["window_y"] = self.root.winfo_y()
            self.persisted_settings["window_width"] = self.root.winfo_width()
            self.persisted_settings["window_height"] = self.root.winfo_height()
            save_settings(self.persisted_settings)
        except tk.TclError:
            pass
        if self.hotkey is not None:
            try:
                # The hotkey thread unregisters its own hotkey when its loop exits.
                self.hotkey.stop()
            except Exception:
                pass
        if self.tray is not None:
            try:
                self.tray.stop()   # removes the tray icon so no "ghost" icon is left behind
            except Exception:
                pass
        self.stop_connections()
        self.sound.close()
        self.root.destroy()


def setup_logging() -> None:
    """Write connection steps and problems to connection_log.txt next to main.py.

    Kept small (two files of ~300 KB max). Send this file when something won't connect.
    """
    import logging
    import logging.handlers
    import platform as platform_module
    try:
        handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=300_000, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log = logging.getLogger("caustic")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("---- %s %s starting (Python %s, %s, %s) ----", APP_NAME, APP_VERSION,
             platform_module.python_version(), platform_module.platform(),
             "installed" if FROZEN else "run from folder")
    log.info("data folder: %s", DATA_DIR)

    # A windowed program has no console, so unexpected errors would vanish; log them instead.
    def log_crash(exc_type, exc, tb):
        log.error("unexpected error", exc_info=(exc_type, exc, tb))
    sys.excepthook = log_crash
    threading.excepthook = lambda args: log_crash(args.exc_type, args.exc_value, args.exc_traceback)


SINGLE_INSTANCE_MUTEX = "Local\\CausticOverlay.SingleInstance"
_instance_mutex = None    # kept alive for the whole run


def already_running() -> bool:
    """True if another copy is open. Brings that copy's window to the front."""
    global _instance_mutex
    if not IS_WINDOWS:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        _instance_mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
        if ctypes.get_last_error() != 183:            # ERROR_ALREADY_EXISTS
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, APP_NAME)
        if hwnd:
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.ShowWindow(hwnd, 9)                # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False        # if the check itself fails, just start normally


def migrate_old_data() -> str | None:
    """Installed app, first run after the rename: bring over MultiChat Overlay's settings + sounds.

    Runs once (a marker file remembers). The old folder is left as it was, just in case.
    Returns a short description of what moved, or None if nothing to do.
    """
    import shutil
    if OLD_DATA_DIR is None or not OLD_DATA_DIR.is_dir():
        return None
    marker = DATA_DIR / MIGRATED_MARKER
    if marker.exists():
        return None
    moved = []
    old_settings = OLD_DATA_DIR / "settings.json"
    if old_settings.exists():
        # The old program's live settings beat the copy the installer seeded from the build folder.
        shutil.copy2(old_settings, CONFIG_PATH)
        moved.append("settings")
    old_sounds = OLD_DATA_DIR / "sounds"
    if old_sounds.is_dir():
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        for sound in old_sounds.iterdir():
            if sound.is_file() and not (SOUNDS_DIR / sound.name).exists():
                shutil.copy2(sound, SOUNDS_DIR / sound.name)
                moved.append(sound.name)
    marker.write_text(f"Settings were copied from {OLD_DATA_DIR} on {time.strftime('%Y-%m-%d %H:%M')}.\n",
                      encoding="utf-8")
    return ", ".join(moved) if moved else "nothing to copy"


def main():
    if already_running():
        return
    try:
        moved = migrate_old_data()
    except OSError as exc:
        moved = f"failed: {exc}"
    setup_logging()
    if moved:
        import logging
        logging.getLogger("caustic").info("moved data from %s: %s", OLD_DATA_DIR, moved)
    if IS_WINDOWS:
        try:
            # Own taskbar identity, so the taskbar shows our icon instead of Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    root = tk.Tk()

    def log_tk_error(exc_type, exc, tb):
        import logging
        logging.getLogger("caustic").error("error in the window", exc_info=(exc_type, exc, tb))
    root.report_callback_exception = log_tk_error
    CausticOverlay(root)
    root.mainloop()


if __name__ == "__main__":
    main()
