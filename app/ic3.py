"""
IC3 complaint helper: data model + trace prefill.

The FBI's Internet Crime Complaint Center (https://www.ic3.gov) accepts
complaints only through its manual web form at https://complaint.ic3.gov -
there is no API or bulk submission. This module therefore builds a
WORKSHEET that mirrors the real form's sections and fields, so the victim
or officer can copy answers straight into the form.

Facts about the form encoded here (verified against ic3.gov, 2026):

* Seven sections; the field lists below mirror the form's own labels.
* Narrative limits are hard: Description 3,500 chars, Technical details
  5,000, Witnesses 1,000, Other-agency reports 1,000. The UI enforces
  these so nothing is truncated on paste.
* Crypto transactions use Transaction Type "Cryptocurrency/Crypto ATM"
  with optional fields for crypto type, transaction ID/hash, originating/
  recipient wallet addresses and platforms, and ATM/kiosk details.
* IC3 says NOT to include Social Security numbers or dates of birth -
  the worksheet deliberately has no such fields.
* Complaints cannot be edited after filing; additions are filed as a new
  complaint marked as an update.

Per IC3's cryptocurrency guidance, "the most important information you can
provide" is: cryptocurrency addresses, amounts and types, transaction
hashes, and dates/times - exactly what a finished trace contains, so
`prefill_from_trace` turns a trace result into worksheet entries.
"""

from datetime import datetime, timezone

# Hard character limits on the real form's narrative fields.
LIMIT_DESCRIPTION = 3500
LIMIT_TECHNICAL = 5000
LIMIT_WITNESSES = 1000
LIMIT_OTHER_AGENCIES = 1000

# The form's Transaction Type dropdown, verbatim.
TRANSACTION_TYPES = [
    "Cryptocurrency/Crypto ATM",
    "Cash",
    "Check/Cashier's Check",
    "Debit Card/Credit Card",
    "Money Order",
    "Peer-to-peer Transfer",
    "Prepaid Card/Gift Card",
    "Wire Transfer",
    "Other",
]

# Friendly names for the crypto-type field.
ASSET_DISPLAY_NAMES = {
    "BTC": "Bitcoin (BTC)",
    "ETH": "Ethereum (ETH)",
    "USDT": "Tether (USDT)",
    "USDC": "USD Coin (USDC)",
}

# How many depth-1 movements to prefill before asking the user to trim.
MAX_PREFILL_TRANSACTIONS = 20


def empty_transaction() -> dict:
    """One financial-transaction block, mirroring the form's crypto fields."""
    return {
        "transaction_type": "Cryptocurrency/Crypto ATM",
        "amount_usd": "",          # the form wants USD; crypto amount kept too
        "crypto_amount": "",       # e.g. "0.5 BTC" (helper field, not on form)
        "date": "",                # YYYY-MM-DD
        "sent_or_lost": "Sent",
        "contacted_institution": "",
        "crypto_type": "",
        "tx_hash": "",
        "originating_wallet": "",
        "recipient_wallet": "",
        "originating_platform": "",   # exchange/app the victim paid from
        "recipient_platform": "",     # exchange the funds landed at (if known)
        "kiosk_name": "",             # only for crypto-ATM payments
        "kiosk_address": "",
    }


def empty_draft() -> dict:
    """A blank worksheet covering every section of the IC3 form."""
    return {
        "filer": {
            "filing_for_self": True,
            "name": "", "phone": "", "email": "", "business": "",
        },
        "complainant": {
            "name": "", "address": "", "city": "", "county": "",
            "country": "United States", "state": "", "zip": "",
            "phone": "", "email": "", "age_range": "", "is_minor": False,
        },
        "financial": {
            "money_sent_or_lost": True,
            "total_loss_usd": "",
            "transactions": [],
        },
        "subjects": [],   # each: name/business/address/phone/email/site/ip
        "description": "",
        "other": {
            "technical_details": "",
            "witnesses": "",
            "other_agencies": "",
            "is_update": False,
        },
    }


def empty_subject() -> dict:
    """One suspect/subject block (Section 4 of the form; all optional)."""
    return {"name": "", "business": "", "address": "", "phone": "",
            "email": "", "website_social": "", "ip": ""}


