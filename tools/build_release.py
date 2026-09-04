"""
Build the distributable Windows release.

Usage:  python tools/build_release.py [--skip-installer] [--skip-zip]

Steps (each prints what it did):

1. Refresh the Windows version resource from VERSION.
2. PyInstaller: dist/CryptoInvestigator/  (windowed exe + support files).
3. Portable zip: dist/CryptoInvestigator-v{VERSION}-portable.zip
   (for agencies that cannot run installers: unzip anywhere and run
   CryptoInvestigator.exe).
4. Inno Setup: dist/CryptoInvestigator-Setup-v{VERSION}.exe - only when
   ISCC.exe is found (Inno Setup 6, https://jrsoftware.org/isinfo.php; or
   set the ISCC environment variable to its path). Otherwise this step is
   skipped with instructions; the zip is still produced.

Case data is never part of any artefact: the build reads only source files
and the default label files under data/labels.
"""

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "packaging" / "crypto_investigator.spec"
ISS = ROOT / "packaging" / "installer.iss"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"
APP_FOLDER = DIST / "CryptoInvestigator"

ISCC_CANDIDATES = [
    os.environ.get("ISCC", ""),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
        / "Inno Setup 6" / "ISCC.exe"),
]


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def read_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def refresh_version_info(version: str) -> None:
    """Rewrite the numeric + string versions in the Windows resource file."""
    parts = [int(p) for p in re.findall(r"\d+", version)][:3]
    while len(parts) < 3:
        parts.append(0)
    tuple_text = f"({parts[0]}, {parts[1]}, {parts[2]}, 0)"
    text = VERSION_INFO.read_text(encoding="utf-8")
    text = re.sub(r"filevers=\([^)]*\)", f"filevers={tuple_text}", text)
    text = re.sub(r"prodvers=\([^)]*\)", f"prodvers={tuple_text}", text)
    text = re.sub(r"'FileVersion', '[^']*'",
                  f"'FileVersion', '{version}'", text)
    text = re.sub(r"'ProductVersion', '[^']*'",
                  f"'ProductVersion', '{version}'", text)
    VERSION_INFO.write_text(text, encoding="utf-8")
    print(f"version resource -> {version}")


def run_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is not installed: "
                 "python -m pip install -r requirements-build.txt")
    if APP_FOLDER.exists():
        shutil.rmtree(APP_FOLDER)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm",
         "--clean", "--distpath", str(DIST), "--workpath", str(BUILD)],
        check=True, cwd=str(ROOT))
    exe = APP_FOLDER / "CryptoInvestigator.exe"
    if not exe.exists():
        sys.exit("PyInstaller finished but the executable is missing")
    size_mb = sum(p.stat().st_size for p in APP_FOLDER.rglob("*")
                  if p.is_file()) / 1e6
    print(f"built {exe} ({size_mb:.0f} MB folder)")


def make_portable_zip(version: str) -> Path:
    target = DIST / f"CryptoInvestigator-v{version}-portable.zip"
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(APP_FOLDER.rglob("*")):
            if path.is_file():
                archive.write(path, Path("CryptoInvestigator")
                              / path.relative_to(APP_FOLDER))
        for extra in ("README.md", "CHANGELOG.md", "LICENSE"):
            if (ROOT / extra).exists():
                archive.write(ROOT / extra,
                              Path("CryptoInvestigator") / extra)
    print(f"wrote {target} ({target.stat().st_size / 1e6:.0f} MB)")
    return target


def find_iscc():
    for candidate in ISCC_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    found = shutil.which("ISCC")
    return found


def run_inno(version: str) -> None:
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup (ISCC.exe) not found - installer step skipped.\n"
              "  Install Inno Setup 6 from https://jrsoftware.org/isinfo.php\n"
              "  (or set the ISCC environment variable to ISCC.exe) and "
              "re-run this script.")
        return
    subprocess.run([iscc, f"/DAppVersion={version}", str(ISS)],
                   check=True, cwd=str(ROOT))
    setup = DIST / f"CryptoInvestigator-Setup-v{version}.exe"
    print(f"wrote {setup}")


def main() -> None:
    if sys.platform != "win32":
        sys.exit("The release build targets Windows; run this on Windows.")
    version = read_version()
    step(f"Crypto Investigator v{version}: release build")
    DIST.mkdir(exist_ok=True)

    step("1. Version resource")
    refresh_version_info(version)

    step("2. PyInstaller")
    run_pyinstaller()

    if "--skip-zip" not in sys.argv:
        step("3. Portable zip")
        make_portable_zip(version)

    if "--skip-installer" not in sys.argv:
        step("4. Installer (Inno Setup)")
        run_inno(version)

    step("Done")
    for artefact in sorted(DIST.iterdir()):
        if artefact.is_file():
            print(f"  {artefact.name}  "
                  f"({artefact.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
