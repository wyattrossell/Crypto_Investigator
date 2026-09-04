"""
Generate the complete program listing for the current version.

Usage:  python tools/make_listing.py

Writes docs/listings/v{VERSION}-full-listing.txt containing every source
file (code, config, docs, packaging scripts) concatenated with clear file
headers, so each release ships a single reviewable/diffable artefact of the
whole program. Binary/vendored/generated assets are listed by name and
size only.

Files are DISCOVERED, not hand-listed: earlier versions kept a manual list
that silently fell behind as modules were added (the v0.8-v0.10 listings
omit several files for that reason). Anything matching the source
patterns below and not excluded is included, so a new module cannot be
left out of the record by forgetting to register it.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LISTINGS_DIR = PROJECT_ROOT / "docs" / "listings"

# Files that must lead the listing, in reading order.
LEAD_FILES = [
    "VERSION", "requirements.txt", "requirements-build.txt",
    "README.md", "CHANGELOG.md", "LICENSE",
    "run.py", "run.pyw",
]

# Text source extensions included in full.
SOURCE_SUFFIXES = {".py", ".pyw", ".md", ".txt", ".json", ".html", ".css",
                   ".js", ".spec", ".iss", ".ps1", ".yaml", ".yml",
                   ".toml", ".cfg", ".ini"}
EXTENSIONLESS_SOURCES = {"VERSION", "LICENSE", ".gitignore"}

# Directories never inlined (case data, generated artefacts, caches).
EXCLUDED_DIRS = {"__pycache__", ".git", ".claude", "build", "dist",
                 "listings", "validation", "reports", "logs", "vendor",
                 ".venv", "venv"}
# Individual files never inlined (they are DATA or local conveniences).
EXCLUDED_FILES = {"investigator.db", "investigator.db-wal",
                  "investigator.db-shm", "data-location.txt"}

# Present but intentionally not inlined (vendored/binary/generated) -
# listed by name and size.
MENTION_ONLY_SUFFIXES = {".ico", ".png", ".jpg", ".pdf", ".lnk", ".db",
                         ".zip"}
MENTION_ONLY_DIRS = {"vendor"}

HEADER_RULE = "=" * 78


def _excluded(path: Path) -> bool:
    relative_parts = path.relative_to(PROJECT_ROOT).parts
    return any(part in EXCLUDED_DIRS for part in relative_parts[:-1]) or \
        path.name in EXCLUDED_FILES


def discover() -> tuple:
    """Return (inline_files, mention_only_files) as project-relative
    POSIX paths, lead files first, the rest sorted by path."""
    inline, mention = [], []
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        parts = path.relative_to(PROJECT_ROOT).parts
        if any(part in MENTION_ONLY_DIRS for part in parts[:-1]) or \
                path.suffix.lower() in MENTION_ONLY_SUFFIXES:
            if not any(part in (EXCLUDED_DIRS - MENTION_ONLY_DIRS)
                       for part in parts[:-1]):
                mention.append(rel)
            continue
        if _excluded(path):
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES or \
                path.name in EXTENSIONLESS_SOURCES:
            inline.append(rel)
    lead = [f for f in LEAD_FILES if f in inline]
    rest = [f for f in inline if f not in lead]
    return lead + rest, mention


def main() -> None:
    version = (PROJECT_ROOT / "VERSION").read_text().strip()
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = LISTINGS_DIR / f"v{version}-full-listing.txt"
    inline, mention = discover()

    parts = [
        f"CRYPTO INVESTIGATOR v{version} - COMPLETE PROGRAM LISTING",
        f"{len(inline)} source files inlined below; vendored/binary assets "
        f"listed at the end.",
        "",
        "FILES INLINED:",
        *[f"  {rel}" for rel in inline],
        "",
    ]
    for relative in inline:
        path = PROJECT_ROOT / relative
        parts.append(HEADER_RULE)
        parts.append(f"FILE: {relative}  ({path.stat().st_size:,} bytes)")
        parts.append(HEADER_RULE)
        parts.append(path.read_text(encoding="utf-8"))
        parts.append("")

    parts.append(HEADER_RULE)
    parts.append("VENDORED / BINARY ASSETS (not inlined)")
    parts.append(HEADER_RULE)
    for relative in mention:
        path = PROJECT_ROOT / relative
        parts.append(f"{relative}  ({path.stat().st_size:,} bytes)")

    output_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {output_path} ({output_path.stat().st_size:,} bytes; "
          f"{len(inline)} files inlined)")


if __name__ == "__main__":
    main()
