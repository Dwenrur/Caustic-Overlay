# PyInstaller recipe for Caustic Overlay. build_installer.py runs this; you don't need to.
# Makes a folder ".build/dist/Caustic Overlay/" containing "Caustic Overlay.exe".
import os
import sys
from pathlib import Path

here = Path(SPECPATH)                     # noqa: F821  (provided by PyInstaller)
name = "Caustic Overlay"
version_file = os.environ.get("MCO_VERSION_FILE")

a = Analysis(                              # noqa: F821
    [str(here / "main.py")],
    pathex=[str(here)],
    datas=[
        (str(here / "icon.ico"), "."),                      # window / tray icon
        (str(here / "sounds" / "chime.wav"), "sounds"),     # built-in message sound
    ],
    hiddenimports=["chat_sources", "sound_player"],
    excludes=["numpy", "PIL", "matplotlib", "pytest"],      # never used; keeps the build small
    noarchive=False,
)
pyz = PYZ(a.pure)                          # noqa: F821
exe = EXE(                                 # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,                 # "one folder" build: starts fast, fewer antivirus false alarms
    name=name,
    icon=str(here / "icon.ico"),
    console=False,                         # no black console window
    upx=False,
    version=version_file if (version_file and sys.platform == "win32") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=name, upx=False)   # noqa: F821
