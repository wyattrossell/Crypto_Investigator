"""
Flag packs: sharing wallet designations between agencies as files.

EXPORT bundles this agency's own wallet flags (address, chain, reason,
date - never case names or numbers) with the agency's name/contact from
Settings, an export timestamp and a SHA-256 over the canonical flag list.

IMPORT verifies the format and the hash, then stores the flags as a
SEPARATE label source ('pack:<agency>') with category 'shared_flag'. They
raise a finding ("traced funds reached a wallet flagged by <Agency>") and
mark the map, but they are never merged into this agency's own flags,
never treated as a stopping point, and every report names the originating
agency. A second import from the same agency replaces the first (packs are
snapshots, not deltas).

The hash detects corruption or accidental edits; it does not prove who
made the file - agencies exchange packs through channels they already
trust (the design decision recorded in the changelog).
"""

import hashlib
import json
import re

from app import config, database
from app.labels import store as label_store


def _canonical(items: list) -> str:
    return json.dumps(items, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _digest(items: list) -> str:
    return hashlib.sha256(_canonical(items).encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "agency"


def build_pack(agency: dict, note: str = "") -> dict:
    """The exportable pack for this agency's current flags."""
    items = []
    for flag in database.flags_list():
        items.append({
            "address": flag["address"],
            "chain": flag["chain"],
            "reason": flag.get("reason") or "",
            "flagged_utc": flag.get("created_utc") or "",
        })
    items.sort(key=lambda i: (i["chain"], i["address"]))
    name = (agency.get("agency_name") or "").strip()
    contact_bits = [agency.get("agency_unit"), agency.get("officer_name"),
                    agency.get("officer_email"), agency.get("agency_phone")]
    contact = ", ".join(b.strip() for b in contact_bits if b and b.strip())
    return {
        "format": config.FLAG_PACK_FORMAT,
        "generator": f"{config.APP_NAME} v{config.APP_VERSION}",
        "agency": {"name": name or "[agency name not set in Settings]",
                   "contact": contact},
        "exported_utc": database.utc_now_iso(),
        "note": note or ("Wallet addresses designated as fraud-related by "
                         "the exporting agency, for use as a corroborating "
                         "label source by the importing agency. Each "
                         "designation is the exporting agency's own; verify "
                         "before relying on it in legal process."),
        "flag_count": len(items),
        "flags": items,
        "sha256": _digest(items),
    }


def verify_pack(payload: dict) -> tuple:
    """Validate a pack. Returns (items, agency_name, contact, exported_utc,
    sha256). Raises ValueError with a plain-language reason."""
    if not isinstance(payload, dict):
        raise ValueError("The file is not a flag pack (not a JSON object).")
    if payload.get("format") != config.FLAG_PACK_FORMAT:
        raise ValueError(
            f"Unsupported pack format '{payload.get('format')}' (expected "
            f"{config.FLAG_PACK_FORMAT}).")
    items = payload.get("flags")
    if not isinstance(items, list):
        raise ValueError("The pack has no 'flags' list.")
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("A flag entry is not an object.")
        chain = str(item.get("chain") or "").strip().lower()
        address = str(item.get("address") or "").strip()
        if not chain or not address:
            raise ValueError("A flag entry is missing its address or chain.")
        cleaned.append({
            "address": address,
            "chain": chain,
            "reason": str(item.get("reason") or ""),
            "flagged_utc": str(item.get("flagged_utc") or ""),
        })
    claimed = str(payload.get("sha256") or "").lower()
    actual = _digest(sorted(items, key=lambda i: (
        str(i.get("chain", "")), str(i.get("address", "")))))
    # Hash the list exactly as sent (sorted the way build_pack sorts it).
    if claimed != actual:
        raise ValueError(
            "Integrity check failed: the pack's SHA-256 does not match its "
            "contents. The file was altered or corrupted in transit - ask "
            "the sending agency for a fresh export.")
    agency = payload.get("agency") or {}
    name = str(agency.get("name") or "").strip()
    if not name:
        raise ValueError("The pack does not name the exporting agency.")
    return (cleaned, name, str(agency.get("contact") or ""),
            payload.get("exported_utc"), actual)


def import_pack(payload: dict) -> dict:
    """Verify and import a pack, replacing any earlier pack from the same
    agency. Returns a summary."""
    items, name, contact, exported_utc, sha256 = verify_pack(payload)
    existing = database.label_pack_by_agency(name)
    if existing:
        database.label_pack_remove(existing["id"])
    source = f"{config.LABEL_SOURCE_PACK_PREFIX}{_slug(name)}"
    rows = []
    for item in items:
        entity = f"Flagged by {name}"
        if item["reason"]:
            entity += f": {item['reason']}"
        if item["flagged_utc"]:
            entity += f" (flagged {item['flagged_utc'][:10]})"
        rows.append((
            label_store.normalise_address(item["address"], item["chain"]),
            item["chain"], entity, config.LABEL_CATEGORY_SHARED_FLAG,
            config.CONFIDENCE_MEDIUM))
    database.labels_replace_source(source, rows)
    pack_id = database.label_pack_add(
        source, name, contact, exported_utc, sha256, len(rows),
        note=str(payload.get("note") or "")[:500])
    return {"pack_id": pack_id, "agency": name, "imported": len(rows),
            "replaced_previous": bool(existing), "source": source}


def list_packs() -> list:
    return database.label_packs_list()


def remove_pack(pack_id: int) -> bool:
    return database.label_pack_remove(pack_id)
