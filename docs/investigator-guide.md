# Crypto Investigator — Investigator's Guide

How to use the tool on a case, from the first paste to the freeze letter.
Installation and settings are in the [setup guide](setup-guide.md).

## What the tool does — and does not — do

It follows stolen or scammed cryptocurrency across the public blockchain
until the money reaches a **business that can be served with legal
process** (usually an exchange), or a dead end it reports honestly. It then
packages what a subpoena, warrant, freeze request or IC3 complaint needs.

It **does not identify people**. The account holder's identity comes from
the custodian's KYC records, through legal process. Anything the tool
infers rather than reads from the chain carries a confidence level and its
basis; it never invents continuity through mixers or privacy coins.

## The workflow (left panel, top to bottom)

**1. Case.** Every trace is saved to a case. Pick one or create one with
the report number. *Trace history* reopens earlier traces of the case;
*Case overlay map* merges every finished trace and rings wallets where
different victims' funds converge; *Export case* moves a whole case to
another machine.

**2. Victim wallet.** Paste the wallet the funds left from. The tool
recognises the chain (Bitcoin, Ethereum incl. USDT/USDC, Tron incl.
TRC-20 USDT) and tells you plainly when it cannot trace something (Monero,
Litecoin). Choose the direction: *Follow the money* (forward, the normal
victim trace) or *Source of funds* (backward, for a seized or suspect
wallet).

**3. Which payment (optional).** If you know the transaction hash of the
payment to the scammer, paste it. Only that payment is followed, its
recipient becomes the first suspect wallet, and every wallet is examined
only forward in time from when the money reached it. Leave it blank to
follow every outgoing payment.

**4. Search pattern.** *Rapid* follows the bulk of the value (fastest way
to a freeze target). *Balanced* follows every branch carrying at least 5%
(the default). *Thorough* follows everything above dust — run it for the
final report. Skipped branches are always counted and listed; selective
search is never presented as complete search.

**5. How far.** Hops to follow (3 is a good start), or *Extended* to keep
going until every branch resolves into an outcome. Dust thresholds are
under *Advanced*.

Press **Follow the money**. Traces take from seconds to many minutes: data
pulls are rate-limited by the free public APIs (see *Settings → Data
sources* for what is happening). You can close the tab; the trace keeps
running and *Trace history* reopens it.

## Reading the results

**Where the money stands** — the disposition of funds: how much reached
named exchanges, sits unspent, entered busy unidentified services, hit
the trail edge, or was not examined.

**Investigative findings** — ranked cards, each with *why it matters* and a
*next step*. The main kinds:

| Finding | Meaning | Typical action |
|---|---|---|
| 🏛 Named exchange reached | Funds arrived at a labelled custodian — an exit point | Serve process; draft the freeze letter |
| 💰 Funds at rest | Traced funds still sit at an address | Watch it; prepare a freeze request; if it moves, re-trace |
| 🎯 Probable exchange deposit | Sweep pattern typical of an exchange deposit address, service not yet named | Identify the service via explorer/Chainabuse |
| 🚩 Wallet flagged by this agency | Cross-case hit against the agency's own flags | Link the cases |
| 🤝 Wallet flagged by a partner agency | Hit against an imported flag pack | Contact the originating agency (details on the pack) |
| ⚠️ Publicly reported scam address | Scam-list or Chainabuse hit — corroboration, not proof | Cite as corroboration |
| 🧩 Address cluster | Several traced Bitcoin addresses presumed one wallet (common-input heuristic) | Request records for the whole cluster; state the heuristic |
| ⛔ / 🌀 Sanctioned / mixer contact | OFAC-listed address or a mixing service | Report; the trail is not guessed beyond a mixer |
| ⋯ Unresolved edges | Branches stopped at the depth or size limit | Re-trace from the edge address |

**Exit points** — one card per custodian with the deposit address, the
amounts (with ≈USD at the transaction dates), the attribution source and
confidence, the agency's compliance designation, a copyable hop-by-hop
*traceroute* for the warrant, and *Draft freeze/seizure request (PDF)*.

**Address clusters** (Bitcoin) — groups of addresses spent together, with
the evidencing transactions and a confidence level. CoinJoin-like
transactions are excluded. A cluster is an inference and does not identify
a person.

