"""
Input recognition: work out what the investigator pasted.

Given a raw string, classify it as an address or transaction id and identify
the likely chain from its format, so the UI can pre-select the right chain
and speak plainly about what will happen ("this is a Monero address - the
trail cannot be followed on-chain").

Format detection is reliable for the shapes below but the UI always lets the
user confirm/override, because some formats collide (e.g. a 64-hex string is
a txid on several chains).
"""

import re

from app import config

# Input kinds
KIND_ADDRESS = "address"
KIND_TXID = "txid"
KIND_UNKNOWN = "unknown"

# Regexes for supported/recognised formats.
BITCOIN_LEGACY_RE = re.compile(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$")
BITCOIN_BECH32_RE = re.compile(r"^(bc1)[a-z0-9]{20,80}$")
ETHEREUM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ETHEREUM_TXID_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
HEX64_TXID_RE = re.compile(r"^[a-fA-F0-9]{64}$")          # BTC txid (no 0x)
TRON_ADDRESS_RE = re.compile(r"^T[a-km-zA-HJ-NP-Z1-9]{33}$")
MONERO_ADDRESS_RE = re.compile(r"^[48][0-9AB][a-km-zA-HJ-NP-Z1-9]{93}$")
LITECOIN_ADDRESS_RE = re.compile(r"^(ltc1[a-z0-9]{20,80}|[LM][a-km-zA-HJ-NP-Z1-9]{26,33})$")


def detect_input(raw: str) -> dict:
    """Classify user input.

    Returns {
        "kind":      address | txid | unknown,
        "chain":     one of config.CHAIN_* or None,
        "traceable": bool,        # can Phase 1 trace it?
        "message":   plain-language explanation for the UI,
    }
    """
    value = raw.strip()

    if ETHEREUM_ADDRESS_RE.match(value):
        return {"kind": KIND_ADDRESS, "chain": config.CHAIN_ETHEREUM,
                "traceable": True,
                "message": "Ethereum address (also covers USDT/USDC and "
                           "other ERC-20 tokens held at this address)."}

    if ETHEREUM_TXID_RE.match(value):
        return {"kind": KIND_TXID, "chain": config.CHAIN_ETHEREUM,
                "traceable": True,
                "message": "Ethereum transaction hash."}

    if BITCOIN_BECH32_RE.match(value) or BITCOIN_LEGACY_RE.match(value):
        return {"kind": KIND_ADDRESS, "chain": config.CHAIN_BITCOIN,
                "traceable": True,
                "message": "Bitcoin address."}

    if HEX64_TXID_RE.match(value):
        return {"kind": KIND_TXID, "chain": config.CHAIN_BITCOIN,
                "traceable": True,
                "message": "Transaction ID (64 hex characters). Assuming "
                           "Bitcoin - please confirm the chain."}

    if TRON_ADDRESS_RE.match(value):
        return {"kind": KIND_ADDRESS, "chain": config.CHAIN_TRON,
                "traceable": True,
                "message": "Tron address (also covers TRC-20 USDT held at "
                           "this address). A free TronGrid API key in "
                           "Settings is recommended; focus transactions "
                           "are not yet supported on Tron."}

    if MONERO_ADDRESS_RE.match(value):
        return {"kind": KIND_ADDRESS, "chain": config.CHAIN_MONERO,
                "traceable": False,
                "message": "Monero address recognised. Monero is a privacy "
                           "coin: transactions hide sender, receiver and "
                           "amount, so the trail CANNOT be followed on the "
                           "blockchain. Consider serving legal process on "
                           "any exchange the suspect used to buy or sell "
                           "Monero."}

    if LITECOIN_ADDRESS_RE.match(value):
        return {"kind": KIND_ADDRESS, "chain": config.CHAIN_LITECOIN,
                "traceable": False,
                "message": "Litecoin address recognised. Litecoin tracing "
                           "is planned for a later version."}

    return {"kind": KIND_UNKNOWN, "chain": None, "traceable": False,
            "message": "This doesn't match any address or transaction "
                       "format the tool recognises. Check for missing "
                       "characters or extra spaces."}
