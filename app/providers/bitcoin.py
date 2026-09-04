"""
Bitcoin data provider backed by the Esplora REST API.

Default host is mempool.space; blockstream.info exposes the identical API and
can be swapped in via settings. No API key is required by either.

The tracing engine consumes two things from this module:

* address_summary(address)    -> activity stats (tx count, balances)
* outgoing_movements(address) -> normalised list of spends FROM the address,
                                 each with txid, timestamp, destination
                                 address and value.

Normalised movement dict (shared shape with the Ethereum provider):
    {
        "txid":        str,
        "timestamp":   int | None,   # unix seconds; None if unconfirmed
        "from_address": str,         # the address being traced
        "to_address":  str,
        "asset":       "BTC",
        "value":       float,        # in BTC (display units)
        "value_raw":   int,          # in satoshis (exact)
    }
"""

import json

from app import config, database
from app.providers.base import ProviderClient

# Esplora returns address transactions in pages of 25.
ESPLORA_PAGE_SIZE = 25
# Upper bound of transaction pages pulled per address; combined with
# MAX_OUTGOING_TXS_PER_ADDRESS this stops hot wallets exploding a trace.
MAX_PAGES_PER_ADDRESS = 4


class BitcoinProvider(ProviderClient):
    """Esplora-compatible Bitcoin explorer client."""

    provider_name = "mempool.space"

    def __init__(self, trace_id=None, memo=None):
        super().__init__(trace_id=trace_id, memo=memo)
        # The endpoint is user-swappable (Settings screen) with a sane
        # default. When the configured endpoint is one of the two known
        # Esplora hosts, requests ROUND-ROBIN across both - they serve the
        # identical API, which roughly doubles throughput (each host has
        # its own rate-limit budget). A custom endpoint disables this.
        self.api_base = database.get_setting(
            "bitcoin_api_base", config.DEFAULT_BITCOIN_API_BASE).rstrip("/")
        known = {config.DEFAULT_BITCOIN_API_BASE.rstrip("/"),
                 config.FALLBACK_BITCOIN_API_BASE.rstrip("/")}
        known.update(base.rstrip("/")
                     for base in config.ESPLORA_EXTRA_BASES)
        if self.api_base in known:
            self.api_bases = [self.api_base] + \
                sorted(known - {self.api_base})
        else:
            self.api_bases = [self.api_base]
        self._request_count = 0

    # -- raw endpoint wrappers ---------------------------------------------

    def _get_json(self, path: str, cacheable: bool) -> object:
        """One Esplora call, alternating hosts. The cache key is the PATH
        (host-independent) so an immutable record fetched from either host
        is one evidence-cache entry; the custody log records the exact
        host and URL actually used. Rate limits are throttled per host."""
        base = self.api_bases[self._request_count % len(self.api_bases)]
        self._request_count += 1
        if "blockstream" in base:
            self.provider_name = "blockstream.info"
        elif "emzy" in base:
            self.provider_name = "mempool.emzy.de"
        else:
            self.provider_name = "mempool.space"
        body = self.fetch(f"{base}{path}", cacheable=cacheable,
                          cache_key=f"esplora:{path}")
        return json.loads(body)

    def address_summary(self, address: str) -> dict:
        """Lifetime stats for an address.

        Not cached: an address can always receive new transactions, so this
        must reflect the chain at acquisition time (the custody log records
        exactly when that was)."""
        data = self._get_json(f"/address/{address}", cacheable=False)
        chain_stats = data.get("chain_stats", {})
        funded = chain_stats.get("funded_txo_sum", 0)
        spent = chain_stats.get("spent_txo_sum", 0)
        return {
            "address": address,
            "tx_count": chain_stats.get("tx_count", 0),
            "total_received_raw": funded,
            "balance_raw": funded - spent,
        }

    def transaction(self, txid: str) -> dict:
        """One transaction by id. Confirmed transactions are immutable, so
        this response is cached permanently."""
        return self._get_json(f"/tx/{txid}", cacheable=True)

    def _address_transactions(self, address: str, limit: int) -> list:
        """Up to `limit` most-recent transactions touching an address,
        following Esplora's txid-cursor pagination."""
        transactions = []
        last_txid = None
        for _ in range(MAX_PAGES_PER_ADDRESS):
            path = (f"/address/{address}/txs/chain/{last_txid}"
                    if last_txid else f"/address/{address}/txs")
            page = self._get_json(path, cacheable=False)
            if not page:
                break
            transactions.extend(page)
            if len(transactions) >= limit or len(page) < ESPLORA_PAGE_SIZE:
                break
            last_txid = page[-1]["txid"]
        return transactions[:limit]

    # -- normalised view used by the tracing engine -------------------------

    # -- transaction input/output record for address clustering ------------

    def _record_tx(self, tx: dict) -> None:
        """Remember each parsed transaction's input addresses and output
        values (no extra network calls) for the common-input-ownership
        clustering step (app/tracing/cluster.py)."""
        store = self.__dict__.setdefault("tx_inputs", {})
        txid = tx.get("txid")
        if not txid or txid in store:
            return
        store[txid] = {
            "inputs": {vin.get("prevout", {}).get("scriptpubkey_address")
                       for vin in tx.get("vin", []) if vin.get("prevout")},
            "outputs": [vout.get("value", 0) for vout in tx.get("vout", [])],
        }

    def outgoing_movements(self, address: str, max_txs: int) -> list:
        """Spends FROM `address`: for every transaction where the address
        appears in an input, emit one movement per output.

        NOTE (documented limitation, v0.1): without change-address detection
        (Phase 2) the address's own change output is included and labelled
        `is_possible_change` when it pays back to the same address only."""
        movements = []
        for tx in self._address_transactions(address, max_txs):
            self._record_tx(tx)
            input_addresses = {
                vin.get("prevout", {}).get("scriptpubkey_address")
                for vin in tx.get("vin", [])
                if vin.get("prevout")
            }
            if address not in input_addresses:
                continue  # address only received in this tx; not a spend

            timestamp = tx.get("status", {}).get("block_time")
            for vout in tx.get("vout", []):
                destination = vout.get("scriptpubkey_address")
                if destination is None:
                    continue  # OP_RETURN or non-standard script
                value_sats = vout.get("value", 0)
                movements.append({
                    "txid": tx["txid"],
                    "timestamp": timestamp,
                    "from_address": address,
                    "to_address": destination,
                    "asset": "BTC",
                    "value": value_sats / config.SATOSHIS_PER_BTC,
                    "value_raw": value_sats,
                    # Same-address output is change by definition; other
                    # change detection is Phase 2.
                    "is_self_transfer": destination == address,
                })
        return movements

    def incoming_movements(self, address: str, max_txs: int) -> list:
        """Funding sources OF `address` (backward tracing): for every
        transaction where the address appears in an OUTPUT, emit one
        movement per input address.

        Value accounting (documented in the report): each movement carries
        the input's full contribution to the funding transaction
        (poison-style backward taint), not an apportioned share of what the
        address actually received - fees, change and other outputs are not
        deducted per input."""
        movements = []
        for tx in self._address_transactions(address, max_txs):
            self._record_tx(tx)
            received = any(
                vout.get("scriptpubkey_address") == address
                for vout in tx.get("vout", []))
            if not received:
                continue  # address only spent in this tx; not a funding
            timestamp = tx.get("status", {}).get("block_time")
            for vin in tx.get("vin", []):
                prevout = vin.get("prevout") or {}
                source = prevout.get("scriptpubkey_address")
                if source is None:
                    continue  # coinbase or non-standard input
                value_sats = prevout.get("value", 0)
                movements.append({
                    "txid": tx["txid"],
                    "timestamp": timestamp,
                    "from_address": source,
                    "to_address": address,
                    "asset": "BTC",
                    "value": value_sats / config.SATOSHIS_PER_BTC,
                    "value_raw": value_sats,
                    "is_self_transfer": source == address,
                })
        return movements

    def transaction_output_movements(self, txid: str) -> list:
        """Movements for a trace that STARTS from a txid rather than an
        address: every output of that transaction."""
        tx = self.transaction(txid)
        self._record_tx(tx)
        timestamp = tx.get("status", {}).get("block_time")
        input_addresses = sorted({
            vin.get("prevout", {}).get("scriptpubkey_address") or "coinbase"
            for vin in tx.get("vin", [])
        })
        source_label = input_addresses[0] if len(input_addresses) == 1 \
            else f"{len(input_addresses)} input addresses"
        movements = []
        for vout in tx.get("vout", []):
            destination = vout.get("scriptpubkey_address")
            if destination is None:
                continue
            value_sats = vout.get("value", 0)
            movements.append({
                "txid": txid,
                "timestamp": timestamp,
                "from_address": source_label,
                "to_address": destination,
                "asset": "BTC",
                "value": value_sats / config.SATOSHIS_PER_BTC,
                "value_raw": value_sats,
                "is_self_transfer": False,
            })
        return movements