**Money-flow map** — victim on the left, exits on the right. Colours follow
the legend; a red ring is an agency flag, a dashed orange ring a partner
flag, a double orange ring a multi-victim convergence. Simplify toggles
collapse pass-through chains, group exchange addresses into one entity, and
fold minor branches; *Expand all* shows everything.

**Limits and data gaps** — every cap the trace hit, every branch it did not
examine, every fetch that failed. Read this before relying on a negative
result: *absence of an exit finding is not evidence of absence*.

## The address panel (click any node)

- **Role, basis, labels** with source and confidence.
- **Look up elsewhere**: block explorer, Chainabuse page.
- **🚩 Flag as fraudulent** — the agency's own designation; every future
  trace in any case raises a finding when it touches this wallet.
- **👁 Watch for movement** — re-checked on an interval; an alert badge
  appears in the header when anything changes.
- **Investigator note** — saved to the case; printed in the PDF address
  table as your own annotation.
- **⚠️ Check Chainabuse reports** — one API lookup (free tier: 10 a
  month; answers are cached 30 days). Reports are public, unverified
  claims.
- **📝 Plain-language summary** — a short factual paragraph set about the
  wallet, built from the tool's data (no AI), ready to copy into notes.
- **Draft freeze/seizure request (PDF)** for any address that received
  traced funds.

## Exports

*Evidence package (ZIP)* bundles the court report PDF, the full
chain-of-custody CSV, raw JSON, a DRAFT affidavit methodology, per-exit
traceroutes and freeze letters, with a SHA-256 manifest of every file.
Each is also available separately, plus a print-resolution map image for
exhibits.

The PDF report separates on-chain facts from inferences, states the
accounting method, lists every heuristic used, and ends with the custody
appendix any analyst can replay.

## Freeze and preservation requests

Generated as **DRAFT** letters with the funding transactions as Attachment
A, citing 18 U.S.C. § 2703(f) "to the extent applicable" with voluntary
cooperation in the alternative. The custodian's channel comes from the
legal-process directory (📇 *Legal contacts*), which carries a
per-entry confidence and a warning to verify the channel on the day of
service. When traced USDT/USDC sits at a wallet no exchange controls, the
letter goes to the token issuer (Tether/Circle), who can freeze at any
address. **Counsel or prosecutor review is required before service.**

## Watches, flags and partner packs

- **👁 Watches** — the list of watched wallets, alerts, *Check all now*, and
  *Re-trace from here* when funds move.
- **🚩 Flags** — the agency's designations, with reason and originating
  case. *Export flag pack* writes them (addresses, reasons, dates only — no
  case names) to a file for a partner agency; *Import a partner's pack*
  verifies its integrity hash and keeps it as a **separate** source that is
  never merged into your own flags.

## ⊞ Bulk triage

Paste up to 500 addresses from a seized device, an exchange production or a
victim list. Each is checked against every label source, your flags,
partner packs, scam lists, cached Chainabuse reports and the watch list,
optionally with live activity. Rows offer *Trace*, *Flag* and *Watch*; the
run is saved and exportable as CSV.

## IC3 complaint helper

Fills the FBI IC3 worksheet for the case, prefills the payments from the
latest trace, and prints it for copying into complaint.ic3.gov (IC3
accepts complaints only through its own form). Warn victims: anyone who
offers to *recover* the funds for a fee is almost certainly a second scam.

## AI assistant (optional, off by default)

If the agency enables it in Settings, the assistant can explain a trace in
plain language, answer questions about it, and draft an IC3 narrative.
Output is banner-marked as **assistance, not evidence**, never enters
court documents automatically, and every exchange is stored in the AI
activity log. A local model server keeps case data on the machine.

## Honesty rules the tool follows

1. Heuristics are never presented as facts; every inference carries a
   confidence and its basis.
2. Dead ends are flagged, never bridged by guesswork.
3. Every data pull is custody-logged (URL, UTC time, SHA-256).
4. Public data only.
5. Partner and third-party designations are always named as such.

## Glossary

**Hop** — one movement of the money to a new address. **Dust** — amounts too
small to follow. **Exit point** — an address attributed to a custodian
where process can be served. **Funds at rest** — traced value still
unspent. **Taint-by-touch** — every movement above dust is followed in
full (no apportionment). **Cluster** — Bitcoin addresses presumed one
wallet because they were spent together. **Flag pack** — a file of one
agency's wallet designations shared with another.
