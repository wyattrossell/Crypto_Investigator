"""
FastAPI application: JSON API + static UI, all bound to localhost.

Traces run in a background thread (block-explorer pulls are slow and rate
limited); the UI polls GET /api/traces/{id} for status and results.
"""

import csv
import io
import json
import logging
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import assistant, config, database, ic3, prices, scheduler
from app.intel import chainabuse, summary as wallet_summary, triage
from app.labels import packs as flag_packs
from app.labels import store as label_store
from app.report import casefiles, freeze_letter, ic3_worksheet, pdf_report
from app.tracing import detect
from app.tracing.engine import ForwardTrace

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """The browser must never run an old copy of the UI against a newer
    server (features silently 'disappear'). no-cache makes the browser
    revalidate static files on every load - trivial cost on localhost."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Set by the desktop launcher (app/launcher.py) so the UI can stop the
# program cleanly; stays None when the server is run some other way.
shutdown_hook = None

log = logging.getLogger("crypto_investigator.api")


@app.on_event("startup")
def startup() -> None:
    """Create folders + tables, load seed labels, tidy interrupted traces,
    start the watch scheduler."""
    config.bootstrap_data_dir()
    database.initialise_database()
    label_store.load_seed_labels()
    orphaned = database.fail_orphaned_traces()
    database.fail_orphaned_triage_runs()
    if orphaned:
        log.warning("%d trace(s) interrupted by a previous shutdown were "
                    "marked failed", orphaned)
    scheduler.start()
    log.info("%s v%s ready; data folder %s", config.APP_NAME,
             config.APP_VERSION, config.DATA_DIR)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    case_number: str = ""
    notes: str = ""


class TraceCreate(BaseModel):
    case_id: int
    victim_address: str = Field(min_length=8)   # the wallet the trace starts at
    focus_txid: str = ""            # optional: one payment sets the direction
    extended: bool = False          # follow until every branch resolves
    direction: str = config.DIRECTION_FORWARD   # forward | backward
    search_pattern: str = config.DEFAULT_SEARCH_PATTERN
    chain: str                      # user-confirmed chain
    max_depth: int = config.DEFAULT_MAX_DEPTH
    dust_btc: float = config.DEFAULT_DUST_THRESHOLD_BTC
    dust_eth: float = config.DEFAULT_DUST_THRESHOLD_ETH
    dust_token: float = config.DEFAULT_DUST_THRESHOLD_TOKEN_USD
    dust_trx: float = config.DEFAULT_DUST_THRESHOLD_TRX


class Ic3DraftSave(BaseModel):
    data: dict


class SettingsUpdate(BaseModel):
    etherscan_api_key: str | None = None
    coingecko_api_key: str | None = None
    trongrid_api_key: str | None = None
    chainabuse_api_key: str | None = None
    chainabuse_tier: str | None = None         # free | partner
    alchemy_api_key: str | None = None
    blockstream_client_id: str | None = None
    blockstream_client_secret: str | None = None
    bitcoin_api_mode: str | None = None        # pool | keyed | custom
    blockscout_api_base: str | None = None     # self-hosted Blockscout URL
    bitcoin_api_base: str | None = None
    etherscan_api_base: str | None = None
    ethereum_api_mode: str | None = None       # auto | etherscan | blockscout
    watch_interval_minutes: str | None = None
    labels_autorefresh: str | None = None      # "on" | "off"
    # Optional AI assistant (off until a provider is chosen).
    ai_provider: str | None = None             # "" | anthropic | openai | custom
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_base_url: str | None = None
    ai_workspace_id: str | None = None         # anthropic identity-linked keys
    # Agency letterhead for the freeze/preservation request generator.
    agency_name: str | None = None
    agency_unit: str | None = None
    agency_address: str | None = None
    agency_phone: str | None = None
    officer_name: str | None = None
    officer_title: str | None = None
    officer_badge: str | None = None
    officer_email: str | None = None


# Settings keys persisted verbatim (API keys are handled separately and
# encrypted at rest).
PLAIN_SETTING_KEYS = (
    "bitcoin_api_base", "etherscan_api_base", "ethereum_api_mode",
    "watch_interval_minutes", "labels_autorefresh", "chainabuse_tier",
    "bitcoin_api_mode", "blockstream_client_id", "blockscout_api_base",
    "ai_provider", "ai_model", "ai_base_url", "ai_workspace_id",
    "agency_name", "agency_unit", "agency_address", "agency_phone",
    "officer_name", "officer_title", "officer_badge", "officer_email",
)

AGENCY_SETTING_KEYS = (
    "agency_name", "agency_unit", "agency_address", "agency_phone",
    "officer_name", "officer_title", "officer_badge", "officer_email",
)


class FlagCreate(BaseModel):
    address: str = Field(min_length=8)
    chain: str
    reason: str = ""
    case_id: int | None = None


# ---------------------------------------------------------------------------
# Meta / settings / labels
# ---------------------------------------------------------------------------

@app.get("/api/meta")
def meta():
    """App identity + label inventory (drives the status bar in the UI)."""
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "labels_loaded": database.labels_count_by_source(),
        "labels_freshness": database.labels_freshness(),
        "flags_count": database.flags_count(),
        "watch_alerts": database.watch_alert_count(),
        "etherscan_key_configured":
            bool(database.get_setting("etherscan_api_key")),
        "data_dir": str(config.DATA_DIR),
        "installed_build": config.FROZEN,
        "can_shutdown": shutdown_hook is not None,
        "letterhead_configured": bool(database.get_setting("agency_name")),
        "ai_configured": bool(database.get_setting("ai_provider")),
    }


@app.post("/api/shutdown")
def shutdown():
    """Stop the program (only available under the desktop launcher, which
    registers `shutdown_hook`). Localhost-only like everything else."""
    if shutdown_hook is None:
        raise HTTPException(status_code=503,
                            detail="Not running under the desktop launcher; "
                                   "stop the server from where it was "
                                   "started.")
    threading.Timer(0.3, shutdown_hook).start()
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    """Current settings. The API key is masked - it never leaves the box."""
    key = database.get_setting("etherscan_api_key")
    def masked(value: str) -> str:
        return ((value[:4] + "..." + value[-4:]) if len(value) > 8
                else ("configured" if value else ""))

    settings = {
        "etherscan_api_key_masked": masked(key),
        "coingecko_api_key_masked":
            masked(database.get_setting("coingecko_api_key")),
        "trongrid_api_key_masked":
            masked(database.get_setting("trongrid_api_key")),
        "chainabuse_api_key_masked":
            masked(database.get_setting("chainabuse_api_key")),
        "chainabuse_tier": database.get_setting("chainabuse_tier", "free"),
        "chainabuse_status": chainabuse.status(),
        "alchemy_api_key_masked":
            masked(database.get_setting("alchemy_api_key")),
        "blockstream_client_id": database.get_setting("blockstream_client_id"),
        "blockstream_client_secret_masked":
            masked(database.get_setting("blockstream_client_secret")),
        "bitcoin_api_mode": database.get_setting("bitcoin_api_mode", "pool"),
        "blockscout_api_base": database.get_setting("blockscout_api_base", ""),
        "ai_api_key_masked":
            masked(database.get_setting("ai_api_key")),
        "ai_provider": database.get_setting("ai_provider", ""),
        "ai_model": database.get_setting("ai_model", ""),
        "ai_base_url": database.get_setting("ai_base_url", ""),
        "ai_workspace_id": database.get_setting("ai_workspace_id", ""),
        "bitcoin_api_base": database.get_setting(
            "bitcoin_api_base", config.DEFAULT_BITCOIN_API_BASE),
        "etherscan_api_base": database.get_setting(
            "etherscan_api_base", config.DEFAULT_ETHERSCAN_API_BASE),
        "ethereum_api_mode": database.get_setting(
            "ethereum_api_mode", config.ETHEREUM_API_MODE_AUTO),
        "watch_interval_minutes": database.get_setting(
            "watch_interval_minutes",
            str(config.WATCH_DEFAULT_INTERVAL_MINUTES)),
        "labels_autorefresh": database.get_setting(
            "labels_autorefresh", "on"),
    }
    for key_name in AGENCY_SETTING_KEYS:
        settings[key_name] = database.get_setting(key_name)
    return settings


@app.post("/api/settings")
def update_settings(body: SettingsUpdate):
    """Store any provided settings; blank string clears a value."""
    if body.ai_provider is not None and body.ai_provider.strip() and \
            body.ai_provider.strip() not in config.AI_PROVIDERS:
        raise HTTPException(status_code=400,
                            detail="Unknown AI provider - choose "
                                   "anthropic, openai, or custom.")
    if body.chainabuse_tier is not None and body.chainabuse_tier.strip() \
            and body.chainabuse_tier.strip() not in config.CHAINABUSE_TIERS:
        raise HTTPException(status_code=400,
                            detail="Chainabuse tier must be free or partner.")
    if body.bitcoin_api_mode is not None and body.bitcoin_api_mode.strip() \
            and body.bitcoin_api_mode.strip() not in config.BITCOIN_API_MODES:
        raise HTTPException(status_code=400,
                            detail="Bitcoin mode must be pool, keyed or custom.")
    if body.ethereum_api_mode is not None and body.ethereum_api_mode.strip() \
            and body.ethereum_api_mode.strip() not in config.ETHEREUM_API_MODES:
        raise HTTPException(status_code=400,
                            detail="Unknown Ethereum data-source mode.")
    for field_name in ("etherscan_api_key", "coingecko_api_key",
                       "trongrid_api_key", "ai_api_key",
                       "chainabuse_api_key", "alchemy_api_key",
                       "blockstream_client_secret") + PLAIN_SETTING_KEYS:
        value = getattr(body, field_name)
        if value is not None:
            database.set_setting(field_name, value.strip())
    return {"ok": True}


@app.get("/api/datasources")
def datasources():
    """The Data Sources panel: every provider with its tier, whether it is
    configured/active, and live counters for this run of the program."""
    from app.providers.base import ProviderStats
    stats = ProviderStats.snapshot()
    etherscan_key = bool(database.get_setting("etherscan_api_key"))
    alchemy_key = bool(database.get_setting("alchemy_api_key"))
    eth_mode = database.get_setting("ethereum_api_mode",
                                    config.ETHEREUM_API_MODE_AUTO)
    if eth_mode == config.ETHEREUM_API_MODE_AUTO:
        eth_active = ("alchemy" if alchemy_key else
                      "etherscan" if etherscan_key else "blockscout")
    else:
        eth_active = eth_mode
    if eth_active == "alchemy" and not alchemy_key:
        eth_active = "blockscout"
    if eth_active == "blockscout" and database.get_setting(
            "blockscout_api_base", "").strip():
        eth_active = "blockscout-custom"
    btc_mode = database.get_setting("bitcoin_api_mode", "pool")
    keyed_ok = bool(database.get_setting("blockstream_client_id")) and \
        bool(database.get_setting("blockstream_client_secret"))
    btc_active = ("blockstream-enterprise" if btc_mode == "keyed" and keyed_ok
                  else "esplora-custom" if btc_mode == "custom"
                  else "bitcoin-pool")
    configured = {
        "blockstream-enterprise": keyed_ok,
        "esplora-custom": btc_mode == "custom",
        "alchemy": alchemy_key,
        "etherscan": etherscan_key,
        "blockscout-custom": bool(database.get_setting("blockscout_api_base",
                                                       "").strip()),
        "trongrid": bool(database.get_setting("trongrid_api_key")),
        "prices": bool(database.get_setting("coingecko_api_key")),
        "chainabuse": bool(database.get_setting("chainabuse_api_key")),
    }
    active = {btc_active, eth_active, "trongrid", "prices", "labels",
              "chainabuse"}
    rows = []
    for entry in config.DATA_SOURCE_CATALOG:
        totals = {f: 0 for f in ProviderStats.FIELDS}
        last_error = None
        last_activity = None
        for provider in entry["providers"]:
            counters = stats.get(provider)
            if not counters:
                continue
            for field in ProviderStats.FIELDS:
                totals[field] += counters.get(field, 0)
            if counters.get("last_error"):
                last_error = counters["last_error"]
            last_activity = max(filter(None, [last_activity,
                                              counters.get("last_activity_utc")]),
                                default=None)
        totals["throttle_wait_s"] = round(totals["throttle_wait_s"], 1)
        row = dict(entry)
        row["configured"] = configured.get(entry["id"], True)
        row["active"] = entry["id"] in active
        row["stats"] = totals
        row["last_error"] = last_error
        row["last_activity_utc"] = last_activity
        rows.append(row)
    notes = []
    if btc_mode == "keyed" and not keyed_ok:
        notes.append("Bitcoin mode is 'keyed' but the Blockstream client "
                     "ID/secret are missing - the public pool is being used.")
    if eth_mode == "alchemy" and not alchemy_key:
        notes.append("Ethereum mode is 'alchemy' but no Alchemy key is set - "
                     "keyless Blockscout is being used.")
    return {"sources": rows, "bitcoin_mode": btc_mode,
            "ethereum_mode": eth_mode, "ethereum_active": eth_active,
            "bitcoin_active": btc_active, "notes": notes,
            "session_started_note": "Counters cover this run of the program "
                                    "only; the chain-of-custody log is the "
                                    "permanent record."}


@app.post("/api/labels/refresh-ofac")
def refresh_ofac():
    """Download + parse the official OFAC SDN list (takes ~a minute)."""
    try:
        summary = label_store.refresh_ofac_labels()
    except Exception as exc:  # surfaced verbatim in the UI
        raise HTTPException(status_code=502,
                            detail=f"OFAC refresh failed: {exc}")
    return summary


@app.post("/api/labels/refresh-tagpacks")
def refresh_tagpacks():
    """Download + import GraphSense TagPack exchange/mixer attribution."""
    try:
        summary = label_store.refresh_graphsense_labels()
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"TagPack refresh failed: {exc}")
    return summary


@app.post("/api/labels/refresh-scamsniffer")
def refresh_scamsniffer():
    """Download + import the ScamSniffer drainer/phishing blacklist."""
    try:
        summary = label_store.refresh_scamsniffer_labels()
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"ScamSniffer refresh failed: {exc}")
    return summary


@app.post("/api/labels/refresh-eth-labels")
def refresh_eth_labels():
    """Download + import eth-labels (Etherscan public name tags, MIT)."""
    try:
        summary = label_store.refresh_eth_labels()
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"eth-labels refresh failed: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Flag packs (sharing designations between agencies as files)
# ---------------------------------------------------------------------------

@app.get("/api/flags/pack.json")
def flags_pack_export():
    """This agency's flags as a shareable pack (no case names/numbers)."""
    agency = {k: database.get_setting(k) for k in AGENCY_SETTING_KEYS}
    pack = flag_packs.build_pack(agency)
    slug = "".join(ch if ch.isalnum() else "-"
                   for ch in (agency.get("agency_name") or "agency").lower())
    filename = f"flag-pack_{slug.strip('-') or 'agency'}_" \
               f"{pack['exported_utc'][:10]}.json"
    return Response(content=json.dumps(pack, indent=2, ensure_ascii=False),
                    media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})


