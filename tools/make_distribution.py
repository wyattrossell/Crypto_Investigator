"""
Assemble the single ZIP an agency receives.

Usage:  python tools/make_distribution.py        (after tools/build_release.py)

Writes dist/CryptoInvestigator-v{VERSION}-distribution.zip containing one
folder with everything needed to install, run and understand the program:

    CryptoInvestigator-v{VERSION}/
        README-FIRST.txt                    what this is, how to install
        CryptoInvestigator-Setup-v*.exe      the installer (recommended)
        CryptoInvestigator-v*-portable.zip   no-install alternative
        SHA256SUMS.txt                       hashes of the two artefacts
        CHANGELOG.md
        docs/Setup-Guide.md                  for agency IT
        docs/Investigator-Guide.md           for investigators
        docs/Overview-v*.pdf                 two-page overview
        docs/Technical-Validation-v*.pdf     methodology for experts/court
        LICENSE                              when present in the repository

Case data is never part of it: only files under dist/ and docs/ are read.
"""

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    setup = DIST / f"CryptoInvestigator-Setup-v{version}.exe"
    portable = DIST / f"CryptoInvestigator-v{version}-portable.zip"
    for required in (setup, portable):
        if not required.exists():
            sys.exit(f"Missing {required.name}: run tools/build_release.py "
                     f"first (the installer needs Inno Setup 6).")
    validation = ROOT / "docs" / "validation"
    overview = validation / f"v{version}-overview.pdf"
    technical = validation / f"v{version}-technical-validation.pdf"
    for required in (overview, technical):
        if not required.exists():
            sys.exit(f"Missing {required.name}: run tools/make_docs.py first.")

    hashes = {setup.name: sha256(setup), portable.name: sha256(portable)}
    sums = "".join(f"{digest}  {name}\n" for name, digest in hashes.items())

    readme = f"""CRYPTO INVESTIGATOR v{version}
Cryptocurrency tracing and warrant preparation for law-enforcement investigators.

WHAT IS IN THIS FOLDER
  CryptoInvestigator-Setup-v{version}.exe     Installer (recommended). Windows 10/11, 64-bit.
  CryptoInvestigator-v{version}-portable.zip  No-install alternative: unzip anywhere, run
                                             CryptoInvestigator\\CryptoInvestigator.exe
  SHA256SUMS.txt                             Hashes of the two files above - verify with
                                             certutil -hashfile <file> SHA256
  docs\\Setup-Guide.md                        For agency IT: install, data location, firewall
                                             hosts, optional API keys, security notes
  docs\\Investigator-Guide.md                 How to use it on a case
  docs\\Overview-v{version}.pdf                Two-page overview
  docs\\Technical-Validation-v{version}.pdf    Full methodology for outside experts / court
  CHANGELOG.md                               What each version added and its known limits

INSTALL (about one minute)
  1. Run CryptoInvestigator-Setup-v{version}.exe. No administrator rights are needed;
     it installs for the current Windows user and creates a Start-menu entry and a
     desktop icon.
  2. Windows SmartScreen may say the publisher is unknown (the build is not
     code-signed). Choose "More info" then "Run anyway".
  3. Open Crypto Investigator from the desktop icon. Your browser opens the tool;
     a small icon in the notification area shows it is running (right-click it to
     reopen the tab or to quit). No command window ever appears.

FIRST RUN (once)
  In Settings, press the four label downloads (OFAC sanctions list, exchange label
  packs, scam blacklist, Ethereum name tags) and fill in the agency letterhead.
  No accounts or API keys are required; optional free keys only raise rate limits.

WHERE YOUR DATA GOES
  %LOCALAPPDATA%\\CryptoInvestigator\\data  (one folder per Windows user).
  Upgrades and uninstalls never delete it. To keep evidence elsewhere, see the
  Setup Guide (data-location.txt).

NETWORK
  Outbound HTTPS only, to public block explorers, the U.S. Treasury (OFAC) and
  GitHub (label lists). Nothing is uploaded; there is no telemetry. The full host
  list is in the Setup Guide.

SUPPORT
  https://github.com/wyattrossell/Crypto_Investigator
"""

    target = DIST / f"CryptoInvestigator-v{version}-distribution.zip"
    if target.exists():
        target.unlink()
    top = f"CryptoInvestigator-v{version}"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{top}/README-FIRST.txt", readme)
        archive.writestr(f"{top}/SHA256SUMS.txt", sums)
        archive.write(setup, f"{top}/{setup.name}")
        archive.write(portable, f"{top}/{portable.name}")
        archive.write(ROOT / "CHANGELOG.md", f"{top}/CHANGELOG.md")
        archive.write(ROOT / "docs" / "setup-guide.md",
                      f"{top}/docs/Setup-Guide.md")
        archive.write(ROOT / "docs" / "investigator-guide.md",
                      f"{top}/docs/Investigator-Guide.md")
        archive.write(overview, f"{top}/docs/Overview-v{version}.pdf")
        archive.write(technical,
                      f"{top}/docs/Technical-Validation-v{version}.pdf")
        if (ROOT / "LICENSE").exists():
            archive.write(ROOT / "LICENSE", f"{top}/LICENSE")
    print(f"Wrote {target} ({target.stat().st_size / 1e6:.0f} MB)")
    print("Contents:")
    with zipfile.ZipFile(target) as archive:
        for info in archive.infolist():
            print(f"  {info.filename:60} {info.file_size / 1e6:6.2f} MB")
    print("SHA-256 of the distribution zip:", sha256(target))


if __name__ == "__main__":
    main()