def _asset_display(asset: str) -> str:
    return ASSET_DISPLAY_NAMES.get(asset, asset)


def _date_from_ts(unix_ts) -> str:
    if not unix_ts:
        return ""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime(
        "%Y-%m-%d")


def prefill_from_trace(result: dict) -> dict:
    """Build worksheet suggestions from a finished trace result.

    Returns {"transactions": [...], "description": str, "notes": [...]}.
    Only movements leaving the victim wallet (depth-1 edges) become
    transaction blocks - those are the victim's actual payments.
    """
    victim = result.get("victim_address") or result.get("start_input", "")
    chain = result.get("chain", "")

    # Exit entities keyed by address, to fill "recipient platform".
    exit_by_address = {e["address"]: e for e in result.get("exits", [])}
    label_by_address = {}
    for node in result.get("nodes", []):
        exchange = [l for l in node.get("labels", [])
                    if l["category"] == "exchange"]
        if exchange:
            label_by_address[node["address"]] = exchange[0]["entity_name"]

    transactions = []
    seen = set()
    for edge in result.get("edges", []):
        if edge.get("depth") != 1 or edge["from_address"] != victim:
            continue
        key = (edge["txid"], edge["to_address"], edge["asset"])
        if key in seen:
            continue
        seen.add(key)
        tx = empty_transaction()
        tx["crypto_amount"] = (
            f"{edge['value']:,.8f}".rstrip("0").rstrip(".") +
            f" {edge['asset']}")
        if edge.get("value_usd") is not None:
            tx["amount_usd"] = f"{edge['value_usd']:.2f}"
        tx["date"] = _date_from_ts(edge.get("timestamp"))
        tx["crypto_type"] = _asset_display(edge["asset"])
        tx["tx_hash"] = edge["txid"]
        tx["originating_wallet"] = victim
        tx["recipient_wallet"] = edge["to_address"]
        tx["recipient_platform"] = label_by_address.get(edge["to_address"], "")
        transactions.append(tx)

    notes = []
    if len(transactions) > MAX_PREFILL_TRANSACTIONS:
        notes.append(
            f"{len(transactions)} outgoing payments were found; only the "
            f"first {MAX_PREFILL_TRANSACTIONS} were prefilled. Add the rest "
            f"manually if they are part of the crime.")
        transactions = transactions[:MAX_PREFILL_TRANSACTIONS]
    notes.append(
        "Prefilled USD amounts are approximations from the daily market "
        "price on the transaction date. If the victim has a receipt or "
        "exchange statement showing the exact USD value, use that figure "
        "instead - it is the better evidence.")
    notes.append(
        "Delete any prefilled payment that is NOT part of the crime "
        "(e.g. the victim's ordinary transactions).")

    # Description skeleton the victim edits, within the 3,500-char limit.
    exits = result.get("exits", [])
    exit_sentence = ""
    if exits:
        exit_names = ", ".join(sorted({e["entity"] for e in exits}))
        exit_sentence = (
            f" Blockchain tracing shows the funds moved to deposit "
            f"address(es) attributed to: {exit_names}.")
    description = (
        "[HOW IT STARTED - describe how the scammer first contacted you or "
        "how you found the website/app: platform, date, and what they "
        "claimed. Include names, usernames, phone numbers and email "
        "addresses they used.]\n\n"
        "[WHAT HAPPENED - describe what you were told, what you were "
        "promised, and how you were instructed to pay.]\n\n"
        f"I sent cryptocurrency from my wallet {victim} on the "
        f"{chain.capitalize()} blockchain. The transaction details "
        f"(dates, amounts, transaction IDs/hashes and receiving wallet "
        f"addresses) are listed in the financial transactions section of "
        f"this complaint.{exit_sentence}\n\n"
        "[HOW IT ENDED - describe when you realised it was a scam, any "
        "further demands (fees, taxes), and any contact since.]")

    return {
        "transactions": transactions,
        "description": description[:LIMIT_DESCRIPTION],
        "notes": notes,
    }
