"""
Label store: loads attribution data into the `labels` table and answers
"who is this address?" queries for the tracing engine.

Two sources in v0.1:

* seed_community - bundled exchange wallet list (data/labels/*.json),
                   imported at MEDIUM confidence because it derives from
                   public explorer tags, not official records.
* ofac_sdn       - digital-currency addresses parsed live from the official
                   US Treasury SDN list, imported at HIGH confidence.
"""

import json
import re
import xml.etree.ElementTree as ElementTree

import yaml

from app import config, database
from app.providers.base import ProviderClient

# OFAC idType values look like "Digital Currency Address - XBT".
OFAC_DIGITAL_CURRENCY_PREFIX = "Digital Currency Address"

# Maps the OFAC asset suffix to our chain identifiers where we support the
# chain; unmapped assets are stored under the raw suffix (lowercased) so the
# data is retained for later phases.
OFAC_ASSET_TO_CHAIN = {
    "XBT": config.CHAIN_BITCOIN,
    "ETH": config.CHAIN_ETHEREUM,
    "USDC": config.CHAIN_ETHEREUM,
    "LTC": config.CHAIN_LITECOIN,
    "XMR": config.CHAIN_MONERO,
    "TRX": config.CHAIN_TRON,
}


def normalise_address(address: str, chain: str) -> str:
    """Canonical form used everywhere: EVM addresses are lowercased,
    UTXO-chain addresses are case-sensitive and kept as-is."""
    if chain == config.CHAIN_ETHEREUM or address.startswith("0x"):
        return address.lower()
    return address


def _chain_for_ofac_entry(asset_suffix: str, address: str) -> str:
    """Choose a chain for an OFAC address: explicit map first, then address
    format (0x... is EVM, T... is Tron), else the raw suffix."""
    if asset_suffix in OFAC_ASSET_TO_CHAIN:
        return OFAC_ASSET_TO_CHAIN[asset_suffix]
    if address.startswith("0x"):
        return config.CHAIN_ETHEREUM
    if address.startswith("T") and len(address) == 34:
        return config.CHAIN_TRON
    return asset_suffix.lower()


class OfacClient(ProviderClient):
    """Downloads the official SDN XML (custody-logged like every source)."""
    provider_name = "ofac"


def refresh_ofac_labels() -> dict:
    """Download and parse the SDN list; replace all 'ofac_sdn' labels.
    Returns {"addresses": n, "entities": m} for UI feedback."""
    client = OfacClient()
    try:
        # Never cached: the sanctions list is a living document. The custody
        # log records exactly which snapshot (by hash) this run used.
        raw_xml = client.fetch(config.OFAC_SDN_XML_URL, cacheable=False)
    finally:
        client.close()

    root = ElementTree.fromstring(raw_xml)
    rows = []            # (address, chain, entity_name, category, confidence)
    entity_names = set()

    for element in root.iter():
        if not element.tag.endswith("sdnEntry"):
            continue
        entry = element
        # Entity display name: "lastName" holds the organisation/person name.
        name_parts = []
        for child in entry:
            if child.tag.endswith("firstName") and child.text:
                name_parts.append(child.text)
            if child.tag.endswith("lastName") and child.text:
                name_parts.append(child.text)
        entity_name = " ".join(name_parts) or "OFAC SDN entity"

        # Digital-currency addresses live in idList/id records.
        for id_element in entry.iter():
            if not id_element.tag.endswith("}id") and id_element.tag != "id":
                continue
            id_type, id_number = None, None
            for field in id_element:
                if field.tag.endswith("idType"):
                    id_type = (field.text or "").strip()
                elif field.tag.endswith("idNumber"):
                    id_number = (field.text or "").strip()
            if not id_type or not id_number:
                continue
            if not id_type.startswith(OFAC_DIGITAL_CURRENCY_PREFIX):
                continue
            asset_suffix = id_type.split("-")[-1].strip()
            chain = _chain_for_ofac_entry(asset_suffix, id_number)
            rows.append((
                normalise_address(id_number, chain), chain,
                f"OFAC SDN: {entity_name}", "sanctioned",
                config.CONFIDENCE_HIGH,
            ))
            entity_names.add(entity_name)

    database.labels_replace_source("ofac_sdn", rows)
    return {"addresses": len(rows), "entities": len(entity_names)}


class GraphSenseClient(ProviderClient):
    """Downloads GraphSense TagPack files (custody-logged like every
    source)."""
    provider_name = "graphsense"


