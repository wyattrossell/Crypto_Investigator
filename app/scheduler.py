"""
Background scheduler: wallet-watch checks and label auto-refresh.

One daemon thread ticks every SCHEDULER_TICK_SECONDS and

1. re-checks watched wallets whose interval has elapsed, comparing a small
   activity snapshot (transaction count + balance) against the stored
   baseline and raising an alert when anything changed, and
2. refreshes label sources (OFAC / TagPacks / ScamSniffer) once they are
   older than their cadence - but ONLY sources the user has already
   downloaded at least once (the tool never starts a bulk download the
   user did not ask for), and at most one source per tick.

Every network pull goes through the standard provider layer (throttled +
custody-logged with trace_id NULL, i.e. "pulls outside a trace").

Honest limitation, stated in the UI: an Ethereum watch tracks the account
nonce and ETH balance - an INCOMING token-only transfer changes neither
and will not trigger an alert until something else moves.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from app import config, database
from app.labels import store as label_store
from app.providers.bitcoin import BitcoinProvider
from app.providers.ethereum import EthereumProvider
from app.providers.tron import TronProvider

_started = False
_start_lock = threading.Lock()


def start() -> None:
    """Start the scheduler thread (idempotent; called at app startup)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True,
                     name="watch-scheduler").start()


def _loop() -> None:
    while True:
        try:
            check_due_watches()
        except Exception:
            pass    # a failed sweep must never kill the scheduler
        try:
            _autorefresh_labels()
        except Exception:
            pass
        time.sleep(config.SCHEDULER_TICK_SECONDS)


# ---------------------------------------------------------------------------
# Watch checks
# ---------------------------------------------------------------------------

def _snapshot(chain: str, address: str) -> dict:
    """Small activity snapshot used for movement detection."""
    if chain == config.CHAIN_BITCOIN:
        provider = BitcoinProvider()
        try:
            summary = provider.address_summary(address)
        finally:
            provider.close()
        return {"tx_count": summary["tx_count"],
                "balance_raw": summary["balance_raw"], "unit": "BTC",
                "per_unit": config.SATOSHIS_PER_BTC}
    if chain == config.CHAIN_ETHEREUM:
        provider = EthereumProvider()
        try:
            nonce = provider.outgoing_count(address)
            balance = provider.balance_raw(address)
        finally:
            provider.close()
        return {"tx_count": nonce, "balance_raw": balance, "unit": "ETH",
                "per_unit": config.WEI_PER_ETH}
    if chain == config.CHAIN_TRON:
        provider = TronProvider()
        try:
            count = provider.outgoing_count(address)
            balance = provider.account_summary(address)["balance_raw"]
        finally:
            provider.close()
        return {"tx_count": count, "balance_raw": balance, "unit": "TRX",
                "per_unit": config.SUN_PER_TRX}
    raise ValueError(f"watches not supported on chain: {chain}")


def _check_watch(watch: dict) -> bool:
    """Check one watch; returns True when a NEW alert was raised."""
    old = json.loads(watch["baseline_json"]) \
        if watch.get("baseline_json") else None
    try:
        snap = _snapshot(watch["chain"], watch["address"])
    except Exception as exc:
        # Record the attempt (so the interval applies) but keep the old
        # baseline - a fetch failure is not a movement.
        database.watch_update_check(
            watch["id"], old if old else {"error": str(exc)})
        return False

    alert = None
    if old and "error" not in old:
        changes = []
        if snap["tx_count"] != old.get("tx_count"):
            changes.append(f"transaction count {old.get('tx_count')} "
                           f"→ {snap['tx_count']}")
        if snap["balance_raw"] != old.get("balance_raw"):
            per_unit = snap.get("per_unit") or 1
            changes.append(
                f"balance {old.get('balance_raw', 0) / per_unit:,.6f} "
                f"→ {snap['balance_raw'] / per_unit:,.6f} "
                f"{snap.get('unit', '')}")
        if changes:
            alert = {
                "detected_utc": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "summary": "Activity detected: " + "; ".join(changes)
                           + ". Re-trace from this address now.",
                "previous": old,
                "current": snap,
            }
    database.watch_update_check(watch["id"], snap, alert)
    return alert is not None


def check_due_watches(force: bool = False) -> dict:
    """Check watches whose interval elapsed (or all, when forced)."""
    interval = config.WATCH_DEFAULT_INTERVAL_MINUTES
    try:
        interval = int(database.get_setting("watch_interval_minutes",
                                            str(interval)) or interval)
    except ValueError:
        pass
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=interval)).isoformat(timespec="seconds")
    due = database.watches_list() if force else database.watches_due(cutoff)
    checked, new_alerts = 0, 0
    for watch in due:
        checked += 1
        if _check_watch(watch):
            new_alerts += 1
    return {"checked": checked, "new_alerts": new_alerts}


# ---------------------------------------------------------------------------
# Label auto-refresh
# ---------------------------------------------------------------------------

_REFRESHERS = {
    "ofac_sdn": label_store.refresh_ofac_labels,
    "scamsniffer": label_store.refresh_scamsniffer_labels,
    "graphsense_tagpack": label_store.refresh_graphsense_labels,
    "eth_labels": label_store.refresh_eth_labels,
}


def _autorefresh_labels() -> None:
    if database.get_setting("labels_autorefresh", "on") != "on":
        return
    freshness = database.labels_freshness()
    now = datetime.now(timezone.utc)
    for source, cadence_days in config.LABEL_AUTOREFRESH_DAYS.items():
        last = freshness.get(source)
        if not last:
            continue    # never downloaded by the user - never auto-start
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            continue
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if now - last_dt < timedelta(days=cadence_days):
            continue
        try:
            _REFRESHERS[source]()
        except Exception:
            pass    # transient network failure; retried next elapse
        return      # at most one bulk refresh per tick