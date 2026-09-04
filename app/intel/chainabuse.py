"""
Chainabuse (TRM Labs) scam-report lookups.

API facts (verified against docs.chainabuse.com on 2026-09-04):
  GET https://api.chainabuse.com/v0/reports?address=<addr>&perPage=50
  Authorization: Basic base64("<api key>:")   (key as username)
  Free tier: 10 calls per month, up to 50 reports per call.
  Law-enforcement partner tier: up to 5,000 calls/hour, richer fields
  (accused-scammer contact info, IPs, evidence) when granted.

Design:
  * Lookups are ON DEMAND (a button on the address panel or a triage row),
    never automatic - the free budget is precious.
  * Every answer is cached locally (intel_lookups) and re-used for
    CHAINABUSE_CACHE_DAYS; a forced refresh spends a call.
  * The monthly call count is tracked (api_usage) and the free tier is
    refused once spent, with the remaining budget shown in the UI.
  * A positive result is stored as a 'chainabuse' label (category
    scam_report, MEDIUM confidence) so every later trace raises the
    existing "publicly reported scam address" finding automatically.
  * Reports are third-party, unverified claims: the UI and reports say
    so. Victim personal data is never present in the public API.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx

from app import config, database
from app.labels import store as label_store
from app.providers.base import ProviderClient, ProviderError


class BudgetError(Exception):
    """The free tier's monthly call budget is spent."""


class NotConfigured(Exception):
    """No API key stored."""


class ChainabuseClient(ProviderClient):
    provider_name = "chainabuse"

    def __init__(self, api_key: str):
        super().__init__(trace_id=None, memo=None)
        token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
        self._client.headers["Authorization"] = f"Basic {token}"
        self._client.headers["Accept"] = "application/json"


def _period_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def status() -> dict:
    """Key/tier/budget summary for Settings and the address panel."""
    key = database.get_setting("chainabuse_api_key")
    tier = database.get_setting("chainabuse_tier", "free")
    if tier not in config.CHAINABUSE_TIERS:
        tier = "free"
    used = database.api_usage_get(config.LABEL_SOURCE_CHAINABUSE,
                                  _period_now())
    budget = (config.CHAINABUSE_FREE_MONTHLY_CALLS if tier == "free"
              else None)
    return {
        "configured": bool(key),
        "tier": tier,
        "period": _period_now(),
        "calls_used": used,
        "monthly_budget": budget,
        "calls_remaining": (max(0, budget - used) if budget is not None
                            else None),
        "cached_addresses": database.intel_cache_count(
            config.LABEL_SOURCE_CHAINABUSE),
        "cache_days": config.CHAINABUSE_CACHE_DAYS,
        "partner_url": config.CHAINABUSE_PARTNER_URL,
    }


def _trim_report(report: dict) -> dict:
    """Keep the fields an investigator needs; drop bulk. Partner-only
    fields pass through when present (they are absent on the free tier)."""
    addresses = report.get("addresses") or []
    losses = report.get("losses") or []
    trimmed = {
        "id": report.get("id"),
        "category": report.get("scamCategory"),
        "created_at": report.get("createdAt"),
        "trusted": bool(report.get("trusted")),
        "checked": bool(report.get("checked")),
        "is_private": bool(report.get("isPrivate")),
        "description": (report.get("description") or "")[:1200],
        "address_count": len(addresses),
        "other_addresses": [
            {"address": a.get("address"), "chain": a.get("chain"),
             "domain": a.get("domain")}
            for a in addresses[:20]],
        "losses": [{"amount": l.get("amount"), "asset": l.get("asset")}
                   for l in losses[:10]],
    }
    for partner_field in ("accusedScammers", "ips", "evidences"):
        if report.get(partner_field):
            trimmed[partner_field] = report[partner_field]
    return trimmed


def cached(address: str, chain: str):
    """The stored lookup for an address (any age), or None."""
    normalised = label_store.normalise_address(address, chain)
    record = database.intel_cache_get(config.LABEL_SOURCE_CHAINABUSE,
                                      normalised, chain)
    if record is None:
        return None
    return _present(record["payload"], record["fetched_utc"], True)


