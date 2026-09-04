"""
Bitcoin address clustering by the common-input-ownership heuristic.

Rule: every input of one Bitcoin transaction was signed by whoever spent
it, so the addresses behind a transaction's inputs are presumed controlled
by ONE wallet. Chaining that presumption across transactions gives
clusters. The heuristic has court history (it underpins commercial
tracing tools and was examined in United States v. Sterlingov), but it is
a HEURISTIC with known failure modes, so this module is glass-box:

* CoinJoin-like transactions (many inputs and several equal-value
  outputs) are EXCLUDED - their inputs belong to different people by
  design - and the exclusion is reported.
* Every cluster lists the exact transaction(s) that evidence it.
* Confidence is MEDIUM by default and LOW when a very wide co-spend
  (a possible service batching payments) contributed.
* Clusters containing a labelled exchange/service address are named as
  that service's wallet cluster, not as a suspect.

Input: the per-transaction input/outputs the Bitcoin provider recorded
while parsing movements (no extra network calls).
"""

from collections import Counter

from app import config


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def is_coinjoin_like(inputs: set, output_values: list) -> bool:
    """Many inputs plus several outputs of identical value: the CoinJoin
    fingerprint. Such transactions must not link their inputs."""
    if len(inputs) < config.CLUSTER_COINJOIN_MIN_INPUTS:
        return False
    if not output_values:
        return False
    most_common = Counter(output_values).most_common(1)[0][1]
    return most_common >= config.CLUSTER_COINJOIN_MIN_EQUAL_OUTPUTS


def build_clusters(tx_inputs: dict, nodes: dict) -> tuple:
    """tx_inputs: {txid: {"inputs": set(addresses), "outputs": [sats,...]}}
    nodes: the trace's address -> node dict.
    Returns (clusters, notes). Each cluster:
      {id, addresses, traced_addresses, size, evidence_txids, confidence,
       basis, service_label}"""
    uf = _UnionFind()
    evidence = {}          # address -> set(txids)
    wide_txids = set()
    skipped_coinjoin = []
    for txid, record in tx_inputs.items():
        inputs = {a for a in record.get("inputs", set()) if a}
        if len(inputs) < 2:
            continue
        if is_coinjoin_like(inputs, record.get("outputs", [])):
            skipped_coinjoin.append(txid)
            continue
        if len(inputs) >= config.CLUSTER_LOW_CONFIDENCE_INPUTS:
            wide_txids.add(txid)
        inputs = sorted(inputs)
        first = inputs[0]
        for other in inputs[1:]:
            uf.union(first, other)
        for address in inputs:
            evidence.setdefault(address, set()).add(txid)

    groups = {}
    for address in evidence:
        groups.setdefault(uf.find(address), set()).add(address)

    clusters = []
    for members in groups.values():
        traced = sorted(a for a in members if a in nodes)
        if len(traced) < 1 or len(members) < 2:
            continue
        txids = set()
        for address in members:
            txids |= evidence.get(address, set())
        low = any(t in wide_txids for t in txids)
        service_label = None
        for address in traced:
            for label in nodes[address].get("labels", []):
                if label["category"] in ("exchange", "mixer"):
                    service_label = label["entity_name"]
                    break
            if service_label:
                break
        basis = (
            f"Common-input-ownership heuristic: these {len(members)} "
            f"addresses appear together as inputs of {len(txids)} "
            f"transaction(s), so one party is presumed to have controlled "
            f"all of them at the time of spending. CoinJoin-like "
            f"transactions were excluded. This is an inference, not an "
            f"on-chain fact.")
        if low:
            basis += (" One evidencing transaction had "
                      f"{config.CLUSTER_LOW_CONFIDENCE_INPUTS}+ inputs, "
                      "which is also how services batch payouts, so "
                      "confidence is LOW.")
        if service_label:
            basis += (f" A member is attributed to '{service_label}', so "
                      f"this is most likely that service's wallet cluster.")
        clusters.append({
            "addresses": sorted(members)[:config.CLUSTER_MAX_LISTED_ADDRESSES],
            "size": len(members),
            "traced_addresses": traced,
            "evidence_txids": sorted(txids)[:25],
            "evidence_count": len(txids),
            "confidence": (config.CONFIDENCE_LOW if low
                           else config.CONFIDENCE_MEDIUM),
            "basis": basis,
            "service_label": service_label,
        })
    # Biggest, most-traced clusters first; stable ids.
    clusters.sort(key=lambda c: (-len(c["traced_addresses"]), -c["size"]))
    for index, cluster in enumerate(clusters, start=1):
        cluster["id"] = f"C{index}"
    notes = []
    if skipped_coinjoin:
        notes.append(
            f"Clustering skipped {len(skipped_coinjoin)} CoinJoin-like "
            f"transaction(s) whose inputs cannot be assumed to share an "
            f"owner.")
    return clusters, notes