@app.get("/api/flags/packs")
def flags_packs_list():
    return flag_packs.list_packs()


@app.post("/api/flags/packs/import")
def flags_pack_import(body: dict):
    """Verify + import a pack another agency exported. The body is the
    pack file's JSON."""
    try:
        return flag_packs.import_pack(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/flags/packs/{pack_id}")
def flags_pack_delete(pack_id: int):
    if not flag_packs.remove_pack(pack_id):
        raise HTTPException(status_code=404, detail="Pack not found.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Wallet intelligence: Chainabuse, summaries, bulk triage
# ---------------------------------------------------------------------------

class IntelLookup(BaseModel):
    address: str = Field(min_length=8)
    chain: str
    force: bool = False


@app.get("/api/intel/chainabuse/status")
def chainabuse_status():
    return chainabuse.status()


@app.post("/api/intel/chainabuse/lookup")
def chainabuse_lookup(body: IntelLookup):
    """On-demand Chainabuse report lookup (cached; budgeted)."""
    chain = body.chain.strip().lower()
    try:
        return chainabuse.lookup(body.address.strip(), chain, body.force)
    except chainabuse.NotConfigured as exc:
        raise HTTPException(status_code=428, detail=str(exc))
    except chainabuse.BudgetError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Chainabuse lookup failed: {exc}")


@app.get("/api/intel/chainabuse/cached")
def chainabuse_cached(address: str, chain: str):
    cached = chainabuse.cached(address.strip(), chain.strip().lower())
    if cached is None:
        raise HTTPException(status_code=404, detail="No cached lookup.")
    return cached


@app.get("/api/intel/summary")
def intel_summary(address: str, chain: str, trace_id: int | None = None,
                  activity: bool = True):
    """Plain-language wallet summary (no AI; facts included)."""
    chain = chain.strip().lower()
    if chain not in config.SUPPORTED_CHAINS:
        raise HTTPException(status_code=400, detail="Unsupported chain.")
    result = None
    if trace_id is not None:
        trace = database.get_trace(trace_id)
        if trace and trace.get("result_json"):
            result = json.loads(trace["result_json"])
    try:
        return wallet_summary.wallet_summary(chain, address.strip(), result,
                                             fetch_activity=activity)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Summary failed: {exc}")


class TriageCreate(BaseModel):
    addresses: list[str]
    chain: str = "auto"          # auto | bitcoin | ethereum | tron | litecoin
    case_id: int | None = None
    fetch_activity: bool = False


@app.post("/api/intel/triage")
def triage_create(body: TriageCreate):
    if body.case_id is not None and database.get_case(body.case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    chain = (body.chain or "auto").strip().lower()
    if chain != "auto" and chain not in config.SUPPORTED_CHAINS + \
            (config.CHAIN_LITECOIN,):
        raise HTTPException(status_code=400, detail="Unknown chain.")
    try:
        run_id = triage.start(body.case_id, body.addresses, chain,
                              body.fetch_activity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"run_id": run_id}


@app.get("/api/intel/triage")
def triage_list():
    rows = database.triage_list()
    for row in rows:
        row["params"] = json.loads(row.pop("params_json") or "{}")
    return rows


@app.get("/api/intel/triage/{run_id}")
def triage_get(run_id: int):
    row = database.triage_get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Triage run not found.")
    row["params"] = json.loads(row.pop("params_json") or "{}")
    row["result"] = json.loads(row.pop("result_json") or "null")
    return row


@app.get("/api/intel/triage/{run_id}/export.csv")
def triage_csv(run_id: int):
    row = database.triage_get(run_id)
    if row is None or not row.get("result_json"):
        raise HTTPException(status_code=404, detail="No finished triage run.")
    text = triage.result_csv(json.loads(row["result_json"]))
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="triage_{run_id}.csv"'})


# ---------------------------------------------------------------------------
# Wallet flags (the agency's own fraud designations)
# ---------------------------------------------------------------------------

@app.get("/api/flags")
def flags_list():
    """Every flagged wallet, newest first, with the originating case."""
    return database.flags_list()


@app.post("/api/flags")
def flags_add(body: FlagCreate):
    """Flag (or re-flag with a new reason) one wallet."""
    chain = body.chain.strip().lower()
    address = label_store.normalise_address(body.address.strip(), chain)
    if not chain:
        raise HTTPException(status_code=400, detail="Chain is required.")
    if body.case_id is not None and database.get_case(body.case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    database.flag_add(address, chain, body.reason.strip(), body.case_id)
    return {"ok": True, "address": address, "chain": chain}


@app.delete("/api/flags")
def flags_delete(address: str, chain: str):
    """Remove one wallet flag."""
    chain = chain.strip().lower()
    address = label_store.normalise_address(address.strip(), chain)
    if not database.flag_remove(address, chain):
        raise HTTPException(status_code=404, detail="No flag on this wallet.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Optional AI assistant (glass-box: fully logged, never evidence)
# ---------------------------------------------------------------------------

class AiQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list = []      # prior [{role, content}] turns of this Q&A


def _finished_trace_result(trace_id: int) -> tuple:
    trace = database.get_trace(trace_id)
    if trace is None or not trace.get("result_json"):
        raise HTTPException(status_code=404,
                            detail="Trace not found or not finished.")
    return trace, json.loads(trace["result_json"])


@app.get("/api/ai/status")
def ai_status():
    """Whether/how the assistant is configured (never returns the key)."""
    return assistant.get_config()


@app.post("/api/traces/{trace_id}/ai/summary")
def ai_trace_summary(trace_id: int):
    """Plain-language AI summary of a finished trace (logged in full)."""
    trace, result = _finished_trace_result(trace_id)
    try:
        return assistant.summarize_trace(result, trace_id,
                                         trace["case_id"])
    except assistant.AssistantError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/traces/{trace_id}/ai/ask")
def ai_trace_ask(trace_id: int, body: AiQuestion):
    """Answer one question about a finished trace (logged in full)."""
    trace, result = _finished_trace_result(trace_id)
    try:
        return assistant.answer_question(result, trace_id,
                                         trace["case_id"],
                                         body.question.strip(),
                                         body.history)
    except assistant.AssistantError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/cases/{case_id}/ai/ic3-narrative")
def ai_ic3_narrative(case_id: int):
    """Suggest an IC3 incident-description draft from the case's latest
    finished trace. Returned as a suggestion only - never auto-saved."""
    trace = database.latest_finished_trace_for_case(case_id)
    if trace is None:
        raise HTTPException(status_code=404,
                            detail="No finished trace in this case yet - "
                                   "run a trace first.")
    result = json.loads(trace["result_json"])
    saved = database.ic3_draft_get(case_id)
    existing = ""
    if saved:
        existing = str(saved["data"].get("description", ""))
    try:
        return assistant.draft_ic3_narrative(result, existing, case_id)
    except assistant.AssistantError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/ai/log")
def ai_log(limit: int = 50):
    """The AI audit log (metadata; full records via /api/ai/log/{id})."""
    return database.ai_log_list(max(1, min(limit, 500)))


@app.get("/api/ai/log/{log_id}")
def ai_log_entry(log_id: int):
    """One full AI interaction record (exact prompt + response)."""
    entry = database.ai_log_get(log_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found.")
    return entry


# ---------------------------------------------------------------------------
# Wallet watches (movement alerts)
# ---------------------------------------------------------------------------

class WatchCreate(BaseModel):
    address: str = Field(min_length=8)
    chain: str
    note: str = ""
    case_id: int | None = None


@app.get("/api/watches")
def watches_get():
    """Every watch (alerts first) + the unacknowledged alert count."""
    watches = database.watches_list()
    for watch in watches:
        watch["baseline"] = (json.loads(watch.pop("baseline_json"))
                             if watch.get("baseline_json") else None)
        watch["alert"] = (json.loads(watch.pop("alert_json"))
                          if watch.get("alert_json") else None)
    return {"watches": watches,
            "alert_count": database.watch_alert_count()}


@app.post("/api/watches")
def watches_add(body: WatchCreate):
    """Watch one wallet for movement."""
    chain = body.chain.strip().lower()
    if chain not in config.SUPPORTED_CHAINS:
        raise HTTPException(status_code=400,
                            detail=f"Watches are not supported on "
                                   f"'{chain}'.")
    address = label_store.normalise_address(body.address.strip(), chain)
    if body.case_id is not None and database.get_case(body.case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    database.watch_add(address, chain, body.note.strip(), body.case_id)
    return {"ok": True, "address": address, "chain": chain}


@app.delete("/api/watches")
def watches_delete(address: str, chain: str):
    chain = chain.strip().lower()
    address = label_store.normalise_address(address.strip(), chain)
    if not database.watch_remove(address, chain):
        raise HTTPException(status_code=404,
                            detail="This wallet is not being watched.")
    return {"ok": True}


@app.post("/api/watches/check-now")
def watches_check_now():
    """Check every watch immediately (also what the scheduler does on its
    interval). Rate-limited by the shared provider throttles."""
    try:
        return scheduler.check_due_watches(force=True)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Watch check failed: {exc}")


@app.post("/api/watches/{watch_id}/acknowledge")
def watches_acknowledge(watch_id: int):
    database.watch_acknowledge(watch_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Custodian legal-process directory (browse/lookup)
# ---------------------------------------------------------------------------

@app.get("/api/custodians")
def custodians_get():
    """The agency-editable custodian directory, for in-app lookup."""
    directory = label_store.custodian_directory()
    return directory


@app.post("/api/detect")
def detect_endpoint(body: dict):
    """Classify pasted input so the UI can guide the user."""
    return detect.detect_input(str(body.get("value", "")))


@app.get("/api/prices/spot")
def prices_spot():
    """Live BTC/ETH USD prices for the ticker and dust-threshold hints.
    Display-only context - not evidence, not stored with any trace."""
    try:
        return prices.get_spot()
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Price source unavailable: {exc}")


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@app.get("/api/cases")
def cases_list():
    cases = database.list_cases()
    for case in cases:
        case["traces"] = database.list_traces_for_case(case["id"])
    return cases


@app.post("/api/cases")
def cases_create(body: CaseCreate):
    case_id = database.create_case(body.name, body.case_number, body.notes)
    return {"id": case_id}


class AnnotationSave(BaseModel):
    address: str = Field(min_length=8)
    chain: str
    note: str = ""      # empty note deletes the annotation


@app.get("/api/cases/{case_id}/traces")
def case_traces(case_id: int):
    """Trace history for a case (newest first) so the UI can reopen an
    earlier trace instead of only showing the one just run."""
    if database.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    rows = database.list_traces_for_case(case_id)
    for row in rows:
        trace = database.get_trace(row["id"])
        params = json.loads(trace.get("params_json") or "{}")
        row["direction"] = params.get("direction", "forward")
        row["extended"] = bool(params.get("extended"))
        row["search_pattern"] = params.get("search_pattern", "")
        row["focus_txid"] = params.get("focus_txid", "")
        result = trace.get("result_json")
        summary = {}
        if result:
            try:
                parsed = json.loads(result)
                summary = {
                    "exits": len(parsed.get("exits", [])),
                    "findings": len(parsed.get("findings", [])),
                    "addresses": len(parsed.get("nodes", [])),
                }
            except (ValueError, TypeError):
                summary = {}
        row["summary"] = summary
    return rows


@app.get("/api/cases/{case_id}/annotations")
def annotations_get(case_id: int):
    if database.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    return database.annotations_for_case(case_id)


@app.post("/api/cases/{case_id}/annotations")
def annotations_save(case_id: int, body: AnnotationSave):
    """Save (or clear, with an empty note) one investigator note."""
    if database.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    chain = body.chain.strip().lower()
    address = label_store.normalise_address(body.address.strip(), chain)
    database.annotation_set(case_id, address, chain, body.note)
    return {"ok": True}


@app.get("/api/cases/{case_id}/export.json")
def case_export(case_id: int):
    """The whole case (traces, custody log, IC3 draft, annotations,
    linked flags/watches) as one portable JSON document."""
    if database.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    payload = database.export_case(case_id)
    return Response(
        content=json.dumps(payload, indent=1),
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="case_{case_id}_export.json"'})


@app.post("/api/cases/import")
def case_import(body: dict):
    """Recreate an exported case (new ids). Flags and watches are
    upserted agency-wide and linked to the imported case."""
    payload = body.get("data") if isinstance(body.get("data"), dict) \
        else body
    if not isinstance(payload, dict) or \
            payload.get("format") != "crypto-investigator-case":
        raise HTTPException(
            status_code=400,
            detail="This is not a Crypto Investigator case export - "
                   "choose a case_*_export.json file created by the "
                   "Export case button.")
    try:
        new_case_id = database.import_case(payload)
    except Exception as exc:
        raise HTTPException(status_code=400,
                            detail=f"Import failed: {exc}")
    return {"ok": True, "case_id": new_case_id}


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------

def _run_trace_in_background(trace_id: int, chain: str, start_input: str,
                             params: dict) -> None:
    """Thread target: run the trace, storing progress + result/failure."""
    def on_progress(note: str) -> None:
        database.update_trace_status(trace_id, "running", progress_note=note)

    try:
        database.update_trace_status(trace_id, "running", "Starting trace...")
        tracer = ForwardTrace(trace_id, chain, start_input, params,
                              progress_callback=on_progress)
        result = tracer.run()
        database.update_trace_status(trace_id, "finished", "Done",
                                     result=result)
    except Exception as exc:
        database.update_trace_status(trace_id, "failed", error=str(exc))


@app.post("/api/traces")
def traces_create(body: TraceCreate):
    """Validate, persist and launch a trace in a background thread."""
    if body.chain not in config.SUPPORTED_CHAINS:
        raise HTTPException(status_code=400,
                            detail=f"Chain '{body.chain}' cannot be traced "
                                   f"in this version.")
    if database.get_case(body.case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    # No key needed for any chain: Ethereum falls back to the keyless
    # Blockscout API when no Etherscan key is configured.

    victim = body.victim_address.strip()
    detected = detect.detect_input(victim)
    if detected["kind"] == detect.KIND_TXID:
        raise HTTPException(
            status_code=400,
            detail="That looks like a transaction ID, not a wallet address. "
                   "Enter the victim's WALLET ADDRESS here; a transaction "
                   "ID goes in the optional 'focus transaction' field.")
    if body.direction not in (config.DIRECTION_FORWARD,
                              config.DIRECTION_BACKWARD):
        raise HTTPException(status_code=400,
                            detail="Trace direction must be 'forward' or "
                                   "'backward'.")

    focus_txid = body.focus_txid.strip()
    if focus_txid and body.direction == config.DIRECTION_BACKWARD:
        raise HTTPException(
            status_code=400,
            detail="A backward (source-of-funds) trace examines every "
                   "incoming payment; the focus-transaction field does "
                   "not apply. Leave it blank for backward traces.")
    if focus_txid and body.chain == config.CHAIN_TRON:
        raise HTTPException(
            status_code=400,
            detail="Focus transactions are not yet supported on Tron - "
                   "leave the field blank to trace the wallet's outgoing "
                   "payments.")
    if focus_txid:
        focus_detected = detect.detect_input(focus_txid)
        if focus_detected["kind"] != detect.KIND_TXID:
            raise HTTPException(
                status_code=400,
                detail="The focus transaction must be a transaction ID/hash "
                       "(64 hex characters, with 0x in front on Ethereum) - "
                       "what was entered looks like something else.")
    if body.search_pattern not in config.SEARCH_PATTERNS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown search pattern '{body.search_pattern}' - "
                   f"choose one of: {', '.join(config.SEARCH_PATTERNS)}.")

    params = {
        "max_depth": max(1, min(body.max_depth, config.MAX_DEPTH_LIMIT)),
        "dust_btc": body.dust_btc,
        "dust_eth": body.dust_eth,
        "dust_token": body.dust_token,
        "dust_trx": body.dust_trx,
        "focus_txid": focus_txid,
        "extended": bool(body.extended),
        "direction": body.direction,
        "search_pattern": body.search_pattern,
    }
    trace_id = database.create_trace(body.case_id, body.chain, victim, params)
    thread = threading.Thread(
        target=_run_trace_in_background,
        args=(trace_id, body.chain, victim, params),
        daemon=True)
    thread.start()
    return {"trace_id": trace_id}


@app.get("/api/traces/{trace_id}")
def traces_get(trace_id: int):
    """Status + (when finished) full result for one trace."""
    trace = database.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    trace["params"] = json.loads(trace.pop("params_json"))
    result_json = trace.pop("result_json")
    trace["result"] = json.loads(result_json) if result_json else None
    return trace


# ---------------------------------------------------------------------------
# Report + custody exports
# ---------------------------------------------------------------------------

@app.get("/api/traces/{trace_id}/report.pdf")
def trace_report(trace_id: int):
    """Generate (or regenerate) and download the court-ready PDF."""
    trace = database.get_trace(trace_id)
    if trace is None or not trace.get("result_json"):
        raise HTTPException(status_code=404,
                            detail="Trace not found or not finished.")
    case = database.get_case(trace["case_id"])
    result = json.loads(trace["result_json"])
    custody = database.custody_entries_for_trace(trace_id)
    annotations = {a["address"]: a["note"]
                   for a in database.annotations_for_case(trace["case_id"])
                   if a["chain"] == result["chain"]}

    pdf_path = config.REPORTS_DIR / f"trace_{trace_id}_report.pdf"
    pdf_report.build_report(str(pdf_path), case, trace, result, custody,
                            annotations=annotations)
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        filename=f"fund_tracing_report_trace{trace_id}.pdf")


@app.get("/api/traces/{trace_id}/custody.csv")
def trace_custody_csv(trace_id: int):
    """Full chain-of-custody log as CSV (for disclosure packages)."""
    entries = database.custody_entries_for_trace(trace_id)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[
        "id", "trace_id", "source", "url", "http_status", "sha256",
        "from_cache", "fetched_utc"])
    writer.writeheader()
    writer.writerows(entries)
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv")


@app.get("/api/traces/{trace_id}/freeze-request.pdf")
def trace_freeze_request(trace_id: int, address: str, mode: str = "auto"):
    """DRAFT preservation/asset-freeze request letter for one wallet the
    trace touched, with the transaction schedule as Attachment A.

    Route selection (`mode=auto`): a wallet attributed to an exchange gets
    a CUSTODIAN letter; an unattributed wallet holding traced stablecoins
    gets a TOKEN-ISSUER letter (Tether/Circle can freeze USDT/USDC at any
    address, including self-custodied ones); otherwise a custodian letter
    with an honest do-not-serve warning. `mode=issuer` forces the issuer
    route (e.g. to freeze stablecoins already sitting at an exchange)."""
    trace = database.get_trace(trace_id)
    if trace is None or not trace.get("result_json"):
        raise HTTPException(status_code=404,
                            detail="Trace not found or not finished.")
    case = database.get_case(trace["case_id"])
    result = json.loads(trace["result_json"])
    target = label_store.normalise_address(address.strip(), result["chain"])

    node = next((n for n in result["nodes"] if n["address"] == target), None)
    if node is None:
        raise HTTPException(status_code=404,
                            detail="That address is not part of this trace.")
    funding_edges = [e for e in result["edges"] if e["to_address"] == target]
    if not funding_edges and result.get("direction") == \
            config.DIRECTION_BACKWARD:
        # Backward traces: the interesting edges LEAVE the source address.
        funding_edges = [e for e in result["edges"]
                         if e["from_address"] == target]
    if not funding_edges:
        raise HTTPException(
            status_code=400,
            detail="No traced funds touched this address in this trace - "
                   "there is nothing to freeze or preserve there. Pick an "
                   "address that received the traced funds (an exit point "
                   "or a funds-at-rest address).")

    exchange_label = next((l for l in node.get("labels", [])
                           if l["category"] == "exchange"), None)
    entity = exchange_label["entity_name"] if exchange_label else ""

    # Token-issuer route: dominant freezable stablecoin in the edges.
    stable_totals = {}
    for edge in funding_edges:
        if edge["asset"] in config.STABLECOIN_ISSUERS:
            stable_totals[edge["asset"]] = (
                stable_totals.get(edge["asset"], 0.0) + edge["value"])
    issuer_asset = ""
    if stable_totals and (mode == "issuer" or (mode == "auto" and
                                               not entity)):
        issuer_asset = max(stable_totals, key=stable_totals.get)
        entity = config.STABLECOIN_ISSUERS[issuer_asset]
        funding_edges = [e for e in funding_edges
                         if e["asset"] == issuer_asset]
    elif mode == "issuer":
        raise HTTPException(
            status_code=400,
            detail="No freezable stablecoin (USDT/USDC) among the traced "
                   "funds at this address - the token-issuer route does "
                   "not apply. Use the custodian letter instead.")

    custodian = label_store.custodian_lookup(entity) if entity else None
    compliance = (label_store.compliance_lookup(entity)
                  if entity and not issuer_asset else None)
    agency = {key: database.get_setting(key) for key in AGENCY_SETTING_KEYS}

    pdf_path = (config.REPORTS_DIR /
                f"trace_{trace_id}_freeze_{target[:12]}.pdf")
    freeze_letter.build_freeze_request(
        str(pdf_path), case, result, target, node, funding_edges,
        entity, custodian, compliance, agency, issuer_asset=issuer_asset)
    return FileResponse(
        str(pdf_path), media_type="application/pdf",
        filename=f"DRAFT_freeze_request_trace{trace_id}_{target[:12]}.pdf")


@app.get("/api/traces/{trace_id}/affidavit.txt")
def trace_affidavit(trace_id: int):
    """DRAFT affidavit methodology paragraphs (counsel to adapt)."""
    trace = database.get_trace(trace_id)
    if trace is None or not trace.get("result_json"):
        raise HTTPException(status_code=404,
                            detail="Trace not found or not finished.")
    case = database.get_case(trace["case_id"])
    result = json.loads(trace["result_json"])
    custody = database.custody_entries_for_trace(trace_id)
    agency = {key: database.get_setting(key) for key in AGENCY_SETTING_KEYS}
    text = casefiles.affidavit_text(case, trace, result, len(custody),
                                    agency)
    return PlainTextResponse(text, media_type="text/plain")


@app.get("/api/traces/{trace_id}/evidence-package.zip")
def trace_evidence_package(trace_id: int):
    """One ZIP with every artifact of the trace (report, custody log,
    raw data, affidavit draft, traceroutes, freeze letters) and a
    MANIFEST listing the SHA-256 of each file - ready for disclosure."""
    trace = database.get_trace(trace_id)
    if trace is None or not trace.get("result_json"):
        raise HTTPException(status_code=404,
                            detail="Trace not found or not finished.")
    case = database.get_case(trace["case_id"])
    result = json.loads(trace["result_json"])
    custody = database.custody_entries_for_trace(trace_id)
    agency = {key: database.get_setting(key) for key in AGENCY_SETTING_KEYS}
    annotations = {a["address"]: a["note"]
                   for a in database.annotations_for_case(trace["case_id"])
                   if a["chain"] == result["chain"]}
    zip_path = config.REPORTS_DIR / f"trace_{trace_id}_evidence.zip"
    casefiles.build_evidence_package(
        str(zip_path), case, trace, result, custody, agency, annotations,
        config.REPORTS_DIR)
    return FileResponse(
        str(zip_path), media_type="application/zip",
        filename=f"evidence_package_trace{trace_id}.zip")


@app.get("/api/traces/{trace_id}/export.json")
def trace_export_json(trace_id: int):
    """Full trace result as JSON (interop / archival)."""
    trace = database.get_trace(trace_id)
    if trace is None or not trace.get("result_json"):
        raise HTTPException(status_code=404,
                            detail="Trace not found or not finished.")
    return Response(content=trace["result_json"],
                    media_type="application/json")


# ---------------------------------------------------------------------------
# IC3 complaint helper (worksheet per case; filing itself is manual at
# https://complaint.ic3.gov - IC3 has no submission API)
# ---------------------------------------------------------------------------

@app.get("/api/cases/{case_id}/ic3")
def ic3_get(case_id: int):
    """Saved worksheet (or a blank one) + whether trace prefill is possible."""
    if database.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    saved = database.ic3_draft_get(case_id)
    return {
        "draft": saved["data"] if saved else ic3.empty_draft(),
        "updated_utc": saved["updated_utc"] if saved else None,
        "has_saved": saved is not None,
        "prefill_available":
            database.latest_finished_trace_for_case(case_id) is not None,
        "limits": {
            "description": ic3.LIMIT_DESCRIPTION,
            "technical_details": ic3.LIMIT_TECHNICAL,
            "witnesses": ic3.LIMIT_WITNESSES,
            "other_agencies": ic3.LIMIT_OTHER_AGENCIES,
        },
        "transaction_types": ic3.TRANSACTION_TYPES,
    }


@app.post("/api/cases/{case_id}/ic3")
def ic3_save(case_id: int, body: Ic3DraftSave):
    """Persist the worksheet (whole document each time; local single user)."""
    if database.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    database.ic3_draft_save(case_id, body.data)
    return {"ok": True}


@app.get("/api/cases/{case_id}/ic3/prefill")
def ic3_prefill(case_id: int):
    """Worksheet suggestions built from the case's latest finished trace."""
    trace = database.latest_finished_trace_for_case(case_id)
    if trace is None:
        raise HTTPException(
            status_code=404,
            detail="No finished trace in this case yet - run a trace first, "
                   "then prefill the transactions from it.")
    result = json.loads(trace["result_json"])
    return ic3.prefill_from_trace(result)


@app.get("/api/cases/{case_id}/ic3/worksheet.pdf")
def ic3_worksheet_pdf(case_id: int):
    """Printable worksheet in the exact order of the real IC3 form."""
    case = database.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    saved = database.ic3_draft_get(case_id)
    draft = saved["data"] if saved else ic3.empty_draft()
    pdf_path = config.REPORTS_DIR / f"case_{case_id}_ic3_worksheet.pdf"
    ic3_worksheet.build_worksheet(str(pdf_path), case, draft)
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        filename=f"ic3_worksheet_case{case_id}.pdf")


# Static UI last, so /api/* wins the routing race.
app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True),
          name="static")
