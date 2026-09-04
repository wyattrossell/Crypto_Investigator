"""
Forward tracing engine ("follow the money").

Breadth-first walk from the victim's wallet address, following outgoing
value hop by hop until it reaches:

* a LABELLED EXCHANGE / VASP  -> recorded as an EXIT POINT (the deliverable:
                                 where legal process can be served),
* a SANCTIONED address        -> flagged, not expanded,
* a SMART CONTRACT            -> flagged honestly as "not followed in this
                                 version" (DEX/bridge handling is Phase 3),
* a HIGH-ACTIVITY service     -> probable unlabelled exchange/service;
                                 flagged, not expanded (prevents graph
                                 explosion and false continuity),
* the depth / size limits     -> marked "unexpanded" so the investigator can
                                 see exactly where the trace stopped.

v0.1 value accounting: every outgoing movement above the dust threshold is
followed in full ("poison"/taint-by-touch style). The report states this.
Haircut and FIFO arrive with the taint-method selector in Phase 2.

Everything the engine asserts beyond raw transaction data (roles, exits,
high-activity flags) carries a `confidence` and `basis` so that the report
can separate on-chain fact from inference.
"""

import heapq
import itertools
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app import config, prices
from app.labels import store as label_store
from app.providers.base import FetchMemo, ProviderError
from app.providers.bitcoin import BitcoinProvider
from app.providers.ethereum import EthereumProvider
from app.providers.tron import TronProvider


def _dust_threshold_for(asset: str, params: dict) -> float:
    """Minimum value (display units) a movement must carry to be followed."""
    if asset == "BTC":
        return params["dust_btc"]
    if asset == "ETH":
        return params["dust_eth"]
    if asset == "TRX":
        return params.get("dust_trx", config.DEFAULT_DUST_THRESHOLD_TRX)
    # Stablecoins trade ~1:1 with USD; for other tokens the same numeric
    # threshold is applied in token units (documented limitation).
    return params["dust_token"]


class _ValueOrderedFrontier:
    """The trace frontier as a max-heap: the address that received the
    largest traced movement is examined FIRST. This changes only the ORDER
    addresses are expanded in - which branches each address follows is still
    governed solely by the search pattern, dust threshold and caps - but
    when a trace does hit its size caps, the big money is what got followed
    and the periphery is what was left unexpanded. Ties and unpriced values
    fall back to insertion order (breadth-first-like)."""

    def __init__(self):
        self._heap = []
        self._sequence = itertools.count()

    def push(self, address: str, depth: int, priority: float) -> None:
        heapq.heappush(self._heap,
                       (-priority, next(self._sequence), address, depth))

    def pop(self) -> tuple:
        _, _, address, depth = heapq.heappop(self._heap)
        return address, depth

    def __len__(self) -> int:
        return len(self._heap)


