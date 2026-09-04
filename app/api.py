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
    "watch_interval_minutes", "labels_autorefresh",
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
    for field_name in ("etherscan_api_key", "coingecko_api_key",
                       "trongrid_api_key", "ai_api_key") \
            + PLAIN_SETTING_KEYS:
        value = getattr(body, field_name)
        if value is not None:
            database.set_setting(field_name, value.strip())
    return {"ok": True}


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