def _present(payload: dict, fetched_utc: str, from_cache: bool) -> dict:
    result = dict(payload)
    result["fetched_utc"] = fetched_utc
    result["from_cache"] = from_cache
    result["budget"] = status()
    return result


def _is_fresh(fetched_utc: str) -> bool:
    try:
        fetched = datetime.fromisoformat(fetched_utc)
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - fetched < timedelta(
        days=config.CHAINABUSE_CACHE_DAYS)


def lookup(address: str, chain: str, force: bool = False) -> dict:
    """Return the reports for an address, from cache when fresh, otherwise
    spending one API call. Raises NotConfigured / BudgetError /
    ProviderError."""
    normalised = label_store.normalise_address(address, chain)
    record = database.intel_cache_get(config.LABEL_SOURCE_CHAINABUSE,
                                      normalised, chain)
    if record is not None and not force and _is_fresh(record["fetched_utc"]):
        return _present(record["payload"], record["fetched_utc"], True)

    key = database.get_setting("chainabuse_api_key")
    if not key:
        raise NotConfigured(
            "No Chainabuse API key is configured (Settings). Every "
            "Chainabuse account can generate a free key.")
    info = status()
    if info["monthly_budget"] is not None and \
            info["calls_used"] >= info["monthly_budget"]:
        raise BudgetError(
            f"The free Chainabuse tier's {info['monthly_budget']} calls for "
            f"{info['period']} are used up. Cached answers still work; new "
            f"lookups resume next month, or apply for the law-enforcement "
            f"partner tier ({config.CHAINABUSE_PARTNER_URL}).")

    url = (f"{config.CHAINABUSE_API_BASE}/reports?address={normalised}"
           f"&perPage={config.CHAINABUSE_PAGE_SIZE}")
    if info["tier"] == "partner":
        url += "&includePrivate=true"
    client = ChainabuseClient(key)
    try:
        body = client.fetch(url, cacheable=False)
    finally:
        client.close()
    # The call was made: count it whatever the parse outcome.
    database.api_usage_increment(config.LABEL_SOURCE_CHAINABUSE,
                                 _period_now())
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ProviderError(f"Chainabuse returned non-JSON data: {exc}")
    reports_raw = payload.get("reports") if isinstance(payload, dict) else None
    if reports_raw is None:
        raise ProviderError("Chainabuse response had no 'reports' list "
                            f"(keys: {list(payload)[:6] if isinstance(payload, dict) else type(payload).__name__})")
    reports = [_trim_report(r) for r in reports_raw]
    count = int(payload.get("count") or len(reports))
    categories = {}
    for report in reports:
        categories[report["category"] or "UNSPECIFIED"] = \
            categories.get(report["category"] or "UNSPECIFIED", 0) + 1
    result = {
        "address": normalised,
        "chain": chain,
        "report_count": count,
        "reports": reports,
        "categories": categories,
        "source_note": ("Chainabuse public scam reports (TRM Labs). "
                        "Reports are submitted by the public and are "
                        "unverified unless marked checked/trusted; they "
                        "corroborate, they do not prove."),
    }
    database.intel_cache_put(config.LABEL_SOURCE_CHAINABUSE, normalised,
                             chain, count, result)
    _sync_label(normalised, chain, count, categories)
    return _present(result, database.utc_now_iso(), False)


def _sync_label(address: str, chain: str, count: int,
                categories: dict) -> None:
    """Mirror a positive lookup into the label store so traces raise it."""
    if count <= 0:
        database.label_delete(address, chain, config.LABEL_SOURCE_CHAINABUSE)
        return
    cats = ", ".join(f"{k} x{v}" for k, v in sorted(
        categories.items(), key=lambda kv: -kv[1])[:4])
    database.label_upsert(
        address, chain,
        f"Chainabuse: {count} public scam report(s)"
        + (f" [{cats}]" if cats else "")
        + " (unverified third-party reports)",
        config.LABEL_CATEGORY_SCAM_REPORT, config.LABEL_SOURCE_CHAINABUSE,
        config.CONFIDENCE_MEDIUM)
