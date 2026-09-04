# Crypto Investigator

A cryptocurrency tracing and warrant-preparation tool for law-enforcement
investigators. Follows a victim's funds across public blockchains (Bitcoin,
Ethereum/ERC-20, Tron/TRC-20) until they reach a custodial off-ramp
(exchange/VASP) — or backward from a seized/suspect wallet to its source of
funds — then packages the addresses, transaction IDs, timestamps and
amounts needed to draft a subpoena, search warrant, or freeze request to
that custodian (or, for USDT/USDC, to the token issuer, which can freeze
even self-custodied wallets).

**This tool does not identify people.** It identifies *where legal process
can be served*; the account holder's identity comes from the custodian's KYC
records via that legal process.

## Quick start

**Installed build (recommended for agencies):** run
`CryptoInvestigator-Setup-v<version>.exe` (no administrator rights
needed) or unzip the portable build anywhere, then open **Crypto
Investigator** from the Start menu or desktop. No console window appears;
an icon in the notification area (system tray) shows the program is
running and offers **Open** and **Quit**. Case data lives in
`%LOCALAPPDATA%\CryptoInvestigator\data` (shown in Settings); upgrades
and uninstalls leave it in place.

**From source:**

```
python -m pip install -r requirements.txt
python run.py
```

`python run.py` keeps a console for log output; `pythonw run.pyw` (or the
desktop shortcut made by `powershell -ExecutionPolicy Bypass -File
tools\make_shortcut.ps1`) runs without one. Quit from the tray icon or
the **Quit** button in the header. To build the installer yourself, see
`tools/build_release.py` (needs `requirements-build.txt` and, for the
setup.exe, the free Inno Setup 6).

The app opens at http://127.0.0.1:8321 (localhost only — nothing is exposed
to the network). Then:

1. **No accounts are required** — every chain (Bitcoin, Ethereum, Tron)
   and USD pricing work out of the box on free keyless public APIs.
   In **Settings**, run the three label downloads once: **OFAC sanctions
   list**, **exchange label packs (GraphSense)** and the **scam blacklist
   (ScamSniffer)**, and fill in the **agency letterhead** fields if you
   will generate freeze requests. Optional free keys (Etherscan,
   CoinGecko, TronGrid) only raise rate limits.
2. Create a case, paste the **victim's wallet address**, and — if you know
   which payment went to the scammer — the **transaction hash** of that
   payment so only it is followed (leave blank to follow every outgoing
   payment). Press **Follow the money**.
3. Review the findings, exit points and the flow map, then download the
   **evidence package** (report PDF + custody CSV + raw data + affidavit
   draft + traceroutes + freeze letters, with a SHA-256 manifest) or the
   individual exports, including a print-resolution **map image** for
   exhibits.
4. **🚩 Flag** wallets you determine are fraudulent and **👁 watch**
   wallets that matter (funds at rest, exit deposits): flags raise
   findings in every future trace of any case; watches re-check on an
   interval and alert the moment activity changes.
5. For a wallet that received traced funds, use **Draft freeze/seizure
   request (PDF)** — a DRAFT preservation/asset-hold letter with the
   funding transactions as an exhibit, addressed to the custodian, or to
   the token issuer (Tether/Circle) when traced USDT/USDC sits at a
   wallet no exchange controls. Counsel/prosecutor review is required
   before service; channels come from the editable directory
   `data/labels/custodian_contacts.json`, browsable in-app (**📇 Legal
   contacts**).
6. **IC3 complaint helper** (header button): fill out the FBI IC3 complaint
   worksheet for the case, prefill the crypto payments from the trace, and
   print the worksheet to copy into https://complaint.ic3.gov (IC3 accepts
   complaints only through its own web form).
7. Working multiple victims? **🗺 Case overlay map** merges every finished
   trace of a case and rings the wallets where different victims' funds
   converge. **Export case** moves a whole case between machines.
8. Optional **🤖 AI assistant** (off by default): configure Claude,
   ChatGPT, or any OpenAI-compatible endpoint — including a local model
   via Ollama so case data never leaves the machine — to get
   plain-language trace explanations, Q&A, and IC3 narrative drafts.
   Every AI exchange is recorded in full in an audit log, and AI output
   is assistance, never evidence: it is banner-marked and never enters
   court reports automatically.

## Design rules (non-negotiable)

- Heuristics are never presented as facts — every inference carries a
  confidence level and its basis.
- Dead ends (mixers, bridges, privacy coins, smart contracts) are flagged
  honestly; the tool never fabricates continuity.
- Every data pull is recorded in a chain-of-custody log (exact URL, UTC
  timestamp, SHA-256 of the raw response) so any analyst can reproduce it.
- Public data only.

## Documentation

- `docs/validation/` — **Technical Validation Document** (full methodology
  and verification procedures for outside experts/court) and a two-page
  **Overview / Quick Start**. Regenerate for the current version with
  `python tools/make_docs.py`.
- `docs/feature-roadmap.md` — planned features and their feasibility.

## Version

See `VERSION`, `CHANGELOG.md` for what each release adds, and
`docs/listings/` for the complete program listing of each version.