class ScamSnifferClient(ProviderClient):
    """Downloads the ScamSniffer blacklist (custody-logged)."""
    provider_name = "scamsniffer"


def refresh_graphsense_labels() -> dict:
    """Download and import GraphSense TagPack exchange/mixer attribution;
    replaces all 'graphsense_tagpack' labels.

    Imported at MEDIUM confidence: TagPacks are community-maintained
    (MIT-licensed) attribution derived from public sources, not official
    records. Each label's entity text carries the pack's own source and
    last-modified date so reports can show provenance honestly.

    Returns {"packs": n, "labels": m, "skipped_packs": [...]}."""
    client = GraphSenseClient()
    rows = []
    packs_used = 0
    skipped = []
    name_pattern = re.compile(config.GRAPHSENSE_PACK_NAME_PATTERN,
                              re.IGNORECASE)
    try:
        listing = json.loads(client.fetch(
            config.GRAPHSENSE_PACK_LISTING_URL, cacheable=False))
        for entry in listing:
            name = entry.get("name", "")
            if not name.endswith(".yaml") or not name_pattern.search(name):
                continue
            if entry.get("size", 0) > config.GRAPHSENSE_MAX_PACK_BYTES:
                skipped.append(f"{name} (too large)")
                continue
            download_url = entry.get("download_url")
            if not download_url:
                skipped.append(f"{name} (no download URL)")
                continue
            try:
                pack = yaml.safe_load(client.fetch(download_url,
                                                   cacheable=False))
            except Exception as exc:
                skipped.append(f"{name} ({exc})")
                continue
            if not isinstance(pack, dict):
                skipped.append(f"{name} (unexpected format)")
                continue
            # TagPack schema: header fields (label, currency, category) are
            # defaults inherited by every tag that does not override them.
            pack_category = str(pack.get("category", "")).lower()
            pack_currency = str(pack.get("currency", "")).upper()
            pack_label = str(pack.get("label", "") or "").strip()
            pack_lastmod = str(pack.get("lastmod", "") or "")[:10]
            pack_source = str(pack.get("source", "") or "")
            provenance = " [GraphSense TagPack"
            if pack_lastmod:
                provenance += f", as of {pack_lastmod}"
            provenance += "]"
            count_before = len(rows)
            for tag in pack.get("tags") or []:
                if not isinstance(tag, dict):
                    continue
                address = str(tag.get("address", "")).strip()
                label = str(tag.get("label", "")).strip() or pack_label
                if not address or not label:
                    continue
                currency = str(tag.get("currency",
                                       pack_currency)).upper()
                chain = config.TAGPACK_CURRENCY_TO_CHAIN.get(
                    currency, currency.lower())
                if not chain:
                    continue
                category = str(tag.get("category",
                                       pack_category)).lower()
                if category not in ("exchange", "mixer"):
                    # Only categories the engine understands as stopping
                    # points are imported; anything else would be noise.
                    continue
                rows.append((
                    normalise_address(address, chain), chain,
                    label + provenance, category,
                    config.CONFIDENCE_MEDIUM,
                ))
            if len(rows) > count_before:
                packs_used += 1
    finally:
        client.close()

    database.labels_replace_source("graphsense_tagpack", rows)
    return {"packs": packs_used, "labels": len(rows),
            "skipped_packs": skipped}


def refresh_scamsniffer_labels() -> dict:
    """Download and import the ScamSniffer drainer/phishing blacklist
    (EVM addresses); replaces all 'scamsniffer' labels.

    Imported at MEDIUM confidence as category 'scam_report': shown on
    nodes and raised as a finding, but never treated as a stopping point
    and never presented as anything more than a third-party report list.

    Returns {"addresses": n}."""
    client = ScamSnifferClient()
    try:
        payload = json.loads(client.fetch(
            config.SCAMSNIFFER_ADDRESSES_URL, cacheable=False))
    finally:
        client.close()
    if not isinstance(payload, list):
        raise ValueError("ScamSniffer blacklist: unexpected format")
    rows = []
    for address in payload:
        address = str(address).strip()
        if not address:
            continue
        rows.append((
            normalise_address(address, config.CHAIN_ETHEREUM),
            config.CHAIN_ETHEREUM,
            "Reported drainer/phishing address (ScamSniffer public "
            "blacklist; open data, 7-day delayed)",
            config.LABEL_CATEGORY_SCAM_REPORT,
            config.CONFIDENCE_MEDIUM,
        ))
    database.labels_replace_source("scamsniffer", rows)
    return {"addresses": len(rows)}


