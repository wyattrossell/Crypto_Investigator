"""
Bulk address triage.

Paste N addresses (a seized phone, an exchange production, a victim's
list) and get one table: chain, attribution, agency flag, partner flags,
scam-list hits, cached Chainabuse reports, watch status and - optionally -
live activity (transaction count and balance). Runs in a background
thread because live activity means one throttled API call per address.

Every run is stored (triage_runs) so the table can be exported to CSV
later and the run is part of the audit trail; the data pulls it makes are
custody-logged like every other pull (trace_id NULL).
"""

import csv
import io
import json
import threading

from app import config, database
from app.intel import chainabuse
from app.labels import store as label_store
from app.tracing import detect


def _normalise_inputs(raw_addresses: list, chain_hint: str) -> list:
    entries = []
    seen = set()
    for raw in raw_addresses:
        value = (raw or "").strip()
        if not value:
            continue
        detection = detect.detect_input(value)
        chain = detection.get("chain")
        if chain_hint and chain_hint != "auto":
            chain = chain_hint
        entry = {"input": value, "kind": detection["kind"], "chain": chain,
                 "traceable": detection["traceable"] and
                 chain in config.SUPPORTED_CHAINS,
                 "detect_message": detection["message"]}
        if detection["kind"] == detect.KIND_ADDRESS and chain:
            entry["address"] = label_store.normalise_address(value, chain)
        else:
            entry["address"] = value
        key = (entry["chain"], entry["address"])
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def start(case_id, raw_addresses: list, chain_hint: str,
          fetch_activity: bool) -> int:
    entries = _normalise_inputs(raw_addresses, chain_hint)
    if not entries:
        raise ValueError("No addresses were provided.")
    if len(entries) > config.TRIAGE_MAX_ADDRESSES:
        raise ValueError(
            f"Too many addresses ({len(entries)}); the limit is "
            f"{config.TRIAGE_MAX_ADDRESSES} per run.")
    params = {"chain_hint": chain_hint, "fetch_activity": bool(fetch_activity),
              "count": len(entries)}
    run_id = database.triage_create(case_id, params)
    threading.Thread(target=_run, args=(run_id, entries, bool(fetch_activity)),
                     name=f"triage-{run_id}", daemon=True).start()
    return run_id


def _row_for(entry: dict, fetch_activity: bool) -> dict:
    row = dict(entry)
    row.update({"labels": [], "attribution": "", "attribution_confidence": "",
                "flag": None, "shared_flags": [], "scam_lists": [],
                "chainabuse": None, "watched": False, "activity": None,
                "activity_error": None})
    chain, address = entry["chain"], entry["address"]
    if entry["kind"] != detect.KIND_ADDRESS or not chain:
        return row
    labels = label_store.lookup(address, chain)
    row["labels"] = [{"entity": l["entity_name"], "category": l["category"],
                      "source": l["source"], "confidence": l["confidence"]}
                     for l in labels]
    for label in labels:
        if label["category"] in ("exchange", "mixer", "sanctioned", "other") \
                and not row["attribution"]:
            row["attribution"] = label["entity_name"]
            row["attribution_confidence"] = label["confidence"]
            row["attribution_category"] = label["category"]
        elif label["category"] == config.LABEL_CATEGORY_SHARED_FLAG:
            row["shared_flags"].append(label["entity_name"])
        elif label["category"] == config.LABEL_CATEGORY_SCAM_REPORT and \
                label["source"] != config.LABEL_SOURCE_CHAINABUSE:
            row["scam_lists"].append(label["source"])
    flag = database.flag_lookup(address, chain)
    if flag:
        row["flag"] = {"reason": flag.get("reason"),
                       "case_name": flag.get("case_name"),
                       "created_utc": flag.get("created_utc")}
    row["watched"] = database.watch_lookup(address, chain) is not None
    reports = chainabuse.cached(address, chain)
    if reports is not None:
        row["chainabuse"] = {"report_count": reports.get("report_count", 0),
                             "fetched_utc": reports.get("fetched_utc")}
    if fetch_activity and chain in config.SUPPORTED_CHAINS:
        try:
            from app import scheduler
            snap = scheduler._snapshot(chain, address)
            per_unit = snap.get("per_unit") or 1
            row["activity"] = {
                "tx_count": snap.get("tx_count"),
                "balance": snap.get("balance_raw", 0) / per_unit,
                "unit": snap.get("unit", ""),
            }
        except Exception as exc:
            row["activity_error"] = str(exc)
    return row


def _run(run_id: int, entries: list, fetch_activity: bool) -> None:
    rows = []
    try:
        for index, entry in enumerate(entries, start=1):
            rows.append(_row_for(entry, fetch_activity))
            if index % 5 == 0 or index == len(entries):
                database.triage_update(
                    run_id, "running",
                    f"{index}/{len(entries)} addresses checked")
        summary = {
            "total": len(rows),
            "attributed": sum(1 for r in rows if r["attribution"]),
            "flagged": sum(1 for r in rows if r["flag"]),
            "shared_flagged": sum(1 for r in rows if r["shared_flags"]),
            "scam_listed": sum(1 for r in rows if r["scam_lists"]),
            "chainabuse_hits": sum(1 for r in rows if r["chainabuse"] and
                                   r["chainabuse"]["report_count"]),
            "unrecognised": sum(1 for r in rows
                                if r["kind"] != detect.KIND_ADDRESS),
        }
        database.triage_update(run_id, "finished", "Finished",
                               {"rows": rows, "summary": summary,
                                "generated_utc": database.utc_now_iso(),
                                "app_version": config.APP_VERSION})
    except Exception as exc:
        database.triage_update(run_id, "failed", error=str(exc))


def result_csv(result: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["input", "address", "chain", "attribution",
                     "attribution_confidence", "labels", "agency_flag",
                     "shared_flags", "scam_lists", "chainabuse_reports",
                     "watched", "tx_count", "balance", "unit", "note"])
    for row in result.get("rows", []):
        activity = row.get("activity") or {}
        writer.writerow([
            row.get("input"), row.get("address"), row.get("chain") or "",
            row.get("attribution") or "", row.get("attribution_confidence") or "",
            " | ".join(f"{l['entity']} [{l['source']}/{l['confidence']}]"
                       for l in row.get("labels", [])),
            (row.get("flag") or {}).get("reason") if row.get("flag") else "",
            " | ".join(row.get("shared_flags", [])),
            " | ".join(row.get("scam_lists", [])),
            (row.get("chainabuse") or {}).get("report_count", "")
            if row.get("chainabuse") else "",
            "yes" if row.get("watched") else "",
            activity.get("tx_count", ""), activity.get("balance", ""),
            activity.get("unit", ""),
            row.get("activity_error") or (
                "" if row.get("kind") == detect.KIND_ADDRESS
                else row.get("detect_message")),
        ])
    return out.getvalue()
