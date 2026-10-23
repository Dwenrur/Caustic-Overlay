"""Build Caustic-Overlay-Setup-v<N>.exe. Double-click build_installer.bat to run this.

What it does (all inside this folder unless noted):
  1. Makes a private Python environment in .build\\venv and installs PyInstaller there
     (nothing is added to your normal Python).
  2. Turns main.py + its files into "Caustic Overlay.exe" (.build\\dist\\Caustic Overlay\\).
  3. Finds Inno Setup (free installer maker). If it's missing, installs it with Windows'
     winget; Windows may ask you to allow that.
  4. Compiles installer.iss into Caustic-Overlay-Setup-v<N>.exe next to this file.
Everything is also written to build_log.txt, in case something goes wrong.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import time
import venv
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / ".build"
VENV = BUILD / "venv"
DIST = BUILD / "dist"
WORK = BUILD / "work"
SEED = BUILD / "seed"
LOG_FILE = HERE / "build_log.txt"
APP_NAME = "Caustic Overlay"
PYINSTALLER = "pyinstaller==6.22.3"        # pinned so builds are repeatable
IS_WINDOWS = sys.platform == "win32"


class BuildError(Exception):
    pass


def say(message: str = "") -> None:
    print(message, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(message + "\n")


def run(cmd: list, what: str) -> None:
    """Run a command, showing and logging its output; raise BuildError if it fails."""
    say(f"> {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", cwd=HERE)
    except OSError as exc:
        raise BuildError(f"{what}: couldn't start it ({exc})")
    lines = []
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        with LOG_FILE.open("a", encoding="utf-8") as log:
            log.write("    " + line + "\n")
        if not line.startswith(("  ", "INFO:")):       # keep the window readable; full detail is in the log
            print("    " + line[:160], flush=True)
    if proc.wait() != 0:
        tail = "\n".join(lines[-8:])
        raise BuildError(f"{what} failed (exit code {proc.returncode}).\n{tail}")


def app_version() -> str:
    text = (HERE / "main.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION = "v?([0-9][0-9.]*)"', text, re.M)
    if not m:
        raise BuildError("couldn't find APP_VERSION in main.py")
    return m.group(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def step_pyinstaller(version: str) -> Path:
    say("\n[1/4] Getting PyInstaller (private copy, only used for building)…")
    if not venv_python().exists():
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    run([venv_python(), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", PYINSTALLER],
        "Installing PyInstaller")

    say(f"\n[2/4] Building {APP_NAME}.exe…")
    version_file = BUILD / "version_info.txt"
    write_version_file(version_file, version)
    os.environ["MCO_VERSION_FILE"] = str(version_file)
    run([venv_python(), "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", DIST, "--workpath", WORK, HERE / "CausticOverlay.spec"], "PyInstaller")
    exe = DIST / APP_NAME / (f"{APP_NAME}.exe" if IS_WINDOWS else APP_NAME)
    if not exe.exists():
        raise BuildError(f"PyInstaller finished but {exe} is missing")
    say(f"    built {exe}")
    return exe


def write_version_file(path: Path, version: str) -> None:
    """Windows 'Details' tab info for the exe (Properties → Details)."""
    parts = [int(p) for p in version.split(".") if p.isdigit()][:4]
    parts += [0] * (4 - len(parts))
    t = tuple(parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Izzy'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def step_seed() -> None:
    """Copy this folder's settings.json and your own sounds, so the installed app starts with them."""
    shutil.rmtree(SEED, ignore_errors=True)
    (SEED / "sounds").mkdir(parents=True)
    carried = []
    if (HERE / "settings.json").exists():
        shutil.copy2(HERE / "settings.json", SEED / "settings.json")
        carried.append("settings")
    for sound in (HERE / "sounds").glob("*"):
        if sound.is_file() and sound.name.lower() != "chime.wav":
            shutil.copy2(sound, SEED / "sounds" / sound.name)
            carried.append(sound.name)
    say(f"    carrying over: {', '.join(carried) if carried else 'nothing (fresh settings)'}")


def find_iscc() -> Path | None:
    """Look for Inno Setup's compiler in the usual places (any version)."""
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
    for root in filter(None, roots):
        for hit in sorted(glob.glob(os.path.join(root, "Inno Setup*", "ISCC.exe")), reverse=True):
            return Path(hit)
    if IS_WINDOWS:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for base in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                try:
                    with winreg.OpenKey(hive, base) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            sub = winreg.EnumKey(key, i)
                            if not sub.lower().startswith("inno setup"):
                                continue
                            with winreg.OpenKey(key, sub) as app:
                                location = winreg.QueryValueEx(app, "InstallLocation")[0]
                            candidate = Path(location) / "ISCC.exe"
                            if candidate.exists():
                                return candidate
                except OSError:
                    continue
    return None


def step_inno() -> Path:
    say("\n[3/4] Looking for Inno Setup…")
    iscc = find_iscc()
    if iscc:
        say(f"    found {iscc}")
        return iscc
    if not shutil.which("winget"):
        raise BuildError("Inno Setup isn't installed and Windows' winget isn't available to install it.\n"
                         "Install Inno Setup from the page that just opened, then run build_installer.bat again.")
    say("    not found, installing Inno Setup with winget (Windows may ask you to allow it)…")
    try:
        run(["winget", "install", "--id", "JRSoftware.InnoSetup", "--exact", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"], "Installing Inno Setup")
    except BuildError as exc:
        say(f"    {exc}")
    for _ in range(10):                      # give the installer a moment to finish writing files
        iscc = find_iscc()
        if iscc:
            say(f"    installed: {iscc}")
            return iscc
        time.sleep(1)
    raise BuildError("Inno Setup didn't install.\n"
                     "Install it from the page that just opened, then run build_installer.bat again.")


def step_compile(iscc: Path, version: str) -> Path:
    say("\n[4/4] Making the installer…")
    run([iscc, "/Q", f"/DAppVersion={version}", HERE / "installer.iss"], "Inno Setup")
    setup = HERE / f"Caustic-Overlay-Setup-v{version}.exe"
    if not setup.exists():
        raise BuildError(f"Inno Setup finished but {setup.name} is missing")
    return setup


def main(argv: list[str]) -> int:
    LOG_FILE.write_text(f"{APP_NAME} build — {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Python {sys.version.split()[0]} at {sys.executable}\n", encoding="utf-8")
    skip_installer = "--exe-only" in argv
    try:
        if sys.version_info < (3, 10):
            raise BuildError(f"Python 3.10 or newer is needed (this is {sys.version.split()[0]}).")
        if not IS_WINDOWS and not skip_installer:
            raise BuildError("The installer can only be built on Windows.")
        version = app_version()
        say(f"Building {APP_NAME} v{version}. This takes a few minutes the first time.")
        step_pyinstaller(version)
        step_seed()
        if skip_installer:
            say("\nDone (exe only).")
            return 0
        setup = step_compile(step_inno(), version)
    except BuildError as exc:
        say(f"\nBUILD FAILED: {exc}\n\nThe full details are in build_log.txt.")
        if "Install Inno Setup" in str(exc) or "Install it from" in str(exc):
            webbrowser.open("https://jrsoftware.org/isdl.php")
        return 1
    except Exception:                              # unexpected: still leave a useful log
        import traceback
        say("\nBUILD FAILED (unexpected error):\n" + traceback.format_exc())
        return 1
    size_mb = setup.stat().st_size / 1_048_576
    say(f"\nDONE! {setup.name} ({size_mb:.1f} MB) is in this folder.")
    say(f"Double-click it to install {APP_NAME}.")
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(setup)])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