def load_seed_labels() -> int:
    """Import the bundled community exchange list (idempotent).
    Returns the number of labels loaded."""
    with open(config.SEED_LABELS_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = [
        (normalise_address(item["address"], item["chain"]), item["chain"],
         item["entity"], item["category"], config.CONFIDENCE_MEDIUM)
        for item in payload["labels"]
    ]
    database.labels_replace_source("seed_community", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Exchange compliance designations (agency-editable, FATF-referenced)
# ---------------------------------------------------------------------------

_compliance_cache = None


def compliance_lookup(entity_name: str):
    """The agency's compliant/non-compliant designation for an exchange
    entity, or None. Matched case-insensitively by name so 'Binance 8'
    (a specific hot wallet) still matches the 'Binance' designation.

    Returns {"status": "compliant"|"non_compliant", "designated_entity",
    "note", "source_note"}. The source_note states plainly that this is
    the agency's own designation referenced to FATF guidance, not an
    official FATF publication - reports must carry that statement."""
    global _compliance_cache
    if _compliance_cache is None:
        try:
            with open(config.EXCHANGE_COMPLIANCE_PATH, "r",
                      encoding="utf-8") as handle:
                _compliance_cache = json.load(handle)
        except (OSError, json.JSONDecodeError):
            _compliance_cache = {"designations": [], "source_note": ""}
    name = (entity_name or "").lower()
    for item in _compliance_cache.get("designations", []):
        if item["entity"].lower() in name:
            return {
                "status": item["status"],
                "designated_entity": item["entity"],
                "note": item.get("note", ""),
                "source_note": _compliance_cache.get("source_note", ""),
            }
    return None


def lookup(address: str, chain: str) -> list:
    """All labels for an address, best confidence first.
    Each: {entity_name, category, source, confidence}.

    The agency's own wallet flags (wallet_flags table) are merged in as
    labels so every trace, finding and report sees them automatically. A
    flag label additionally carries the raw flag record under "flag"
    (reason, originating case, date)."""
    normalised = normalise_address(address, chain)
    results = database.labels_lookup(normalised, chain)
    flag = database.flag_lookup(normalised, chain)
    if flag:
        results.append({
            "entity_name": "AGENCY FLAG" + (f": {flag['reason']}"
                                            if flag.get("reason") else ""),
            "category": config.LABEL_CATEGORY_FLAGGED,
            "source": config.LABEL_SOURCE_AGENCY_FLAG,
            # The label asserts only "this agency flagged it", which is a
            # local fact; the reason text is the agency's own designation.
            "confidence": config.CONFIDENCE_HIGH,
            "flag": flag,
        })
    confidence_rank = {config.CONFIDENCE_HIGH: 0, config.CONFIDENCE_MEDIUM: 1,
                       config.CONFIDENCE_LOW: 2}
    return sorted(results, key=lambda r: confidence_rank.get(r["confidence"], 3))


# ---------------------------------------------------------------------------
# Custodian legal-process contact directory (agency-editable)
# ---------------------------------------------------------------------------

_custodian_cache = None


def _custodian_directory_load() -> dict:
    global _custodian_cache
    if _custodian_cache is None:
        try:
            with open(config.CUSTODIAN_CONTACTS_PATH, "r",
                      encoding="utf-8") as handle:
                _custodian_cache = json.load(handle)
        except (OSError, json.JSONDecodeError):
            _custodian_cache = {"custodians": [], "source_note": ""}
    return _custodian_cache


def custodian_directory() -> dict:
    """The full custodian directory (for in-app browsing/lookup)."""
    return _custodian_directory_load()


def custodian_lookup(entity_name: str):
    """Legal-process contact details for a custodian entity, or None.
    Matched case-insensitively by name substring so 'Binance 8' (a specific
    hot wallet label) still matches the 'Binance' directory entry.

    Returns the directory record plus the file-level source_note, which
    tells the investigator to verify contacts before service - portals and
    addresses change, and stale legal-process contact info is worse than
    none."""
    cache = _custodian_directory_load()
    name = (entity_name or "").lower()
    for item in cache.get("custodians", []):
        if item["entity"].lower() in name:
            record = dict(item)
            record["source_note"] = cache.get("source_note", "")
            return record
    return None
