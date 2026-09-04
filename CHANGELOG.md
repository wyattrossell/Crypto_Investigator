# Changelog

All notable changes to Crypto Investigator. Each release lists functionality
changes, dependencies, and known limitations, per the project's iteration
directives. The complete program listing for each version is generated at
`docs/listings/v{version}-full-listing.txt`.

## v1.0.0 — 2026-09-04 (First multi-agency release: documentation)

### Functionality
- **Documentation set for distribution.** New `docs/setup-guide.md`
  (for agency IT: what is installed, install options, where data lives
  and how to relocate it, first-run configuration, the complete list of
  outbound hosts for firewall rules, every optional key and mode with
  where to obtain it, security notes for reviewers, upgrading, building
  and signing a release, troubleshooting) and `docs/investigator-guide.md`
  (the workflow, how to read findings and exit points, the address
  panel tools, exports, freeze letters, watches/flags/packs, bulk
  triage, IC3, the AI assistant, the honesty rules, a glossary).
  `docs/release-checklist.md` records the release procedure. README
  rewritten for the public repository with screenshots
  (`docs/images/`).
- **Technical Validation Document and Overview brought up to date**
  with everything added since v0.10: multi-backend data sources, all
  label and scam-report sources, agency and partner designations,
  clustering, the new findings, the new outputs, DPAPI key storage,
  Tron support, and the four label downloads. Several statements in
  the previous versions' validation documents were stale (e.g. "no
  wallet clustering", "keys stored unencrypted", "Tron not traceable");
  from this version the document matches the program.
- Version 1.0.0 marks the first build handed to other agencies. No
  program behaviour changed in this release.

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. The repository ships without a LICENSE file until the owning agency
   chooses one; the installer and listing pick it up automatically once
   added.
2. Builds are unsigned (SmartScreen warns on first run) until the agency
   signs them.
3. v0.14.0 limitations still apply.

## v0.14.0 — 2026-09-04 (Data sources: keyed Bitcoin, Alchemy, self-hosted nodes, live rate-limit counters)

### Functionality
- **Data Sources panel (Settings).** Every data source the tool uses is
  listed with its chain, what it is used for, its free tier and paid
  upgrades (figures read from each provider's own pricing/limits page,
  with the verification date and a link), whether it is configured and
  which one is ACTIVE, and live counters for this run of the program:
  live pulls, cache hits, seconds spent waiting on throttles,
  rate-limited (429) retries, server errors and failures, plus the last
  error text. Misconfigurations (a keyed mode without credentials) are
  called out. Each finished trace also carries a per-source pull
  summary (`data_sources` in the result) shown under the results header
  - the custody log remains the permanent record.
- **Bitcoin backends.** New mode setting: *pool* (default, keyless
  round-robin over mempool.space, blockstream.info and mempool.emzy.de),
  *keyed* (Blockstream Explorer API at enterprise.blockstream.info -
  OAuth client-credentials login at login.blockstream.com, Bearer
  header, tokens refreshed before their 300-second expiry; free tier
  500,000 requests/month; verified 2026-09-04) and *custom* (one
  self-hosted Esplora/mempool instance, no external limit). Missing
  credentials fall back to the pool with a visible note; the token
  request is not custody-logged and the secret never appears in logs.
- **Ethereum: Alchemy backend.** With a free Alchemy key (30M compute
  units/month, 25 requests/s; verified 2026-09-04) the tool uses
  `alchemy_getAssetTransfers`, which returns external, INTERNAL and
  ERC-20 transfers with block timestamps in one 120-CU call - internal
  transactions were previously available only on the keyed Etherscan
  path. Requests are JSON-RPC POSTs; the custody log records a key-free
  descriptor (`alchemy://eth-mainnet/<method>?<params>`) that another
  analyst can replay with their own key. Automatic mode prefers Alchemy,
  then Etherscan, then keyless Blockscout.
- **Ethereum: self-hosted Blockscout.** A Blockscout URL setting points
  the Blockscout mode at an agency-run instance (logged and throttled
  as `blockscout-custom`).
- **Paid-tier guidance** in the panel and Settings: Etherscan Lite/
  Standard/Advanced/Professional ($49/$199/$299/$399 per month),
  CoinGecko Basic/Analyst ($35/$129), Alchemy pay-as-you-go ($0.45 per
  1M CU), Blockstream Enterprise ($3,000/month unlimited), Chainabuse
  partner tier (free for verified agencies). TronGrid's paid plans are
  priced only inside its console and are linked, not quoted.
- **HTTP client** gains POST support with the same memo/cache/throttle/
  retry/custody pipeline, and per-provider counters.
- **PDF methodology corrected** to describe the v0.13.0 clustering
  heuristic (the previous text still said no clustering was applied).

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. The Alchemy backend was verified against Alchemy's documentation and
   with mocked responses; Alchemy's public demo key is rate-limited, so
   no live call was made in this release. The first live use will be by
   an agency with its own key - report any parsing difference.
2. The Blockstream keyed backend's login path was exercised live (the
   token endpoint rejects dummy credentials as expected); data calls
   need a real client ID/secret.
3. Session counters reset when the program restarts.
4. v0.13.0 limitations still apply.

## v0.13.0 — 2026-09-04 (Wallet intelligence: Chainabuse, flag packs, eth-labels, clustering, summaries, bulk triage)

### Functionality
- **Chainabuse scam-report lookups (on demand).** "Check Chainabuse
  reports" on any address panel queries the Chainabuse public API
  (TRM Labs; verified 2026-09-04: `GET /v0/reports?address=`, Basic auth
  with the key as username). Answers are cached for 30 days and the
  monthly call count is tracked, because the free tier is 10 calls a
  month - the tool refuses to overspend and shows the remaining budget.
  A positive answer becomes a `chainabuse` label (category scam_report,
  MEDIUM) so every later trace raises the existing "publicly reported
  scam address" finding. Settings: API key (encrypted) and access tier
  (free / law-enforcement partner: 5,000 calls/hour, private report
  data via `includePrivate`); agencies apply for the partner tier at
  chainabuse.com/partner-contact. Reports are presented as unverified
  third-party claims throughout.
- **Flag packs (agency-to-agency sharing).** Flags dialog: "Export flag
  pack" writes this agency's flags (address, chain, reason, date - no
  case names or numbers) with the agency's name/contact from Settings
  and a SHA-256 over the flag list. "Import a partner's pack" verifies
  the format and hash (a tampered file is rejected) and stores the
  flags as a SEPARATE label source (`pack:<agency>`, category
  shared_flag). They raise a distinct "flagged by a PARTNER agency"
  finding, get a dashed orange ring on the map, a
  "[AGENCY - partner designation]" marker in the PDF address table, and
  are never merged into this agency's own flags. A second import from
  the same agency replaces the first. The hash detects corruption, not
  impersonation (design decision: agencies exchange packs through
  channels they already trust).