class ForwardTrace:
    """One breadth-first forward trace. Instantiate and call run()."""

    def __init__(self, trace_id: int, chain: str, start_input: str,
                 params: dict, progress_callback=None):
        self.trace_id = trace_id
        self.chain = chain
        self.start_input = start_input.strip()
        self.params = params
        # progress_callback(str) lets the API layer surface live status.
        self.progress = progress_callback or (lambda note: None)

        # Trace direction: forward follows the money out of the victim's
        # wallet; backward traces where the target wallet's funds came from.
        self.direction = params.get("direction", config.DIRECTION_FORWARD)
        if self.direction not in (config.DIRECTION_FORWARD,
                                  config.DIRECTION_BACKWARD):
            self.direction = config.DIRECTION_FORWARD

        # Single-flight fetch memo shared by the engine and its prefetch
        # workers: each distinct pull happens at most once per trace.
        self.memo = FetchMemo()
        if chain == config.CHAIN_BITCOIN:
            self._provider_factory = lambda: BitcoinProvider(
                trace_id=trace_id, memo=self.memo)
        elif chain == config.CHAIN_ETHEREUM:
            self._provider_factory = lambda: EthereumProvider(
                trace_id=trace_id, memo=self.memo)
        elif chain == config.CHAIN_TRON:
            self._provider_factory = lambda: TronProvider(
                trace_id=trace_id, memo=self.memo)
        else:
            raise ValueError(f"chain not traceable in this version: {chain}")
        self.provider = self._provider_factory()

        # Background prefetch pool (see _prefetch_task). Workers get their
        # own provider instances; the shared memo and the per-provider
        # throttle keep pulls deduplicated and inside every rate limit.
        self._prefetch_pool = None
        self._prefetch_seen = set()
        self._prefetch_local = threading.local()
        self._worker_providers = []
        self._worker_providers_lock = threading.Lock()

        # Spot prices used ONLY to order the frontier by approximate value
        # (never stored on evidence). Missing prices degrade to raw-amount
        # ordering.
        try:
            spot = prices.get_spot()
            self._spot_usd = {a: spot.get(a) for a in ("BTC", "ETH", "TRX")}
        except Exception:
            self._spot_usd = {}

        self.nodes = {}      # address -> node dict
        self.edges = []      # list of edge dicts
        self.exits = {}      # address -> exit summary
        self.warnings = []   # honest notes about limits hit / data gaps
        # Time-consistency rule, per address (unix seconds; None =
        # unconstrained). FORWARD: earliest arrival of traced funds - only
        # movements from that moment ON are followed (money cannot flow
        # backward in time). BACKWARD: latest moment the address sent the
        # traced funds onward - only its funding movements at or BEFORE
        # that moment are followed (money must arrive before it is sent).
        self.arrival = {}
        self.time_skipped = 0   # movements ignored by the rule
        # Search pattern (branch-selection policy) + honest accounting of
        # every branch the pattern chose not to examine.
        self.pattern = params.get("search_pattern",
                                  config.DEFAULT_SEARCH_PATTERN)
        if self.pattern not in config.SEARCH_PATTERNS:
            self.pattern = config.DEFAULT_SEARCH_PATTERN
        self.unexamined = []    # per-address skipped-branch records
        # Dust accounting: movements below the threshold are not followed,
        # but their count/value appear in the disposition of funds.
        self.dust_skipped = {"count": 0, "totals": {}}

    # ------------------------------------------------------------- prefetch

    def _approx_usd(self, movement: dict) -> float:
        """Approximate USD value of one movement at CURRENT spot prices.
        Used ONLY to order the frontier (largest first); reports value
        movements at their own transaction dates, separately."""
        asset = movement["asset"]
        if asset in config.STABLECOIN_USD:
            return movement["value"] * config.STABLECOIN_USD[asset]
        spot = self._spot_usd.get(asset)
        if spot:
            return movement["value"] * spot
        return movement["value"]   # unpriced: rank by raw amount

    def _start_prefetch_pool(self) -> None:
        if config.PREFETCH_WORKERS > 0:
            self._prefetch_pool = ThreadPoolExecutor(
                max_workers=config.PREFETCH_WORKERS,
                thread_name_prefix=f"trace{self.trace_id}-prefetch")

    def _stop_prefetch_pool(self) -> None:
        if self._prefetch_pool is not None:
            self._prefetch_pool.shutdown(wait=True, cancel_futures=True)
            self._prefetch_pool = None
        with self._worker_providers_lock:
            for provider in self._worker_providers:
                provider.close()
            self._worker_providers = []

    def _prefetch(self, address: str) -> None:
        """Queue a background warm-up of the pulls the engine will need for
        `address`. Cheap to call; duplicates are ignored."""
        if self._prefetch_pool is None or address in self._prefetch_seen:
            return
        self._prefetch_seen.add(address)
        self._prefetch_pool.submit(self._prefetch_task, address)

    def _worker_provider(self):
        """Per-worker-thread provider instance (providers keep per-request
        state, so threads never share one)."""
        provider = getattr(self._prefetch_local, "provider", None)
        if provider is None:
            provider = self._provider_factory()
            self._prefetch_local.provider = provider
            with self._worker_providers_lock:
                self._worker_providers.append(provider)
        return provider

    def _movements(self, provider, address: str) -> list:
        """The address's movements in this trace's direction: spends
        (forward) or funding sources (backward)."""
        if self.direction == config.DIRECTION_BACKWARD:
            return provider.incoming_movements(
                address, config.MAX_OUTGOING_TXS_PER_ADDRESS)
        return provider.outgoing_movements(
            address, config.MAX_OUTGOING_TXS_PER_ADDRESS)

    def _prefetch_task(self, address: str) -> None:
        """Speculatively perform the same pulls the engine's own expansion
        of `address` will make, purely to warm the shared fetch memo. All
        results and errors are discarded here - the engine's sequential
        pass over the same (now-memoised) data stays authoritative, so
        graph construction remains deterministic."""
        try:
            labels = label_store.lookup(address, self.chain)
            if any(label["category"] in ("exchange", "sanctioned", "mixer")
                   for label in labels):
                return   # stopping point: the engine will not fetch anything
            provider = self._worker_provider()
            if self.chain == config.CHAIN_ETHEREUM:
                if provider.is_contract(address):
                    return
                activity = provider.outgoing_count(address)
            elif self.chain == config.CHAIN_TRON:
                activity = provider.outgoing_count(address)
            else:
                activity = provider.address_summary(address)["tx_count"]
            if activity > config.HIGH_ACTIVITY_TX_THRESHOLD:
                return   # high-activity: the engine will not expand it
            self._movements(provider, address)
        except Exception:
            pass   # the engine's own fetch will surface any real error

    # ------------------------------------------------------------------ nodes

    def _ensure_node(self, address: str, depth: int) -> dict:
        """Get or create the node record for an address (keeps min depth)."""
        address = label_store.normalise_address(address, self.chain)
        node = self.nodes.get(address)
        if node is None:
            node = {
                "address": address,
                "chain": self.chain,
                "depth": depth,
                "role": config.ROLE_UNEXPANDED,
                "labels": label_store.lookup(address, self.chain),
                "flags": [],
                "basis": "",
            }
            if any(label["category"] == config.LABEL_CATEGORY_FLAGGED
                   for label in node["labels"]):
                # The agency's own fraud designation rides along on the node
                # (and becomes a finding); the address is still expanded -
                # following a flagged wallet's money out is the point.
                node["flags"].append("agency_flagged")
            self.nodes[address] = node
        else:
            node["depth"] = min(node["depth"], depth)
        return node

    def _classify_before_expansion(self, node: dict) -> str:
        """Decide whether an address is a stopping point BEFORE pulling its
        outgoing transactions. Returns the assigned role."""
        address = node["address"]

        # 1) Official sanctions list - highest priority, highest confidence.
        sanctioned = [l for l in node["labels"] if l["category"] == "sanctioned"]
        if sanctioned:
            node["role"] = config.ROLE_SANCTIONED
            node["basis"] = (f"Listed on the OFAC SDN list as "
                             f"'{sanctioned[0]['entity_name']}' (official "
                             f"source, high confidence).")
            return node["role"]

        # 2) Labelled mixing service: the trail is deliberately obscured
        #    beyond this point - flagged honestly, never "demixed".
        mixer = [l for l in node["labels"] if l["category"] == "mixer"]
        if mixer:
            best = mixer[0]
            node["role"] = config.ROLE_MIXER
            node["basis"] = (f"Attributed to mixing service "
                             f"'{best['entity_name']}' by source "
                             f"'{best['source']}' at {best['confidence']} "
                             f"confidence. Funds entering a mixer are "
                             f"deliberately obscured; this tool does not "
                             f"guess at continuity beyond it.")
            node["flags"].append("mixer_not_followed")
            return node["role"]

        # 3) Known exchange / custodial service - this is an EXIT.
        exchange = [l for l in node["labels"] if l["category"] == "exchange"]
        if exchange:
            best = exchange[0]
            node["role"] = config.ROLE_EXCHANGE
            node["basis"] = (f"Attributed to '{best['entity_name']}' by "
                             f"source '{best['source']}' at "
                             f"{best['confidence']} confidence.")
            return node["role"]

        # 4) Smart contract (Ethereum only): value routed through contracts
        #    (DEXes, bridges, mixers) is NOT followed in v0.1 - flag it.
        if self.chain == config.CHAIN_ETHEREUM:
            try:
                if self.provider.is_contract(address):
                    node["role"] = config.ROLE_CONTRACT
                    node["basis"] = ("Address holds smart-contract code. "
                                    "Funds entering contracts (swap, bridge, "
                                    "mixer) are flagged, not followed, in "
                                    "this version.")
                    node["flags"].append("contract_not_followed")
                    return node["role"]
            except ProviderError as exc:
                self.warnings.append(f"Could not check contract status of "
                                     f"{address}: {exc}")

        # 5) High-activity heuristic: probable unlabelled service.
        try:
            if self.chain == config.CHAIN_BITCOIN:
                summary = self.provider.address_summary(address)
                activity = summary["tx_count"]
                # Kept for the findings layer: a positive balance supports
                # a funds-at-rest determination.
                node["balance_raw"] = summary.get("balance_raw", 0)
            else:
                activity = self.provider.outgoing_count(address)
        except ProviderError as exc:
            self.warnings.append(f"Could not fetch activity of {address}: {exc}")
            activity = 0
        node["activity"] = activity
        if activity > config.HIGH_ACTIVITY_TX_THRESHOLD:
            node["role"] = config.ROLE_HIGH_ACTIVITY
            node["basis"] = (f"HEURISTIC (low confidence): {activity:,} "
                             f"transactions sent/handled suggests a service "
                             f"(exchange, payment processor), not a personal "
                             f"wallet. Not expanded, to avoid mixing "
                             f"unrelated funds into the trace.")
            node["flags"].append("high_activity_not_expanded")
            return node["role"]

        node["role"] = config.ROLE_INTERMEDIARY
        node["basis"] = "Funds passed through; no label matched."
        return node["role"]

    # ------------------------------------------------------------------ exits

    def _record_exit(self, node: dict, incoming_edges: list) -> None:
        """Register/extend an exit point (labelled custodial off-ramp)."""
        address = node["address"]
        best_label = node["labels"][0] if node["labels"] else None
        entity_name = best_label["entity_name"] if best_label else "Unknown"
        entry = self.exits.setdefault(address, {
            "address": address,
            "chain": self.chain,
            "entity": entity_name,
            "confidence": best_label["confidence"] if best_label else
                          config.CONFIDENCE_LOW,
            "source": best_label["source"] if best_label else "",
            # Agency compliant/non-compliant designation (FATF-referenced,
            # locally editable); None when the entity is not designated.
            "compliance": label_store.compliance_lookup(entity_name),
            "depth": node["depth"],
            "funding": [],       # the txids that put traced funds here
            "totals": {},        # asset -> summed value
        })
        for edge in incoming_edges:
            entry["funding"].append({
                "txid": edge["txid"],
                "timestamp": edge["timestamp"],
                "from_address": edge["from_address"],
                "to_address": edge["to_address"],
                "asset": edge["asset"],
                "value": edge["value"],
            })
            entry["totals"][edge["asset"]] = (
                entry["totals"].get(edge["asset"], 0.0) + edge["value"])

    # ------------------------------------------------------------------ run

    def run(self) -> dict:
        """Execute the trace and return the result document."""
        params = self.params
        focus_for_mode = (params.get("focus_txid") or "").strip()
        # Extended mode: ignore the depth slider and keep following until
        # every branch resolves into a classified outcome (exchange, funds
        # at rest, service, mixer, ...), bounded by EXTENDED_MAX_DEPTH and
        # the size caps. Works with or without a focus transaction.
        extended = bool(params.get("extended"))
        if extended:
            max_depth = config.EXTENDED_MAX_DEPTH
        else:
            max_depth = min(
                int(params.get("max_depth", config.DEFAULT_MAX_DEPTH)),
                config.MAX_DEPTH_LIMIT)
            if focus_for_mode:
                # With a focus transaction, hops are counted from the FIRST
                # SUSPECT WALLET (the payment's recipient): the focus
                # payment itself is the approach, not one of the user's
                # hops, so allow one extra movement internally.
                max_depth += 1

        # The trace always starts at the victim's wallet. If the investigator
        # supplied a focus transaction, only that payment sets the direction;
        # otherwise every outgoing movement from the wallet is followed.
        # The frontier is value-ordered: biggest traced movement first.
        queue = _ValueOrderedFrontier()
        self._start_prefetch_pool()
        victim = label_store.normalise_address(self.start_input, self.chain)
        victim_node = self._ensure_node(victim, depth=0)
        victim_node["role"] = config.ROLE_VICTIM
        victim_node["basis"] = (
            "Target wallet supplied by the investigator (backward "
            "source-of-funds trace: where did this wallet's money "
            "come from?)."
            if self.direction == config.DIRECTION_BACKWARD else
            "Victim wallet supplied by the investigator (starting point "
            "of the trace).")
        focus_txid = (params.get("focus_txid") or "").strip()
        if focus_txid:
            try:
                self._seed_from_focus_tx(victim, focus_txid, queue, max_depth)
            except Exception:
                self._stop_prefetch_pool()
                self.provider.close()
                raise
        else:
            queue.push(victim, 0, float("inf"))

        expanded = set()
        while queue:
            address, depth = queue.pop()
            if address in expanded:
                continue
            if len(self.nodes) >= config.MAX_TOTAL_ADDRESSES_PER_TRACE or \
               len(self.edges) >= config.MAX_TOTAL_EDGES_PER_TRACE:
                self.warnings.append(
                    "Trace size limit reached; peripheral branches were left "
                    "unexpanded. Narrow the dust threshold or depth, or run "
                    "a second trace from a specific address of interest.")
                break
            expanded.add(address)

            node = self._ensure_node(address, depth)
            self.progress(f"Hop {depth}/{max_depth}: examining "
                          f"{address[:18]}... "
                          f"({len(self.nodes)} addresses so far, "
                          f"{len(queue)} queued)")

            # Classify. Non-victim stopping roles are not expanded further.
            if node["role"] != config.ROLE_VICTIM:
                role = self._classify_before_expansion(node)
                if role in (config.ROLE_EXCHANGE, config.ROLE_SANCTIONED,
                            config.ROLE_MIXER, config.ROLE_CONTRACT,
                            config.ROLE_HIGH_ACTIVITY):
                    continue
            elif depth == 0:
                # The victim node still needs its activity fetched for context.
                pass

            if depth >= max_depth:
                node["flags"].append("max_depth_reached")
                # A pass-through address we chose not to expand is the edge
                # of the trail, and must be presented as such.
                if node["role"] == config.ROLE_INTERMEDIARY:
                    node["role"] = config.ROLE_UNEXPANDED
                    node["basis"] = ("Reached at the configured depth limit; "
                                     "outgoing funds from here were NOT "
                                     "examined. Re-trace from this address "
                                     "if the trail should continue.")
                continue

            # Pull and follow this address's movements (spends forward;
            # funding sources backward).
            try:
                movements = self._movements(self.provider, address)
            except ProviderError as exc:
                node["flags"].append("fetch_failed")
                self.warnings.append(
                    f"Could not fetch movements of {address}: {exc}")
                continue

            if len(movements) >= config.MAX_OUTGOING_TXS_PER_ADDRESS:
                node["flags"].append("outgoing_txs_capped")
                self.warnings.append(
                    f"{address}: only the most recent "
                    f"{config.MAX_OUTGOING_TXS_PER_ADDRESS} outgoing "
                    f"transactions were followed (address is busy).")

            eligible_count, _followed = self._follow_movements(
                address, depth, movements, queue, max_depth,
                time_bound=self.arrival.get(address), apply_pattern=True)

            if eligible_count == 0 and \
                    "fetch_failed" not in node["flags"] and \
                    "outgoing_txs_capped" not in node["flags"] and \
                    depth > 0:
                if self.direction == config.DIRECTION_FORWARD:
                    # No eligible onward movement after the traced funds
                    # arrived: the value is still sitting here - FUNDS AT
                    # REST, one of the strongest findings a trace makes.
                    node["flags"].append("no_onward_movements")
                else:
                    # Backward: no earlier funding found within the caps
                    # (mined coins, very old funds, or beyond page limits).
                    node["flags"].append("no_funding_found")

        if self.unexamined:
            skipped_total = sum(u["skipped_count"] for u in self.unexamined)
            asset_totals = {}
            for entry in self.unexamined:
                for asset, value in entry["skipped_totals"].items():
                    asset_totals[asset] = asset_totals.get(asset, 0) + value
            totals_text = ", ".join(f"{v:,.6f} {a}"
                                    for a, v in sorted(asset_totals.items()))
            self.warnings.append(
                f"Search pattern '{self.pattern}': {skipped_total} smaller "
                f"movement(s) at {len(self.unexamined)} address(es) were "
                f"NOT examined (totalling {totals_text}). This is a "
                f"deliberate speed/coverage trade-off; run a Thorough "
                f"trace for the complete record. The unexamined branches "
                f"are itemised in the JSON export.")

        if self.time_skipped:
            if self.direction == config.DIRECTION_BACKWARD:
                self.warnings.append(
                    f"Time-consistency rule: {self.time_skipped} "
                    f"movement(s) dated AFTER an address sent the traced "
                    f"funds onward were ignored - money received later "
                    f"cannot be the money that was already sent.")
            else:
                self.warnings.append(
                    f"Time-forward rule: {self.time_skipped} movement(s) "
                    f"dated BEFORE the traced funds arrived at their "
                    f"address were ignored - money cannot flow backward "
                    f"in time, so those movements cannot contain the "
                    f"victim's funds.")

        self._stop_prefetch_pool()
        self.provider.close()

        # Value movements in USD at their transaction dates (best-effort:
        # a price outage degrades to crypto-only amounts, never a failure).
        try:
            prices.annotate_usd(self.edges, self.exits, self.trace_id,
                                self.progress, self.warnings)
        except Exception as exc:
            self.warnings.append(f"USD valuation skipped ({exc}); amounts "
                                 f"are shown in crypto units only.")

        return self._build_result(victim, focus_txid, max_depth)

    # ------------------------------------------------------------- internals

    def _victim_sent_transaction(self, victim: str, txid: str,
                                 movements: list) -> bool:
        """True when the victim wallet is a spender/sender in the focus
        transaction (i.e. the transaction moves value OUT of the wallet)."""
        if self.chain == config.CHAIN_BITCOIN:
            # Esplora movements aggregate multi-input sources into a label
            # like "3 input addresses", so check the raw inputs instead.
            tx = self.provider.transaction(txid)
            input_addresses = {
                vin.get("prevout", {}).get("scriptpubkey_address")
                for vin in tx.get("vin", []) if vin.get("prevout")
            }
            return victim in input_addresses
        return any(
            label_store.normalise_address(m["from_address"], self.chain) ==
            victim for m in movements)

    def _seed_from_focus_tx(self, victim: str, focus_txid: str, queue,
                            max_depth) -> None:
        """Set the trace direction from one specific payment: only the focus
        transaction's outputs leaving the victim wallet become the depth-1
        frontier. Change returning to the victim wallet is excluded."""
        self.progress("Loading the focus transaction...")
        movements = self.provider.transaction_output_movements(focus_txid)
        if not movements:
            raise ValueError(
                f"The focus transaction {focus_txid} contains no traceable "
                f"value movements on {self.chain}.")

        # All movements share one transaction, hence one timestamp: backfill
        # any missing one (Etherscan's proxy endpoint omits it) so the
        # time-forward rule anchors at the payment time.
        known_ts = max((m["timestamp"] for m in movements
                        if m["timestamp"] is not None), default=None)
        for movement in movements:
            if movement["timestamp"] is None:
                movement["timestamp"] = known_ts

        if not self._victim_sent_transaction(victim, focus_txid, movements):
            recipients = {label_store.normalise_address(m["to_address"],
                                                        self.chain)
                          for m in movements}
            if victim in recipients:
                self.warnings.append(
                    "The focus transaction pays INTO the victim wallet (it "
                    "is an incoming payment), so it cannot set an outgoing "
                    "direction. The trace followed every outgoing movement "
                    "from the victim wallet instead. If you meant to trace "
                    "the sender of this payment, start a new trace using "
                    "the sender's address as the starting wallet.")
                queue.push(victim, 0, float("inf"))
                return
            raise ValueError(
                "The focus transaction does not involve the victim wallet "
                "(the wallet is neither a sender nor a recipient in it). "
                "Check that both the wallet address and the transaction "
                "hash were copied correctly.")

        followed = 0
        for movement in movements:
            destination = label_store.normalise_address(
                movement["to_address"], self.chain)
            if destination == victim:
                continue     # change/refund back to the victim wallet
            if self.chain == config.CHAIN_ETHEREUM and \
                    label_store.normalise_address(
                        movement["from_address"], self.chain) != victim:
                continue     # unrelated transfer inside the same tx
            movement = dict(movement)
            # Multi-input Bitcoin sources are attributed to the victim's
            # payment as a whole (standard practice when tracing one tx).
            movement["from_address"] = victim
            movement["is_self_transfer"] = False
            self._follow_movements(victim, 0, [movement], queue, max_depth)
            followed += 1
        if followed == 0:
            raise ValueError(
                "Every output of the focus transaction returns to the "
                "victim wallet itself (a self-transfer or consolidation), "
                "so there is no outgoing direction to follow. Trace the "
                "wallet without a focus transaction instead.")
        if self.chain == config.CHAIN_BITCOIN:
            tx = self.provider.transaction(focus_txid)
            input_count = len({
                vin.get("prevout", {}).get("scriptpubkey_address")
                for vin in tx.get("vin", []) if vin.get("prevout")})
            if input_count > 1:
                self.warnings.append(
                    f"The focus transaction spends inputs from {input_count} "
                    f"addresses (the victim wallet plus others, typically "
                    f"the same wallet's other addresses). Its outputs are "
                    f"attributed to the victim's payment as a whole.")

    def _select_branches(self, movements: list) -> tuple:
        """Apply the search pattern's branch-selection policy, per asset.
        Returns (selected, skipped). Thorough keeps everything; Rapid keeps
        the largest movements covering RAPID_VALUE_COVERAGE of the outgoing
        value; Balanced keeps movements above a minimum value share. Both
        are capped per address. Skipped branches are returned so they can
        be counted and reported - never silently dropped."""
        if self.pattern == config.SEARCH_PATTERN_THOROUGH or \
                len(movements) <= 1:
            return movements, []

        by_asset = {}
        for movement in movements:
            by_asset.setdefault(movement["asset"], []).append(movement)

        selected = []
        for asset_movements in by_asset.values():
            ranked = sorted(asset_movements, key=lambda m: -m["value"])
            total = sum(m["value"] for m in ranked) or 1.0
            if self.pattern == config.SEARCH_PATTERN_RAPID:
                covered = 0.0
                for movement in ranked[:config.RAPID_MAX_BRANCHES]:
                    if covered >= config.RAPID_VALUE_COVERAGE * total:
                        break
                    selected.append(movement)
                    covered += movement["value"]
            else:   # balanced
                cutoff = config.BALANCED_MIN_BRANCH_FRACTION * total
                selected.extend(
                    m for m in ranked[:config.BALANCED_MAX_BRANCHES]
                    if m["value"] >= cutoff)
        chosen = set(id(m) for m in selected)
        skipped = [m for m in movements if id(m) not in chosen]
        return selected, skipped

    def _record_unexamined(self, from_address, depth, skipped: list) -> None:
        """Account for branches the search pattern chose not to follow."""
        totals = {}
        for movement in skipped:
            totals[movement["asset"]] = (totals.get(movement["asset"], 0.0)
                                         + movement["value"])
        self.unexamined.append({
            "address": from_address,
            "depth": depth,
            "skipped_count": len(skipped),
            "skipped_totals": totals,
        })
        node = self.nodes.get(from_address)
        if node is not None and "branches_unexamined" not in node["flags"]:
            node["flags"].append("branches_unexamined")

    def _follow_movements(self, from_address, depth, movements, queue,
                          max_depth, time_bound=None,
                          apply_pattern=False) -> tuple:
        """Convert movements into graph edges and enqueue new addresses.
        `time_bound` enforces the time-consistency rule: forward, movements
        dated before the traced funds arrived at `from_address` are
        ignored; backward, its funding movements dated after it sent the
        traced funds onward are ignored. `apply_pattern` additionally
        applies the search pattern's branch selection (used for normal
        expansion, never for the focus seed)."""
        backward = self.direction == config.DIRECTION_BACKWARD
        eligible = []
        for movement in movements:
            if movement.get("is_self_transfer"):
                continue     # change back to the same address - not a hop
            if movement["value"] < _dust_threshold_for(movement["asset"],
                                                       self.params):
                self.dust_skipped["count"] += 1
                self.dust_skipped["totals"][movement["asset"]] = (
                    self.dust_skipped["totals"].get(movement["asset"], 0.0)
                    + movement["value"])
                continue     # dust - configured to ignore
            if time_bound is not None and movement["timestamp"] is not None:
                out_of_window = (movement["timestamp"] > time_bound
                                 if backward
                                 else movement["timestamp"] < time_bound)
                if out_of_window:
                    self.time_skipped += 1
                    continue
            eligible.append(movement)

        eligible_count = len(eligible)
        if apply_pattern:
            eligible, skipped = self._select_branches(eligible)
            if skipped:
                self._record_unexamined(from_address, depth, skipped)

        for movement in eligible:
            # The NEXT hop is the recipient (forward) or the funder
            # (backward); graph edges always point the way the money moved.
            if backward:
                next_address = label_store.normalise_address(
                    movement["from_address"], self.chain)
                edge_from, edge_to = next_address, from_address
            else:
                next_address = label_store.normalise_address(
                    movement["to_address"], self.chain)
                edge_from, edge_to = from_address, next_address
            edge = {
                "txid": movement["txid"],
                "timestamp": movement["timestamp"],
                "from_address": edge_from,
                "to_address": edge_to,
                "asset": movement["asset"],
                "value": movement["value"],
                "value_raw": movement.get("value_raw"),
                "depth": depth + 1,
            }
            self.edges.append(edge)

            # Time bound for the next hop's own expansion: forward, the
            # earliest arrival wins (min); backward, the latest onward-send
            # wins (max). None means unconstrained and stays so.
            bound = movement["timestamp"] if movement["timestamp"] \
                is not None else time_bound
            if next_address in self.arrival:
                previous = self.arrival[next_address]
                if previous is None or bound is None:
                    self.arrival[next_address] = None
                else:
                    self.arrival[next_address] = (
                        max(previous, bound) if backward
                        else min(previous, bound))
            else:
                self.arrival[next_address] = bound

            node = self._ensure_node(next_address, depth + 1)
            # Exit detection happens immediately so exits at the frontier are
            # captured even when depth limits stop expansion. (Backward, an
            # "exit" is a labelled exchange the funds CAME FROM - a records
            # target for the sending account.)
            if any(l["category"] == "exchange" for l in node["labels"]):
                node["role"] = config.ROLE_EXCHANGE
                self._classify_before_expansion(node)
                self._record_exit(node, [edge])
            elif any(l["category"] == "sanctioned" for l in node["labels"]):
                self._classify_before_expansion(node)
            else:
                queue.push(next_address, depth + 1,
                           self._approx_usd(movement))
                self._prefetch(next_address)

        return eligible_count, len(eligible)

    def _compute_exit_paths(self, victim: str) -> None:
        """Attach a hop-by-hop path to every exit: the 'traceroute' quoted
        in warrants. Shortest path by hops; where paths tie, the larger
        movement is preferred. Forward, paths run victim -> exit deposit
        address; backward, exit source -> target wallet. Hop lists are
        always emitted in money-flow order."""
        backward = self.direction == config.DIRECTION_BACKWARD
        adjacency = {}
        for edge in self.edges:
            key = edge["to_address"] if backward else edge["from_address"]
            adjacency.setdefault(key, []).append(edge)
        for edges_out in adjacency.values():
            edges_out.sort(key=lambda e: -(e.get("value_usd") or e["value"]))

        parent_edge = {victim: None}
        frontier = deque([victim])
        while frontier:
            current = frontier.popleft()
            for edge in adjacency.get(current, []):
                nxt = (edge["from_address"] if backward
                       else edge["to_address"])
                if nxt not in parent_edge:
                    parent_edge[nxt] = edge
                    frontier.append(nxt)

        for exit_entry in self.exits.values():
            address = exit_entry["address"]
            if address not in parent_edge:
                continue   # exit seeded outside the start path (unusual)
            hops = []
            cursor = address
            while parent_edge[cursor] is not None:
                edge = parent_edge[cursor]
                hops.append(edge)
                cursor = (edge["to_address"] if backward
                          else edge["from_address"])
            if not backward:
                hops.reverse()
            exit_entry["path"] = [{
                "hop": i,
                "from_address": e["from_address"],
                "to_address": e["to_address"],
                "txid": e["txid"],
                "timestamp": e["timestamp"],
                "asset": e["asset"],
                "value": e["value"],
                "value_usd": e.get("value_usd"),
            } for i, e in enumerate(hops, start=1)]

    # ------------------------------------------------------------- findings

    def _incoming_summary(self) -> dict:
        """Per-address traced inflow: totals, USD, funders, timestamps."""
        summary = {}
        for edge in self.edges:
            entry = summary.setdefault(edge["to_address"], {
                "totals": {}, "usd": 0.0, "usd_complete": True,
                "funders": set(), "first_ts": None, "last_ts": None})
            entry["totals"][edge["asset"]] = (
                entry["totals"].get(edge["asset"], 0.0) + edge["value"])
            if edge.get("value_usd") is None:
                entry["usd_complete"] = False
            else:
                entry["usd"] += edge["value_usd"]
            entry["funders"].add(edge["from_address"])
            ts = edge.get("timestamp")
            if ts:
                entry["first_ts"] = ts if entry["first_ts"] is None \
                    else min(entry["first_ts"], ts)
                entry["last_ts"] = ts if entry["last_ts"] is None \
                    else max(entry["last_ts"], ts)
        return summary

    def _outgoing_summary(self) -> dict:
        """Per-address traced OUTFLOW (mirror of _incoming_summary; used as
        the terminal-value measure in backward traces, where a terminal
        node is a SOURCE and its relevant value is what it sent)."""
        summary = {}
        for edge in self.edges:
            entry = summary.setdefault(edge["from_address"], {
                "totals": {}, "usd": 0.0, "usd_complete": True,
                "funders": set(), "first_ts": None, "last_ts": None})
            entry["totals"][edge["asset"]] = (
                entry["totals"].get(edge["asset"], 0.0) + edge["value"])
            if edge.get("value_usd") is None:
                entry["usd_complete"] = False
            else:
                entry["usd"] += edge["value_usd"]
            entry["funders"].add(edge["to_address"])
            ts = edge.get("timestamp")
            if ts:
                entry["first_ts"] = ts if entry["first_ts"] is None \
                    else min(entry["first_ts"], ts)
                entry["last_ts"] = ts if entry["last_ts"] is None \
                    else max(entry["last_ts"], ts)
        return summary

    def _build_findings(self, victim) -> tuple:
        """Classify every branch ending into an investigative outcome and
        account for where the traced value went (forward) or came from
        (backward). Returns (findings, disposition). Every finding states
        why it matters and what to do next - the trace's answer even when
        no labelled exchange is hit."""
        backward = self.direction == config.DIRECTION_BACKWARD
        incoming = self._incoming_summary()
        # Terminal value at a node: what it received (forward) or what it
        # sent toward the target (backward).
        terminal = self._outgoing_summary() if backward else incoming
        outgoing = {}
        for edge in self.edges:
            outgoing.setdefault(edge["from_address"], []).append(edge)

        def usd_of(entry):
            if entry and entry["usd_complete"] and entry["usd"]:
                return round(entry["usd"], 2)
            return None

        findings = []

        def add(kind, priority, title, detail, action, addresses,
                entry=None, extra=None):
            findings.append({
                "type": kind, "priority": priority, "title": title,
                "detail": detail, "action": action,
                "addresses": addresses,
                "totals": entry["totals"] if entry else {},
                "usd": usd_of(entry) if entry else None,
                "data": extra or {},
            })

        # 1) Named exchanges (already exits; repeated here so the findings
        #    list is the one complete answer).
        for exit_entry in self.exits.values():
            compliance = exit_entry.get("compliance")
            compliance_text = ""
            if compliance:
                compliance_text = (
                    " Designated "
                    + ("COMPLIANT - expect normal legal-process response."
                       if compliance["status"] == "compliant" else
                       "NON-COMPLIANT - consider MLAT/alternatives."))
            totals = ", ".join(f"{v:,.6f} {a}"
                               for a, v in exit_entry["totals"].items())
            if backward:
                add("named_exchange", 1,
                    f"Funds ORIGINATED from named exchange: "
                    f"{exit_entry['entity']}",
                    f"Exchange address {exit_entry['address']} sent "
                    f"{totals} of the traced funds toward the target "
                    f"wallet ({exit_entry['depth']} hop(s) back)."
                    f"{compliance_text}",
                    "Serve legal process on this custodian for the "
                    "SENDING account's records - it identifies who "
                    "funded the target wallet (source of funds).",
                    [exit_entry["address"]],
                    terminal.get(exit_entry["address"]))
            else:
                add("named_exchange", 1,
                    f"Named exchange reached: {exit_entry['entity']}",
                    f"Deposit address {exit_entry['address']} received "
                    f"{totals} of traced funds ({exit_entry['depth']} "
                    f"hop(s) from the victim).{compliance_text}",
                    "Serve legal process on this custodian - see the exit "
                    "point section and the traceroute export.",
                    [exit_entry["address"]],
                    terminal.get(exit_entry["address"]))

        # 2) Funds at rest - the money is still there.
        for node in self.nodes.values():
            if "no_onward_movements" not in node["flags"]:
                continue
            entry = incoming.get(node["address"])
            if not entry:
                continue
            since = ""
            if entry["last_ts"]:
                since = (" since " + datetime.fromtimestamp(
                    entry["last_ts"], tz=timezone.utc).strftime("%Y-%m-%d"))
            add("funds_at_rest", 2,
                f"FUNDS AT REST: "
                f"{', '.join(f'{v:,.6f} {a}' for a, v in entry['totals'].items())} "
                f"sitting unspent",
                f"Address {node['address']} received traced funds and has "
                f"made no onward movement{since} (as of the trace time). "
                f"The money is still reachable.",
                "Freeze/seizure candidate. Monitor this address; if the "
                "funds move, re-trace from here immediately. If the "
                "address belongs to a service (check the lookups), a "
                "freeze request can be served.",
                [node["address"]], entry)

        # 2b) Contact with wallets the agency itself flagged as fraudulent -
        #     a cross-case hit when the flag came from another case.
        for node in self.nodes.values():
            if "agency_flagged" not in node["flags"] or \
                    node["role"] == config.ROLE_VICTIM:
                continue
            flag_label = next(
                (l for l in node["labels"]
                 if l["category"] == config.LABEL_CATEGORY_FLAGGED), None)
            flag = (flag_label or {}).get("flag") or {}
            reason = flag.get("reason") or "no reason recorded"
            case_bits = ""
            if flag.get("case_name"):
                case_bits = (f" while working case '{flag['case_name']}'"
                             + (f" ({flag['case_number']})"
                                if flag.get("case_number") else ""))
            flagged_date = (flag.get("created_utc") or "")[:10]
            add("flagged_wallet_contact", 2,
                "Traced funds reached a wallet FLAGGED by this agency",
                f"Address {node['address']} was flagged as fraudulent by "
                f"this agency{case_bits}"
                + (f" on {flagged_date}" if flagged_date else "")
                + f" (reason: {reason}). Traced funds from THIS case "
                f"reached it - a likely link between cases/victims. The "
                f"flag is the agency's own designation, not an external "
                f"attribution.",
                "Compare the two cases (same scammer contact details, "
                "same story, same wallets?) and consider consolidating. "
                "Prioritise this branch for freeze/legal process - it is "
                "already known-bad.",
                [node["address"]], terminal.get(node["address"]))

        # 3) Probable exchange deposits (sweep pattern) + busy services.
        #    The sweep fingerprint is a FORWARD pattern; backward traces
        #    get a simpler busy-source finding instead.
        for node in self.nodes.values():
            if node["role"] != config.ROLE_HIGH_ACTIVITY:
                continue
            if backward:
                add("unidentified_service", 4,
                    "Unidentified busy service FUNDED the target wallet",
                    f"Address {node['address']} sent traced funds toward "
                    f"the target wallet and has "
                    f"{node.get('activity', 0):,} recent transactions - "
                    f"almost certainly a business service (exchange, "
                    f"payment processor), not a personal wallet. It is "
                    f"probably nameable.",
                    "Open the block-explorer and Chainabuse lookups on "
                    "this address - large service wallets are usually "
                    "tagged publicly. Once identified, serve process for "
                    "the sending account (source of funds).",
                    [node["address"]], terminal.get(node["address"]))
                continue
            service_entry = incoming.get(node["address"])
            # sweep check on each feeder of this busy wallet
            sweep_feeders = []
            for edge in self.edges:
                if edge["to_address"] != node["address"]:
                    continue
                feeder = edge["from_address"]
                feeder_entry = incoming.get(feeder)
                feeder_out = outgoing.get(feeder, [])
                if feeder == victim or not feeder_entry:
                    continue
                if len(feeder_out) != 1:
                    continue
                inflow = feeder_entry["totals"].get(edge["asset"], 0.0)
                if inflow <= 0 or edge["value"] < \
                        config.SWEEP_MIN_FORWARD_RATIO * inflow:
                    continue
                gap_ok = True
                if edge.get("timestamp") and feeder_entry["first_ts"]:
                    gap_ok = (edge["timestamp"] - feeder_entry["first_ts"]
                              <= config.SWEEP_MAX_GAP_HOURS * 3600)
                if gap_ok:
                    sweep_feeders.append((feeder, edge))
            if sweep_feeders:
                feeder, edge = sweep_feeders[0]
                add("probable_exchange_deposit", 3,
                    "PROBABLE EXCHANGE DEPOSIT (service not yet "
                    "identified)",
                    f"Address {feeder} matches the classic deposit-address "
                    f"fingerprint: it received the traced funds and swept "
                    f"~its full balance (tx {edge['txid']}) into "
                    f"{node['address']}, a wallet with "
                    f"{node.get('activity', 0):,} transactions - typical "
                    f"of an exchange hot wallet. HEURISTIC - verify "
                    f"before swearing to it.",
                    "Identify the service behind the busy wallet (open "
                    "the block-explorer and Chainabuse lookups on it - "
                    "explorers often name large hot wallets). Once named, "
                    "it becomes a records-request target for the deposit "
                    "address.",
                    [feeder, node["address"]], service_entry,
                    extra={"deposit_address": feeder,
                           "service_address": node["address"],
                           "sweep_txid": edge["txid"]})
            else:
                add("unidentified_service", 4,
                    "Unidentified busy service received traced funds",
                    f"Address {node['address']} has "
                    f"{node.get('activity', 0):,} transactions - almost "
                    f"certainly a business service (exchange, payment "
                    f"processor, gambling site), not a personal wallet. "
                    f"It is probably nameable.",
                    "Open the block-explorer and Chainabuse lookups on "
                    "this address - large service wallets are usually "
                    "tagged publicly. Once identified, serve process for "
                    "the account that received the funds.",
                    [node["address"]], service_entry)

        # 4) Consolidation points - branches of traced funds re-merging.
        #    (Forward-only: the backward mirror-image - one source feeding
        #    many branches - is simply the funding pattern itself.)
        for address, entry in ({} if backward else incoming).items():
            if len(entry["funders"]) < 2:
                continue
            node = self.nodes.get(address)
            if node is None or node["role"] in (
                    config.ROLE_EXCHANGE, config.ROLE_HIGH_ACTIVITY,
                    config.ROLE_VICTIM):
                continue   # merging at a service is expected, not a signal
            add("consolidation_point", 5,
                f"Consolidation point: {len(entry['funders'])} traced "
                f"branches merge",
                f"Address {address} collected traced funds from "
                f"{len(entry['funders'])} different addresses - a "
                f"collection wallet under one controller, and a natural "
                f"place to link multiple victims.",
                "Check this address against other cases/victim reports "
                "(Chainabuse lookup included). Consider a dedicated trace "
                "from it.",
                [address], entry)

        # 4b) Contact with third-party-reported scam addresses.
        for node in self.nodes.values():
            scam = next((l for l in node["labels"]
                         if l["category"] ==
                         config.LABEL_CATEGORY_SCAM_REPORT), None)
            if scam is None or node["role"] == config.ROLE_VICTIM:
                continue
            add("scam_reported_contact", 5,
                "Traced funds touched a publicly reported scam address",
                f"Address {node['address']} appears on a third-party scam "
                f"report list: {scam['entity_name']} (source: "
                f"{scam['source']}, {scam['confidence']} confidence, "
                f"unverified). This corroborates - but does not prove - "
                f"that the address is criminal infrastructure.",
                "Check the public report details (Chainabuse lookup on the "
                "address) and cite them as corroboration, not as proof. "
                "Consider flagging the wallet in this tool so future "
                "cases hit it.",
                [node["address"]], terminal.get(node["address"]))

        # 5) Sanctioned and mixer contacts.
        for node in self.nodes.values():
            if node["role"] == config.ROLE_SANCTIONED:
                add("sanctioned_contact", 6,
                    ("Traced funds CAME FROM an OFAC-SANCTIONED address"
                     if backward else
                     "Traced funds reached an OFAC-SANCTIONED address"),
                    f"{node['address']}: {node['basis']}",
                    "Document for charging decisions and consider an OFAC "
                    "referral; transacting with this address is itself "
                    "sanctionable conduct.",
                    [node["address"]], terminal.get(node["address"]))
            elif node["role"] == config.ROLE_MIXER:
                add("mixer_contact", 7,
                    ("Traced funds EMERGED from a mixing service"
                     if backward else
                     "Traced funds entered a mixing service"),
                    f"{node['address']}: {node['basis']}",
                    ("The trail is deliberately obscured behind this "
                     "point. Note the amount/time leaving the mixer for "
                     "the affidavit (use of a mixer evidences intent to "
                     "launder)." if backward else
                     "The trail is deliberately obscured here. Note the "
                     "amount/time entering the mixer for the affidavit "
                     "(use of a mixer evidences intent to launder); "
                     "consider timing analysis or legal process on the "
                     "mixer if it is a business."),
                    [node["address"]], terminal.get(node["address"]))

        # 6) Unresolved trail edges worth re-tracing (aggregated).
        unresolved = []
        for node in self.nodes.values():
            if node["role"] != config.ROLE_UNEXPANDED:
                continue
            entry = terminal.get(node["address"])
            if entry:
                unresolved.append((node["address"], entry))
        if unresolved:
            unresolved.sort(key=lambda item: -(item[1]["usd"] or
                                               max(item[1]["totals"].values()
                                                   or [0])))
            top = unresolved[:config.MAX_FINDINGS_PER_TYPE]
            listing = "; ".join(
                f"{addr} ({', '.join(f'{v:,.6f} {a}' for a, v in e['totals'].items())})"
                for addr, e in top)
            add("unresolved_edges", 9,
                f"{len(unresolved)} trail edge(s) not yet resolved",
                f"Traced funds reached these addresses but the trace "
                f"stopped (depth or size limits). Largest first: {listing}"
                + ("; ..." if len(unresolved) > len(top) else ""),
                "Re-trace from the largest of these, or increase depth / "
                "use Extended mode.",
                [addr for addr, _ in top])

        # Rank and cap per type (full detail always in the JSON export).
        findings.sort(key=lambda f: f["priority"])
        capped = []
        per_type = {}
        for finding in findings:
            per_type[finding["type"]] = per_type.get(finding["type"], 0) + 1
            if per_type[finding["type"]] <= config.MAX_FINDINGS_PER_TYPE:
                capped.append(finding)
        dropped = len(findings) - len(capped)
        if dropped:
            self.warnings.append(
                f"{dropped} lower-priority finding(s) beyond the first "
                f"{config.MAX_FINDINGS_PER_TYPE} per type are in the JSON "
                f"export only.")

        # ---- Disposition of funds: every terminal bucket accounted for.
        bucket_of_role = {
            config.ROLE_EXCHANGE: "Named exchanges",
            config.ROLE_HIGH_ACTIVITY: "Busy/unidentified services",
            config.ROLE_SANCTIONED: "OFAC-sanctioned addresses",
            config.ROLE_MIXER: "Mixing services",
            config.ROLE_CONTRACT: "Smart contracts (not followed)",
            config.ROLE_UNEXPANDED: "Unresolved trail edges",
        }
        buckets = {}
        for node in self.nodes.values():
            entry = terminal.get(node["address"])
            if not entry:
                continue
            if "no_onward_movements" in node["flags"]:
                bucket = "Funds at rest (unspent)"
            elif node["role"] in bucket_of_role:
                bucket = bucket_of_role[node["role"]]
            else:
                continue   # pass-through - value moved onward
            slot = buckets.setdefault(bucket, {
                "bucket": bucket, "count": 0, "totals": {},
                "usd": 0.0, "usd_complete": True})
            slot["count"] += 1
            for asset, value in entry["totals"].items():
                slot["totals"][asset] = slot["totals"].get(asset, 0) + value
            if entry["usd_complete"]:
                slot["usd"] += entry["usd"]
            else:
                slot["usd_complete"] = False

        skipped_totals = dict(self.dust_skipped["totals"])
        skipped_count = self.dust_skipped["count"]
        for entry in self.unexamined:
            skipped_count += entry["skipped_count"]
            for asset, value in entry["skipped_totals"].items():
                skipped_totals[asset] = skipped_totals.get(asset, 0) + value
        if skipped_count:
            buckets["Not examined (dust + smaller branches)"] = {
                "bucket": "Not examined (dust + smaller branches)",
                "count": skipped_count, "totals": skipped_totals,
                "usd": 0.0, "usd_complete": False}

        disposition = sorted(buckets.values(),
                             key=lambda b: -(b["usd"] if b["usd_complete"]
                                             else 0))
        usd_grand = sum(b["usd"] for b in disposition if b["usd_complete"])
        if usd_grand > 0:
            for bucket in disposition:
                bucket["share"] = (round(100 * bucket["usd"] / usd_grand, 1)
                                   if bucket["usd_complete"] else None)
        return capped, disposition

    def _build_result(self, victim, focus_txid, max_depth) -> dict:
        """Assemble the final result document stored on the trace row."""
        self._compute_exit_paths(victim)
        findings, disposition = self._build_findings(victim)
        # Rank exits: most value first, then fewest hops from the victim.
        ranked_exits = sorted(
            self.exits.values(),
            key=lambda e: (-max(e["totals"].values() or [0]), e["depth"]))

        unexpanded = [n for n in self.nodes.values()
                      if n["role"] == config.ROLE_UNEXPANDED]
        if unexpanded:
            self.warnings.append(
                f"{len(unexpanded)} address(es) at the edge of the trace were "
                f"not expanded (depth limit {max_depth}). Increase depth or "
                f"start a new trace from one of them if funds appear to "
                f"continue.")

        ordering_note = (
            "Expansion order: addresses were examined largest traced "
            "movement first (approximate USD at current prices, or raw "
            "amount when unpriced). Ordering affects only the sequence "
            "of examination and which branches were reached before the "
            "trace's size caps - never which movements an examined "
            "address follows. ")
        if self.direction == config.DIRECTION_BACKWARD:
            accounting = (
                "backward taint-by-touch (poison): every funding movement "
                "above the dust threshold is followed in full toward its "
                "source. On Bitcoin, each movement carries the input's "
                "full contribution to the funding transaction, not an "
                "apportioned share of what the address received. "
                "Time-consistency rule: each address is examined only up "
                "to the moment it sent the traced funds onward; later "
                "movements are ignored. " + ordering_note)
        else:
            accounting = (
                "taint-by-touch (poison): every outgoing movement above "
                "the dust threshold is followed in full. Per-unit "
                "apportionment (haircut/FIFO) is not applied in this "
                "version. Time-forward rule: each address is examined "
                "only from the moment the traced funds arrived at it; "
                "earlier movements are ignored. " + ordering_note
                + ("Hops are counted from the first suspect wallet (the "
                   "recipient of the focus transaction); the focus payment "
                   "itself is the approach to hop 1."
                   if focus_txid else ""))
        if self.direction == config.DIRECTION_BACKWARD:
            start_kind = "target wallet (backward source-of-funds trace)"
        else:
            start_kind = "victim wallet" + (
                " with focus transaction" if focus_txid else "")

        return {
            "app_version": config.APP_VERSION,
            "chain": self.chain,
            "direction": self.direction,
            "start_input": victim,
            "start_kind": start_kind,
            "victim_address": victim,
            "focus_txid": focus_txid,
            "search_pattern": self.pattern,
            "unexamined_branches": self.unexamined,
            "findings": findings,
            "disposition": disposition,
            "params": self.params,
            "accounting_method": accounting,
            "usd_valuation_note": prices.USD_VALUATION_NOTE,
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
            "exits": ranked_exits,
            "warnings": self.warnings,
            "stats": {
                "addresses": len(self.nodes),
                "edges": len(self.edges),
                "exit_points": len(ranked_exits),
            },
        }