- **Ethereum attribution depth: eth-labels import.** New Settings
  download of dawsbot/eth-labels (MIT; Etherscan's public name tags,
  ~113k Ethereum-mainnet rows, ~87k distinct addresses). Custodial
  exchanges (curated slug list) import as MEDIUM-confidence exchange
  labels; Etherscan "Take Action"/"Blocked"/*-exploit tags as
  scam_report labels; everything else (protocols, contracts, funds) as
  LOW-confidence informational labels carried on nodes for context.
  Every label states its provenance ("Etherscan tag via eth-labels").
  Auto-refreshes monthly once downloaded.
- **Bitcoin address clustering (common-input-ownership).** The Bitcoin
  provider records each parsed transaction's inputs (no extra pulls);
  after a trace the engine unions co-spent addresses into clusters,
  EXCLUDING CoinJoin-like transactions (5+ inputs with 3+ equal-value
  outputs) and reporting how many were skipped. Each cluster carries
  its evidencing transaction ids, a MEDIUM confidence (LOW when a
  12+-input co-spend contributed, which is also how services batch),
  and is named as a service's wallet cluster when a member is
  attributed. Presented as an "Address clusters (heuristic)" card, an
  `address_cluster` finding, a cluster marker on nodes, a PDF section,
  and an affidavit paragraph - always as an inference, never a fact.
- **Plain-language wallet summary.** "📝 Plain-language summary" on any
  address panel writes a short paragraph set from the tool's own data:
  attribution with provenance, live activity, this trace's view (what
  came in, what went out, share to labelled exchanges, funds at rest),
  agency/partner/scam designations, cached Chainabuse reports and a
  suggested next step. No AI model is involved; every sentence maps to
  a fact in the response, and the text can be copied.
- **Bulk address triage (⊞ Triage).** Paste up to 500 addresses; each
  is chain-detected and checked against every label source, this
  agency's flags, partner packs, scam lists, cached Chainabuse reports
  and the watch list, optionally with live activity (one throttled
  call per address). Runs execute in the background with progress,
  are saved (`triage_runs`) and exportable as CSV, and rows offer
  Trace / Flag / Watch actions. Interrupted runs are marked failed at
  the next start.

### Dependencies
- Unchanged. eth-labels (MIT) and Chainabuse (API terms) are external
  data sources, downloaded only on request.

### Known limitations / problems detected
1. Chainabuse's `chain` filter enum is not documented publicly, so
   lookups are by address only (an address string is chain-specific
   anyway; EVM addresses may return reports from other EVM chains).
2. eth-labels exchange classification is a curated slug list; a
   custodial exchange missing from it imports as informational only.
   Tether/Bitfinex-affiliated token contracts are tagged under the
   exchange slug by Etherscan and are excluded from exchange
   classification by tag text (Token / Stablecoin / Contract / Proxy),
   which is a heuristic.
3. Clustering is Bitcoin-only and sees only the transactions the trace
   parsed; addresses co-spent in transactions outside the trace are not
   linked. Clusters are inferences with known failure modes (CoinJoin,
   custodial batching) and must be described as such in any affidavit.
4. Triage activity mode is bounded by the same free-API rate limits as
   tracing; 500 addresses with activity can take several minutes.
5. v0.12.0 limitations still apply.

## v0.12.0 — 2026-09-04 (Web UI modernisation)

### Functionality
- **Visual redesign of the browser UI** on the same guided workflow the
  investigator already knows (numbered steps, results on the right).
  A token-based design system (`style.css`): card layout, consistent
  buttons/inputs/dialogs/tables, brand mark in the header, chips and
  status badges, spinner while a trace runs, focus rings for keyboard
  use, and a responsive single-column layout below ~960 px.
- **Dark theme.** Follows the Windows/OS preference by default; a header
  toggle (☾/☀) overrides it and the choice is remembered on that
  machine. The money-flow map re-colours its labels, edges and borders
  to match the theme (role colours are unchanged so the legend, PDF
  and map stay consistent).
- **Trace history.** Traces could previously only be viewed in the
  session that ran them. New endpoint `GET /api/cases/{id}/traces`
  (newest first, with exits/findings/address counts) drives a
  "Recent traces in this case" list on the start screen, a 🕘 Trace
  history dialog in Step 1, and `#trace=<id>` in the URL so a browser
  reload (or a bookmark) reopens the same trace. A running trace can be
  re-attached to the same way.
- **Results header** shows the trace number, starting wallet, chain,
  direction, pattern/extended, focus-transaction flag, finish time and
  exit-point count at a glance.
- **Findings list starts collapsed** at the top 8 (extended traces can
  produce dozens), with "Show all N findings"; exit points and the map
  are no longer pushed off-screen. Exports are unaffected.
- **Getting-started checklist** on the start screen: OFAC list, exchange
  label packs, scam blacklist and agency letterhead, each with a link
  into Settings until done (`/api/meta` now reports letterhead and AI
  configuration state).
- Exports grouped in their own card; header nav de-emphasised (ghost
  buttons) with a compact label-inventory summary (full counts in the
  tooltip).

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. The theme toggle is per browser profile (localStorage), not per
   agency setting.
2. Very large maps (hundreds of addresses) still use the simple preset
   layout and can render sparse until "Recenter" or "Tighten" is used;
   layout algorithms are unchanged in this release.
3. v0.11.0 limitations still apply.

## v0.11.0 — 2026-09-04 (Console-free launcher, Windows installer, per-user data folder)

First release built for handing to OTHER agencies.

### Functionality
- **No console window.** The program now starts through a desktop
  launcher (`app/launcher.py`): the server runs in the background, the
  browser opens when it is ready, and an icon in the notification area
  (system tray) is the program's presence and off switch ("Open Crypto
  Investigator" / "Quit"). A **Quit** button in the UI header does the
  same. From source, `run.pyw` runs under pythonw (no window);
  `run.py` keeps a console for developers. Log output goes to a
  rotating file in the data folder (`logs/crypto_investigator.log`)
  since there is no console to read.
- **Single instance.** Starting the program while it is already running
  just reopens the browser tab. If port 8321 is held by something else,
  a message box says so instead of failing silently.
- **Windows installer + portable zip.** `tools/build_release.py`
  freezes the program with PyInstaller (windowed executable, no Python
  install needed on the target machine), zips a portable copy, and
  builds `CryptoInvestigator-Setup-v<version>.exe` with Inno Setup when
  it is available. The installer needs no administrator rights
  (per-user install; all-users offered when elevated), creates Start
  menu / desktop shortcuts, and detects a running copy before
  upgrading. Uninstall never touches case data.
- **Per-user data folder.** The installed build keeps the database,
  reports, logs and the three agency-editable label files under
  `%LOCALAPPDATA%\CryptoInvestigator\data` (a source checkout keeps
  using its own `data/`). The editable label files are copied there
  from the bundled defaults on first run and never overwritten by
  upgrades. The location is shown in Settings and can be redirected
  (e.g. to an encrypted volume) via a `data-location.txt` file next
  to the program or the `CRYPTO_INVESTIGATOR_DATA` environment
  variable.
- **Interrupted traces are marked failed at startup.** A trace left
  "running" by a shutdown (Quit, crash, power loss) previously showed
  as running forever; the server now marks such rows failed with an
  explanatory error at the next start.
- **Program listing tool discovers files** instead of using a hand-kept
  list. The v0.8.0–v0.10.1 listings omitted `providers/tron.py`,
  `assistant.py`, `scheduler.py`, `secretstore.py`,
  `report/casefiles.py`, `report/freeze_letter.py`,
  `custodian_contacts.json` and the research notes because the list
  was never updated; from this version a new file cannot be left out
  of the record.
- Housekeeping: DPAPI secret-store notices go to the log instead of
  stderr; `.gitignore` excludes the database, reports and logs so the
  public repository can never carry case data.

### Dependencies
- `pystray` (tray icon) and `pillow` (icon image; already used by
  `tools/make_icon.py` but previously unlisted) added to
  `requirements.txt`. `pyinstaller` in the new `requirements-build.txt`
  (build machines only). Inno Setup 6 (external, free) for the
  setup.exe.

### Known limitations / problems detected
1. The tray icon and DPAPI key encryption are Windows features; from
   source on macOS/Linux the launcher falls back to running in the
   foreground and keys are stored in plaintext (logged).
2. The Quit button stops the program for everyone using that machine's
   copy - which is only ever one investigator, since the server is
   localhost-only.
3. The installed build is unsigned; Windows SmartScreen will show an
   "unknown publisher" warning on first run until a code-signing
   certificate is used (an agency decision).
4. v0.10.1 limitations still apply.

## v0.10.1 — 2026-09-01 (Fix: Anthropic identity-linked keys need a workspace ID)

### Functionality
- **Fix (user-reported):** Anthropic personal/service-account API keys
  that are not scoped to a single workspace ("identity-linked" keys)
  require an `anthropic-workspace-id` header on every request; the AI
  assistant did not send one, so such keys failed with a raw API error.
  Settings now has an **Anthropic workspace ID** field (shown when the
  provider is Anthropic; found in the Claude Console under Settings →
  Workspaces, ID column, `wrkspc_...`), sent as the header when set.
  Keys created scoped to one workspace continue to need nothing.
- When the API still reports the missing-workspace error, the assistant
  now explains both fixes (paste the workspace ID, or create a
  workspace-scoped key) instead of surfacing the raw message.
  Verified against the documented API behaviour (header name, error
  shape, and where the ID is found confirmed from the current
  Anthropic authentication docs on 2026-09-01).

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. The workspace ID is stored as a plain setting (it is an identifier,
   not a secret).
2. v0.10.0 limitations still apply.

## v0.10.0 — 2026-09-01 (Optional AI assistant — any provider, glass-box, off by default)

### Functionality
- **AI assistant (optional; OFF by default).** The investigator may
  configure ONE provider in Settings: Anthropic (Claude), OpenAI
  (ChatGPT), or any OpenAI-compatible endpoint — which includes LOCAL
  models (Ollama, LM Studio), keeping case data on the machine for
  agencies whose policy prohibits cloud AI. The key is DPAPI-encrypted
  like every other key; a local endpoint may need no key at all.
- **What it does:** "Explain this trace in plain language" (a
  case-notes-ready narrative of where the money went, the strongest
  findings, unknowns, and next steps), free-form Q&A about the current
  trace, and an IC3 incident-description draft (shown as a suggestion
  with [BRACKETED] gaps for facts only the victim can supply — never
  auto-inserted).
- **Glass-box rules, enforced in code:** nothing is sent anywhere until
  the investigator clicks an AI action; every request and response is
  recorded IN FULL in a new AI activity log (browsable in-app: exact
  prompt, exact response, provider, model, duration); the system prompt
  binds the model to the supplied trace data, requires transaction/
  address citations, and forbids invented attribution or continuity;
  AI output is banner-marked "assistance, not evidence" and is never
  written into court reports, worksheets, or letters automatically.
  Trace context sent to the model is capped largest-movements-first
  with an explicit truncation note. Model refusals (Claude safety
  declines) surface their explanation instead of failing silently.
- **Reliability fix (found during testing): hung DNS can no longer
  freeze a trace.** Python's DNS resolution has no timeout; a wedged OS
  resolver was observed blocking a trace indefinitely at hop 0 inside
  the HTTP client. Every provider fetch now runs under a hard
  wall-clock deadline; a hung lookup becomes an ordinary timeout that
  the retry/round-robin logic handles, and the trace completes with an
  honest fetch-failure warning (verified live under a broken resolver:
  previously infinite, now ~3.5 minutes to an honest result).

### Dependencies
- Unchanged. AI calls use httpx directly (provider-neutral raw HTTP; no
  vendor SDK added). AI calls do NOT go through the evidence cache or
  the chain-of-custody log — they are not data acquisitions; they have
  their own dedicated audit log.

### Known limitations / problems detected
1. AI output quality depends entirely on the configured model; the tool
   constrains and logs, but cannot verify, what a model writes — the
   banner and system prompt say so, and the investigator must verify
   every claim against the trace data.
2. Trace context is truncated for very large traces (note included in
   the prompt); disposition totals always cover everything.
3. Default model names (claude-opus-5 / gpt-5) will age; the model
   field is free-text with a "check your provider's docs" hint.
4. A dead API host still costs the full per-request retry budget before
   the round-robin moves on (bounded minutes, not hangs).
5. v0.9.1 limitations still apply.

## v0.9.1 — 2026-08-31 (Zero accounts required: keyless API stack)

### Functionality
- **No accounts required, on any chain.** Built for handing the tool to
  other agencies: every data source now has a verified free KEYLESS
  path, with API keys demoted to optional rate-limit upgrades. Based on
  a live-verified survey of keyless public APIs
  (docs/research/2026-08-31-keyless-apis.md).
- **Ethereum without an Etherscan key:** new selectable backends —
  Blockscout's public instance (keyless, ~300 req/min: Etherscan-format
  account lists + REST v2 for contract checks/activity/transactions,
  which also supplies the focus-transaction timestamps Etherscan's
  proxy endpoint lacks) and Routescan's keyless Etherscan-compatible
  endpoint (2 req/s, the drop-in backup). Mode setting: auto (default —
  Etherscan when a key is set, Blockscout otherwise) / blockscout /
  routescan / etherscan. Custody logs record which source served every
  pull.
- **USD pricing without a CoinGecko key:** keyless fallback chain —
  CoinGecko batched range → Kraken daily OHLC (BTC/ETH/TRX, ~2 years
  back) → Coinbase Exchange candles (BTC to 2015, ETH to 2016) →
  per-date CoinGecko. Spot ticker falls back to Kraken public data.
  Daily OPEN approximates the 00:00 UTC price on the fallback sources;
  the valuation note and custody log state the source per acquisition.
- **Bitcoin resilience:** third community Esplora host
  (mempool.emzy.de, verified) joins the round-robin with a polite
  per-host throttle.
- **Correctness fix — EIP-7702 (Pectra):** delegated personal wallets
  carry code beginning 0xef0100 and were classified as "smart contract —
  not followed" by the eth_getCode check. All Ethereum backends now
  treat 7702-delegated EOAs as ordinary wallets and keep tracing
  (verified against a live delegated wallet).
- **Fix:** Blockscout's "No token transfers found" empty-result phrasing
  is now recognised (previously failed the whole movements fetch).
- **Throttle updates from verified documentation:** TronGrid anonymous
  tier is 1 req/s (throttle adjusted; a configured key now uses a
  15-req/s bucket), Kraken ~1 req/s, Coinbase 10 req/s, Routescan
  2 req/s, Blockscout 300 req/min.

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. Keyless tiers are shared-IP pools: an agency network with heavy
   crypto-API use can hit limits sooner; the optional keys remain the
   fix. Blockchair and TronScan keyless tiers were evaluated and
   REJECTED (personal-use-only terms / officially deprecated keyless).
2. TRX older than Kraken's ~2-year OHLC window has no keyless USD
   source (CoinGecko keyless blocks >365-day history; CryptoCompare and
   CoinCap keyless are dead) — such movements stay honestly unvalued.
3. Blockscout has announced eventual deprecation of per-instance API
   keys/limits in favour of a multichain service; Routescan and
   Etherscan modes are the hedge.
4. Etherscan's richer data (internal transactions) is still only on the
   keyed path; keyless tracing covers native + token transfers.
5. v0.9.0 limitations still apply.

## v0.9.0 — 2026-08-31 (Watchlists, Tron, backward tracing, issuer freezes, evidence packages)

### Functionality
- **Wallet watchlists with movement alerts.** Watch any wallet (👁 header
  dialog, any address panel, or one click on a FUNDS AT REST finding). A
  background scheduler re-checks each watch on an interval (Settings;
  default 30 min), comparing transaction count and balance, and raises an
  alert — header badge, alert card with "re-trace from here" — when
  anything changes. Honest limitation stated in the UI: an Ethereum watch
  tracks nonce + ETH balance, so an incoming token-only transfer may not
  trigger until something else moves. Completes the funds-at-rest →
  freeze-letter loop: know the moment the money moves.
- **Tron / TRC-20 USDT tracing** (the dominant pig-butchering rail) via
  the official TronGrid API. Works keyless at a low rate; a free TronGrid
  key (Settings) raises the limit. Covers native TRX and TRC-20 transfers
  (USDT prioritised); OFAC Tron addresses and Tron label-pack entries
  already in the label store now participate. Limitations (stated in-app):
  focus transactions not yet supported on Tron; the high-activity check is
  based on the newest page of transactions.
- **Backward tracing (source of funds).** New trace direction: "where did
  this wallet's money COME FROM?" — for seized/suspect wallets and
  forfeiture work. Walks funding sources hop by hop with a mirrored
  time-consistency rule (an address's funding movements dated AFTER it
  sent the traced funds onward are ignored), identifies named exchanges
  the funds ORIGINATED from (records targets for the sending account),
  and re-words findings, report and traceroute for the direction. Value
  accounting is backward taint-by-touch; on Bitcoin each movement carries
  the input's full contribution to the funding transaction (stated in the
  methodology). Graph arrows always point the way the money moved.
- **Stablecoin ISSUER freeze route.** Tether and Circle can freeze
  USDT/USDC at ANY address — including self-custodied wallets no exchange
  controls. The freeze-request generator now routes automatically: wallet
  attributed to an exchange → custodian letter; unattributed wallet
  holding traced stablecoins → token-issuer blacklisting request (with an
  investigator note explaining the route); `mode=issuer` forces the
  issuer letter even for funds at an exchange. Tether/Circle added to the
  custodian directory alongside crypto-ATM/kiosk operators (Bitcoin
  Depot, CoinFlip, Athena — victim kiosk receipts identify the operator).
- **Evidence package (one-click disclosure ZIP):** report PDF, complete
  custody CSV, raw JSON, DRAFT affidavit, per-exit traceroutes and DRAFT
  freeze letters, plus a MANIFEST listing the SHA-256 of every packaged
  file.
- **Affidavit methodology helper:** DRAFT "how this tracing was
  performed" paragraphs (data sources, method, scope, attribution/
  confidence framework, records integrity) generated per trace for
  counsel to adapt.
- **Money-flow map image export:** print-resolution PNG of the map for
  warrant/courtroom exhibits.
- **Case overlay map:** every finished trace of a case merged onto one
  display-only map; wallets that received funds traced from DIFFERENT
  victims get an orange double ring and a convergence banner — shared
  scammer infrastructure at a glance.
- **📇 Custodian legal-process directory in-app:** searchable view of the
  (agency-editable) directory — portals, emails, guidelines, process
  notes, per-entry confidence — for use when serving anything.
- **Per-address investigator notes:** the annotations table finally has a
  UI (address panel); notes are case-scoped and appear in the PDF address
  table, marked as the investigator's own annotation.
- **Case export / import:** one JSON file carrying the case, its traces,
  custody log, IC3 draft, notes and linked flags/watches — for backup or
  transfer between machines; import re-creates it under new ids.
- **Label auto-refresh:** downloaded label lists refresh themselves once
  stale (OFAC/scam list weekly, TagPacks monthly; toggle in Settings);
  Settings now shows each list's last-updated date. Sources never
  downloaded are never auto-fetched.
- **API keys encrypted at rest** (Etherscan/CoinGecko/TronGrid) with
  Windows DPAPI, user-scoped; nothing key-like appears in custody-logged
  URLs (all keys travel as headers or are stripped).

### Dependencies
- Unchanged (pyyaml since v0.8.0). TronGrid requires no key (a free one
  is recommended).

### Known limitations / problems detected
1. Watch checks are snapshots on an interval, not real-time; Ethereum
   token-only inflows may not trigger (documented in the dialog).
2. Backward Bitcoin value accounting is poison-style per input, not an
   apportioned share (stated in the methodology and affidavit text).
3. Tron: no focus transactions; page-based activity estimate; an INVALID
   TronGrid key causes HTTP 401 on every request (the error says so).
4. Issuer freezes are discretionary and short-lived without legal
   process; the letter and directory notes say so.
5. DPAPI-encrypted keys cannot be read under a different Windows account/
   profile; they read back empty and must be re-entered (documented in
   Settings).
6. Case overlay merges traces of the majority chain only and is
   display-only (per-trace downloads are hidden in overlay view).
7. Housekeeping note: the archived v0.8.0 program listing was
   accidentally overwritten during the v0.9.0 build (both released
   2026-08-31, hours apart; no case work occurred on v0.8.0). The v0.8.0
   validation PDF therefore references a listing file that no longer
   exists; v0.9.0's listing and validation documents are complete and
   current.
8. v0.8 limitations still apply.

## v0.8.0 — 2026-08-31 (Faster traces, wallet flags, freeze requests, big label import)

### Functionality
- **Speed: concurrent prefetching.** A pool of background workers
  (`PREFETCH_WORKERS`, default 3) now warms the data for queued addresses
  while the engine processes the current one. A per-trace single-flight
  fetch memo guarantees each distinct pull happens AT MOST once per trace
  no matter which thread asks first, so rate limits are still respected
  (the per-provider throttle is shared), custody entries are never
  duplicated, and graph construction stays deterministic — the engine's
  own sequential pass over the (now-memoised) data remains authoritative.
  Prefetching overlaps network latency with processing; with the dual
  Esplora hosts it uses both hosts simultaneously. Side effect
  (documented): within one trace, repeated reads of the same URL now
  produce ONE custody entry instead of several, and every consumer sees
  one consistent snapshot of mutable data for the trace's duration.
- **Speed/meaningfulness: value-ordered expansion.** The frontier is now
  a max-heap: the address that received the largest traced movement
  (approximate USD at current spot prices, raw amount when unpriced) is
  examined first, replacing first-in-first-out order. When a trace hits
  its size caps, the big money is what got followed. Ordering never
  changes which movements an examined address follows (search pattern /
  dust rules unchanged); the methodology text states this.
- **Speed: batched USD valuation.** Historical prices now come from ONE
  CoinGecko market_chart/range call per asset instead of one throttled
  call per (asset, date) — minutes saved on traces spanning many dates.
  Per-date calls remain as fallback for dates the range cannot supply
  (e.g. beyond the free tier's 365-day history). Optional free CoinGecko
  "demo" API key setting (sent as a header, never in custody-logged
  URLs) for much more reliable pricing.
- **Wallet flags (agency fraud designations).** Flag any wallet from the
  map's address panel or the new 🚩 Flagged wallets dialog (reason +
  originating case). Flags are merged into the label system, so every
  future trace in ANY case automatically raises "Traced funds reached a
  wallet FLAGGED by this agency" as a priority-2 finding with the reason
  and cross-case linkage; flagged wallets get a red ring on the map and
  an [AGENCY-FLAGGED] marker in the PDF address table. Flags are
  presented everywhere as the agency's OWN designation, never as
  external attribution. Flagged wallets are still expanded — following
  their money out is the point.
- **Freeze / preservation request generator.** Any wallet that received
  traced funds (exit-point cards, address panels, exchange-entity
  member lists) gets "Draft freeze/seizure request (PDF)": a
  DRAFT-watermarked preservation + asset-hold + non-disclosure letter
  citing 18 U.S.C. § 2703(f) "to the extent applicable, in the
  alternative as voluntary cooperation" (counsel-review flag in the
  text), with the funding transactions as Attachment A (usable as a
  seizure-warrant exhibit). Agency letterhead comes from new Settings
  fields; custodian service channels come from a new agency-editable
  directory (`data/labels/custodian_contacts.json`) compiled 2026-08-31
  from published law-enforcement guidance with per-entry confidence and
  stale-risk warnings (including one known phishing-clone contact to
  avoid). A wallet not attributed to a custodian gets the letter with an
  honest DO-NOT-SERVE warning explaining that self-custodied wallets
  cannot be frozen by third parties.
- **Label imports (attribution at scale).** Two new one-click Settings
  downloads: GraphSense TagPacks (MIT community exchange/mixer
  attribution — tens of thousands of addresses, MEDIUM confidence, pack
  provenance and last-modified date carried on every label) and the
  ScamSniffer drainer/phishing blacklist (Ethereum; imported as
  'scam_report' labels that raise a corroborating finding, never a
  stopping point and never presented as proof).
- **Rate-limit correction:** Etherscan free tier dropped to 3 req/s in
  2026; the throttle now stays under that (0.35s interval).

### Dependencies
- `pyyaml` added (parses GraphSense TagPack files).

### Known limitations / problems detected
1. Value-ordered expansion uses CURRENT spot prices for ordering only;
   reports still value movements at their own transaction dates. With no
   price source, ordering degrades to raw amounts (cross-asset order is
   then approximate).
2. The freeze-request letter is a DRAFT template: § 2703(f)'s
   applicability to exchanges is unsettled, custodian channels change
   (each letter carries the directory's verify-before-service note), and
   voluntary freezes are short and discretionary (e.g. ~2 weeks at
   Binance.US, 30-day auto-lift at KuCoin) — durable restraint requires
   a court order or seizure warrant.
3. TagPack attribution is community-maintained and partly dated
   (some packs last modified 2019–2022; shown per label). Absence of a
   label is still not absence of an exchange.
4. ScamSniffer data is EVM-only and 7-day delayed on the public feed.
5. GitHub's unauthenticated API limit (60 req/hour) can temporarily
   block repeated TagPack refreshes; wait an hour and retry.
6. v0.7 limitations still apply.

## v0.7.0 — 2026-07-10 (Trace until resolution: investigative findings)

### Functionality
- **Why:** even 25-hop traces rarely hit a *labelled* exchange (the label
  list is small; exchange deposit addresses are per-customer and never
  listed), so "no exit found" was a common but misleading result. The
  goal is redefined: every branch is followed until it ends in a
  **classified outcome**, and the outcomes ARE the result.
- **Investigative findings (new headline output):** ranked cards in the
  UI and an "Investigative Summary" section in the PDF, each with the
  facts, the basis, and a recommended next step:
  - *Named exchange reached* (serve process; includes compliance badge)
  - *FUNDS AT REST* — traced funds sitting unspent at an address as of
    trace time: freeze/seizure/monitoring candidate (inflow-only wallets
    are promoted, not dropped: that is where the money IS)
  - *Probable exchange deposit* — sweep-pattern heuristic (address
    forwarded ≥90% of its traced inflow within 72h into a high-activity
    wallet); labelled a heuristic, with identification steps
  - *Unidentified busy service* — elevated with explorer/Chainabuse
    lookups instead of being a dead end (large hot wallets are usually
    publicly tagged; one identification = a warrant target)
  - *Consolidation point* — ≥2 traced branches merging: collection
    wallet, multi-victim linkage candidate
  - *OFAC-sanctioned / mixer contact* — charging and referral notes
  - *Unresolved trail edges* — largest-value re-trace candidates
- **Disposition of funds:** a table accounting for traced value at every
  terminal bucket (at rest / services / exchanges / sanctioned / mixers /
  unresolved / not-examined dust+skipped), with USD shares when priced.
  Converts "no exchange found" into a complete answer.
- **Extended mode generalised:** works with or without a transaction
  hash; follows until every branch resolves (up to 25 hops) instead of
  stopping at the first exchange. Trivial dead ends stay out of the
  narrative; everything remains in the JSON/transaction table.
- Engine now records per-address activity counts and (Bitcoin) balances
  for the findings layer, and counts dust-skipped movements so the
  disposition can account for them.

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. "Funds at rest" reflects the trace snapshot; funds can move any time
   (a watch-list with alerts is the planned companion feature).
2. The sweep/deposit pattern is a HEURISTIC and is labelled as such
   everywhere; it must be verified before being sworn to.
3. Ethereum at-rest determinations rely on observed movements (no
   balance check); Bitcoin uses the fetched balance where available.
4. Findings are capped at 10 per type in reports (full list in JSON).
5. v0.6 limitations still apply.

## v0.6.0 — 2026-07-09 (Search patterns, dual Bitcoin providers)

### Functionality
- **Selectable search patterns (new Step 4):** exhaustive scanning of
  every account/transaction was the only mode and was slow; the
  branch-selection policy is now the investigator's choice:
  - **Rapid — follow the bulk:** per address/asset, only the largest
    movements covering ≈80% of the outgoing value (max 3 branches).
    Fastest route to a freeze/subpoena target.
  - **Balanced (default):** movements carrying ≥5% of the address's
    outgoing value (max 8 branches).
  - **Thorough — court-complete:** every movement above dust (the old
    behaviour). Intended for the final report.
- **Honest accounting of skipped branches:** selective search is never
  presented as complete search. Every branch a pattern skips is counted
  with per-asset value totals in a written warning, itemised in the JSON
  export (`unexamined_branches`), flagged on the affected addresses, and
  the PDF names the pattern in the case summary and describes its exact
  rules in the methodology. Focus-payment seeding is never pruned.
- **Dual Bitcoin providers:** requests round-robin between mempool.space
  and blockstream.info (identical Esplora API), roughly doubling Bitcoin
  throughput; each host keeps its own rate-limit budget. The custody log
  records the exact host/URL of every pull; immutable records share one
  evidence-cache entry regardless of host (cache keys are now
  host-independent `esplora:{path}`). Setting a custom Bitcoin endpoint
  disables alternation.

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. Rapid/Balanced can miss deliberately split funds (structuring across
   many small branches); the skipped-branch warning totals what was left
   unexamined so the investigator can judge, and Thorough remains the
   final-report mode.
2. Pattern selection ranks branches per asset by raw amount, not USD, so
   cross-asset comparisons at one address are approximate.
3. Bitcoin responses cached before v0.6.0 (keyed by full URL) remain in
   the evidence cache but are not found under the new host-independent
   keys; immutable data refetched once re-caches under the new key.
4. v0.5 limitations still apply.

## v0.5.0 — 2026-07-09 (Exchange compliance designations)

### Functionality
- **Compliant / non-compliant exchange designations:** identified
  exchanges are checked against an agency-editable list
  (`data/labels/exchange_compliance.json`). Initial designations per the
  investigating agency: COMPLIANT — Kraken, Bithoven (bithoven.com,
  St. Vincent and the Grenadines), Gemini, Poloniex, Coinbase, Bittrex,
  Uniswap, Binance; NON-COMPLIANT — Bybit, Bitfinex, BitMEX, FTX
  (defunct; bankruptcy estate). Shown as a badge on exit cards with
  strategy guidance (compliant → expect normal legal-process response;
  non-compliant → consider MLAT/alternatives before funds move), in the
  PDF report's exit sections, and in the traceroute text.
- **Honest sourcing (design rule):** FATF evaluates jurisdictions, not
  individual exchanges, so every surface presents the designation as
  the agency's own, made "with reference to FATF guidance," never as an
  official FATF publication. The list is local and editable; matching is
  by entity name, case-insensitive.

- **Desktop launcher:** `tools/make_icon.py` generates the application
  icon (a Bitcoin coin under a magnifying glass, drawn from Pillow
  primitives — reproducible from source) into `assets/`, and a desktop
  shortcut launches `run.py` with that icon. Closing the console window
  stops the program.

### Dependencies
- Pillow added as a development-time dependency (icon generation only;
  the application itself does not import it).

### Known limitations / problems detected
1. Designations match on entity name; an exchange labelled under a
   different name (e.g. a subsidiary brand) will not match until added
   to the JSON file.
2. v0.4 limitations still apply.

## v0.4.0 — 2026-07-09 (Extended tracing, traceroute, time-forward rule)

### Functionality
- **Extended mode (follow until an exchange):** when a focus transaction
  sets the direction, a new Step 4 option keeps following the payment
  until it reaches a labelled exchange (up to 25 hops; address/edge caps
  still apply). The trace stops the moment an exchange is identified and
  reports any unexplored branches; if no exchange is found within the
  limits, that is reported honestly (absence of a label is not absence of
  an exchange).
- **Warrant-ready traceroute:** every exit point now carries a computed
  hop-by-hop path from the victim wallet to the exchange deposit address
  (shortest path; ties favour larger movements). Shown as a table in the
  PDF report and available on each exit card as **Copy traceroute** /
  **Download (.txt)** — plain text with addresses, amounts, USD
  estimates, transaction hashes and UTC times, formatted for pasting
  into a search warrant, plus a note separating blockchain facts from
  attribution inferences.
- **Time-forward rule (fix):** every address is now examined only from
  the moment the traced funds arrived at it — movements dated earlier
  are ignored and counted in a warning. Previously, expanding a suspect
  wallet included its recent history from before the victim's payment,
  polluting the map with unrelated transactions.
- **Focus-transaction hop semantics (fix):** the recipient of the focus
  payment is now the first suspect wallet and hops are counted from it
  (the focus payment is the approach, not one of the user's hops). The
  report's methodology states this.
- **Ethereum focus-transaction timestamps:** missing proxy-endpoint
  timestamps are backfilled from sibling token transfers in the same
  transaction, anchoring the time-forward rule (partially resolves v0.1
  known limitation #7).

### Dependencies
- Unchanged.

### Known limitations / problems detected
1. The time-forward filter applies to the transactions the providers
   return; the per-address "most recent transactions" caps still apply
   before filtering, so a hyper-active address can still hide an old
   spend beyond the cap (date-windowed fetching remains roadmapped).
2. An Ethereum focus transaction that moves only ETH (no token transfers)
   still has no timestamp at hop 1; the time-forward anchor then starts
   at the first timestamped hop.
3. Extended mode stops at the FIRST labelled exchange; funds that split
   toward multiple exchanges need a follow-up fixed-depth trace of the
   remaining branches (the stop warning says exactly this).
4. v0.3 limitations still apply.

## v0.3.0 — 2026-07-09 (USD context, readable flow map)

### Functionality
- **Live price ticker:** header shows current BTC and ETH prices in USD
  (CoinGecko free API, refreshed every minute, cached server-side). The
  dust-threshold inputs now show their live dollar equivalent ("0.0001 BTC
  ≈ $X") so "ignore small amounts" is an informed choice.
- **Historical USD valuation:** every traced movement is valued at the
  daily market price of its own transaction date (CoinGecko /history,
  custody-logged and cached like every other acquisition; stablecoins at
  $1.00 by peg; other tokens honestly left unvalued). Shown in the edge
  details, the exit cards/report, the transaction table, and used to
  prefill the IC3 worksheet's USD amounts. Marked everywhere as an
  approximation, not a market appraisal. A price outage degrades to
  crypto-only amounts with a warning — it never fails a trace.
- **Mixer role:** addresses labelled as mixing services get their own role,
  colour and plain-language explanation ("trail obscured; no continuity is
  guessed"), and are never expanded.
- **Money-flow map redesign (left-to-right):** money now flows from the
  victim on the left toward exits on the right (layered layout, rows
  ordered to reduce crossing lines) — reads like a timeline, like the
  commercial tools.
- **Map simplification (display-only; evidence is never dropped):**
  - *Pass-through chains:* runs of one-in/one-out intermediary addresses
    (classic layering/peel pattern) collapse into a single dashed edge;
    clicking it lists every hop with amounts, dates and hashes.
  - *Exchange entities:* multiple deposit addresses of the same exchange
    group into one entity node ("Binance — 3 deposit addresses").
  - *Minor branches:* sprays of small dead-end movements fold into a
    "+N minor branches" stub (3 largest stay visible).
  - Each simplification has a toggle, and **Expand all** shows every
    address individually. Collapsed data remains complete in the PDF,
    CSV/JSON exports, and the click-through panels.
- **Map controls:** Recenter (also double-click empty space), zoom +/−,
  and Spread out / Tighten buttons that re-space the layout so edges stay
  easy to click on dense graphs.
- **Edge details table:** clicking any flow line now shows every
  constituent transaction — date/time, amount, approximate USD at the
  date, full hash — chronologically, with a copy-as-table button for
  pasting into warrants/spreadsheets.

- **Validation documentation (added post-release, same version):**
  `tools/make_docs.py` generates two PDFs into `docs/validation/` —
  a **Technical Validation Document** (architecture, data sources,
  acquisition integrity, the full tracing algorithm with its exact
  constants, attribution/confidence model, USD methodology, known
  limitations, and step-by-step verification procedures for outside
  experts, anchored to the SHA-256 of the version's complete source
  listing) and a two-page **Overview / Quick Start**. Constants are
  pulled live from `app/config.py` so the documents cannot drift from
  the code.

### Dependencies
- Unchanged (fastapi, uvicorn, httpx, reportlab). CoinGecko is keyless.

### Known limitations / problems detected
1. USD values use CoinGecko's DAILY price for the transaction's UTC date —
   intraday moves are not captured; figures are context, not appraisals.
2. Tokens other than USDT/USDC (and BTC/ETH) are not valued in USD.
3. Historical price fetches add a few seconds per distinct (asset, date)
   pair on first run (3s rate limit; cached permanently afterwards).
4. Minor-branch folding ranks mixed-asset branches by USD when available,
   else by raw amount — cross-asset ranking without USD is approximate.
5. v0.2 limitations still apply.

## v0.2.0 — 2026-07-09 (Victim-first workflow, IC3 helper)

### Functionality
- **Victim-first starting point (workflow redesign):** the single
  "address or txid" input is now two fields. Step 2 takes the victim's
  WALLET ADDRESS (always the anchor of the trace); Step 3 optionally takes
  a TRANSACTION HASH that sets the direction — when supplied, only that
  payment's outputs leaving the victim wallet are followed, because not
  every transaction on a victim's wallet needs investigating. Change
  returning to the victim wallet is excluded. The engine verifies the
  wallet actually participates in the transaction: senders trace normally;
  an incoming payment falls back to a full-wallet trace with an honest
  warning; an unrelated transaction fails with a clear message.
- **IC3 complaint helper:** new header button opens a per-case worksheet
  that mirrors the seven sections of the official FBI complaint form
  (complaint.ic3.gov, which is manual-only — no submission API exists).
  Crypto payments can be PREFILLED from the case's latest finished trace
  (tx hash, date, crypto type/amount, originating + recipient wallets,
  receiving exchange when labelled). Narrative fields enforce the form's
  real character limits (3,500 description / 5,000 technical / 1,000
  witnesses / 1,000 other-agency). Includes IC3's own guidance: no
  SSN/DOB, complaints are immutable once filed, recovery-service scam
  warning, elder-fraud hotline. Exports a printable worksheet PDF in the
  form's exact section order. Draft saved per case in the local database.
- **External enrichment links:** clicking any address in the flow map now
  offers one-click lookups on a public block explorer and on Chainabuse
  (TRM Labs' public scam-report database).
- **Report updates:** the trace PDF now shows the victim wallet and the
  focus transaction (or states that all outgoing movements were followed),
  and the methodology section documents the direction restriction.
- **Competitive feature roadmap:** `docs/feature-roadmap.md` — features
  extracted from Chainalysis, Elliptic, MetaSleuth, Chainabuse and Crystal
  Intelligence, ranked by feasibility with free/public data; proprietary-
  data features (mass attribution, demixing, risk scores) explicitly
  declared out of scope with the honest alternative stated.

### Dependencies
- Unchanged (fastapi, uvicorn, httpx, reportlab).

### Known limitations / problems detected
1. An Ethereum focus-transaction trace can show no timestamp on its first
   edge (Etherscan proxy endpoint returns none) — carried over from v0.1.
2. The IC3 worksheet asks for USD amounts (as the form does) but the tool
   does not convert crypto to historical USD; the investigator supplies
   the value from receipts or exchange records.
3. IC3 prefill uses depth-1 outgoing movements of the LATEST finished
   trace only; run the definitive trace before prefilling.
4. For a multi-input Bitcoin focus transaction, outputs are attributed to
   the victim's payment as a whole (standard single-tx tracing practice);
   the trace warns when this happens.
5. v0.1 known limitations 1–6, 8–9 still apply (newest-first caps, no
   clustering, contracts are dead ends, small seed label list, unencrypted
   key storage, etc.).

## v0.1.0 — 2026-07-09 (Phase 1 MVP)

First release. Baseline functionality (no previous version to diff against).

### Functionality
- **Chains:** Bitcoin (via mempool.space/Esplora, keyless) and Ethereum +
  ERC-20 with USDT/USDC prioritised (via Etherscan V2, free key required).
  Tron, Monero and Litecoin inputs are *recognised* and explained honestly
  (Tron: next version; Monero: cannot be traced; Litecoin: later phase).
- **Input recognition:** paste an address or txid; format auto-detected with
  plain-language guidance and user-confirmable chain selection.
- **Forward tracing:** breadth-first "follow the money" with configurable
  depth (1–6 hops) and dust thresholds. Value accounting is taint-by-touch
  (poison); the report states this. Safety caps prevent graph explosion.
- **Honest stopping points:** OFAC-sanctioned addresses (flagged, official,
  high confidence), labelled exchanges (recorded as exit points), smart
  contracts (flagged, not followed), high-activity probable services
  (heuristic, low confidence, not expanded), depth/size limits (marked as
  "trail edge").
- **Exit detection:** traced funds landing on a labelled exchange address are
  ranked by value and hop-proximity, with funding txids/timestamps/amounts.
- **Attribution sources:** official OFAC SDN list (downloaded and parsed
  live from Treasury, 920 addresses at time of testing → HIGH confidence) +
  a bundled seed list of 34 publicly documented exchange wallets
  (community/explorer-tag derived → MEDIUM confidence, marked unverified).
- **Chain of custody:** every HTTP pull logged with source, exact re-runnable
  URL, UTC timestamp, HTTP status, SHA-256 of the raw body, and cache
  provenance. Exportable as CSV.
- **Court-ready PDF report:** case summary, ranked exit points with funding
  transactions, DRAFT records-request language per custodian, address table
  (role + basis), transaction table, methodology & limitations appendix,
  chain-of-custody appendix.
- **Case management (basic):** named cases with case numbers; traces saved
  per case; full trace JSON export.
- **UI:** guided 3-step plain-language workflow; interactive Cytoscape.js
  money-flow map colour-coded by role, with click-through details for every
  address and movement; settings screen for API keys/endpoints and OFAC
  refresh. All assets served locally (works offline once labels are loaded,
  for cached data).

### Dependencies
- Python 3.10+ with `fastapi`, `uvicorn`, `httpx`, `reportlab`
  (see requirements.txt for pinned versions).
- Browser (any modern one); Cytoscape.js 3.30.2 is bundled locally at
  `app/static/vendor/` — no CDN needed at runtime.
- Free Etherscan API key for Ethereum tracing. Bitcoin requires no key.

### Known limitations / problems detected
1. **Newest-first cap:** only the most recent 25 outgoing transactions per
   address (and most recent 100 for Ethereum) are examined. An address with
   heavy later activity can hide the relevant older spend — verified during
   live testing (a documentation-example address with 434 historical spends
   showed none in its 25 most recent transactions). A date-window filter is
   planned for Phase 2.
2. **No UTXO clustering / change detection yet:** Bitcoin change outputs to
   fresh addresses are followed like real hops, so the suspect's own change
   may appear as part of the flow. Same-address change is excluded. Phase 2.
3. **Smart contracts are dead ends:** DEX swaps, bridges and mixers are
   flagged but not traversed (Phase 3). No continuity is invented.
4. **Seed exchange list is small (34 wallets, ETH-heavy):** absence of an
   exit finding is not evidence of absence; BTC exchange coverage is
   especially thin. Deposit addresses are per-customer and unlisted — traces
   typically reach a labelled corporate wallet one hop after the deposit
   address.
5. **Ethereum path not yet live-tested end-to-end** (needs the user's
   Etherscan key). Code paths are exercised; first live run should be
   watched.
6. **API keys stored unencrypted** in the local SQLite database. Use OS disk
   encryption (BitLocker) on evidence machines. Encrypted storage is on the
   roadmap.
7. **Etherscan tx-hash trace timestamps:** a trace started from an Ethereum
   txid may show no timestamp on the first edge (the proxy endpoint does not
   return one); subsequent hops have timestamps.
8. **Non-standard Bitcoin scripts** (OP_RETURN, bare multisig) are skipped
   silently as destinations.
9. **Backward tracing, taint-method selection (FIFO/haircut), Tron/TRC-20,
   timeline and Sankey views, GraphML export** are not in this version —
   they are Phase 2/3 roadmap items.
