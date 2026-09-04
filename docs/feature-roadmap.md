# Feature roadmap — learned from the commercial investigation platforms

Survey of Chainalysis (Reactor/Rapid/Wallet Scan/Signals/Agents), TRM Labs
(Forensics/Co-Case/Tactical/Beacon/Chainabuse), Elliptic
(Investigator/Holistic/Copilot), Crystal Intelligence (Expert/Ask
Crystal/Scam Alert), MetaSleuth (BlockSec), QLUE/QLUE Express, Arkham,
Breadcrumbs, AMLBot (AI Tracer) and Scorechain — **updated 2026-08-31**
(previous survey July 2026). Features are ranked by value to a
law-enforcement investigator divided by implementation cost in THIS tool,
and split by whether they need proprietary data. The full research reports
behind this update are summarised in the v0.8.0 gap-analysis document.

## Already implemented (as of v0.9.0)

- **Wallet watchlists + movement alerts** (QLUE, MetaSleuth, Arkham,
  Etherscan, TRM, Scorechain — was the most common feature we lacked):
  scheduler-driven checks with alert badges and one-click watch from the
  funds-at-rest finding. (v0.9.0)
- **Tron/TRC-20 USDT tracing** (the pig-butchering rail) via TronGrid.
  (v0.9.0)
- **Backward source-of-funds tracing** (forfeiture work). (v0.9.0)
- **Stablecoin issuer freeze route** (Tether/Circle can freeze at any
  address, incl. self-custodied) + kiosk-operator directory entries +
  in-app custodian directory browser. (v0.9.0)
- **Evidence/disclosure package ZIP with SHA-256 manifest, affidavit
  methodology helper, map-image exhibit export, case overlay map
  (multi-victim convergence), per-address investigator notes, case
  export/import, label auto-refresh, DPAPI-encrypted API keys.** (v0.9.0)

- Transaction graph visualization (all vendors) — Cytoscape flow map with
  display simplification.
- OFAC sanctions screening — live SDN parse, HIGH-confidence labels.
- **Bulk exchange/mixer attribution** (the core of every commercial tool,
  at free-data scale): GraphSense TagPacks import — ~337k labelled
  addresses with per-label provenance — plus the ScamSniffer drainer
  blacklist (2.5k EVM addresses) as a corroborating finding. (v0.8.0)
- **Wallet flagging / local intelligence base** (analog of TRM Beacon's
  flag concept, single-agency scope): agency fraud designations checked by
  every future trace, with cross-case linkage findings. (v0.8.0)
- **Freeze/preservation request generator** (analog of TRM freeze
  packages / Chainalysis Rapid legal-request packaging / AMLBot freeze
  templates): DRAFT 2703(f)-style letter + Attachment A transaction
  schedule + verified custodian legal-process directory. (v0.8.0)
- **Speed: prefetch workers + single-flight memo + value-ordered
  expansion + batched USD pricing** (the "minutes not days" the 2025–26
  triage products sell, within free-API rate limits). (v0.8.0)
- Case management, court-ready PDF, chain-of-custody log (URL + UTC +
  SHA-256 per pull — stronger than any vendor's public claim).
- Investigative findings & disposition-of-funds accounting (v0.7.0).
- Search patterns with honest skipped-branch accounting (v0.6.0).
- Exchange compliance designations, agency-editable (v0.5.0).
- Extended tracing, warrant traceroute, time-forward rule (v0.4.0).
- USD context pricing, readable flow map (v0.3.0).
- IC3 complaint helper (no vendor has this — still a differentiator).
- External enrichment links (explorer + Chainabuse per address).

## High value, feasible with free/public data (build next)

1. **Chainabuse API enrichment** (free key, Basic auth,
   GET /v0/reports?address=). Free tier is only 10 calls/month, so make it
   a per-address button, not automatic; apply for the free LE partner tier
   (private victim reports, jurisdiction filters) via
   chainabuse.com/partner-contact — that is the real prize.
3. **Bulk address triage** (Elliptic bulk screening, Crystal Lite,
   TRM Tactical). Paste N addresses (seized phone/exchange records), get
   labels/flags/balances/first-last activity in a table. Cheap: label
   lookup + address_summary in a loop over the new 337k-label base.
4. **Plain-language wallet summary** (Chainalysis Rapid's core). One
   paragraph per wallet: age, activity, counterparties, % to labelled
   services, flags. Pure arithmetic over data we already fetch.
5. **EVM label depth**: GraphSense is BTC-heavy (336k BTC vs ~700 ETH).
   Import dawsbot/eth-labels (~169k EVM labels, MIT, Etherscan-scraped —
   note provenance honestly) to close the Ethereum attribution gap.
6. **Bitcoin address clustering via published heuristics**
   (common-input-ownership + change detection), labelled MEDIUM/LOW
   confidence. Court precedent exists (Sterlingov Daubert), but implement
   glass-box: show WHY two addresses cluster. WalletExplorer (alive,
   block-current) can corroborate historical BTC clusters.
7. **Date-window fetching** (kills the "newest-25 hides the old spend"
   limitation): Etherscan getblocknobytime + sort=asc from the arrival
   block; Esplora cursor pagination back to the arrival timestamp.
8. **Deconfliction export/import** (INTERPOL pain point; TRM Tactical
   feature): export the agency's flag list as a signed CSV; import
   another agency's list as a separate label source.
9. **Canvas polish**: pin layout, hide nodes, per-address investigator
   notes (annotations table exists, still unused by the UI), notes in
   the PDF.

## Feasible but larger projects

- **Cross-chain / bridge awareness** — recognise major bridge contracts so
  a dead end says "entered Wormhole bridge to Solana".
- **Decode common DEX swaps** — "swapped 10 ETH → 18,400 USDT on Uniswap"
  instead of a contract dead end (the top Reactor complaint in reviews is
  DeFi confusion — nobody has fully solved it).
- **Taint accounting methods** (FIFO/haircut selector).
- **GraphML/CSV export** for i2/Maltego interop.
- ~~Local AI assistant~~ **SHIPPED (v0.10.0)** on exactly the terms this
  roadmap demanded: optional and off by default (the non-AI mode IS the
  default), any provider incl. fully local models, evidence-linked
  system prompt, full-prompt-and-response audit log, output
  banner-marked as assistance and never auto-inserted into court
  documents. Matches the 2026 vendor wave (Co-Case, Agents, Copilot,
  Ask Crystal) without their cloud lock-in.

## Requires proprietary data or scale — do NOT attempt, be honest instead

- Mass attribution beyond public packs ("6.4B addresses", 400M labels).
  Our stance: bulk public imports + bring-your-own-labels + honest
  "absence of a label is not absence of an exchange" warnings.
- Mixer demixing (Crystal claims it; Chainalysis sells it). Flag mixers
  honestly; never fabricate continuity.
- Ground-truth risk scores (BitRank/KYT/Navigator). A numeric score
  without their data is false precision; we present labelled facts +
  confidence instead.
- Real-time exchange interdiction network (TRM Beacon) — requires the
  exchanges' participation; our freeze-letter speed is the single-agency
  approximation.
- Chainabuse Pro LE-restricted data — gated partnership, apply, don't
  scrape.

## Our defensible white space (no vendor offers these — keep them sharp)

- Per-data-pull chain-of-custody hashing exposed to the user.
- Explicit per-inference confidence + basis on every hop.
- IC3 complaint worksheet generation.
- "No fabricated continuity" as a stated, documented guarantee.
- $0 and local-only: the entire market runs $50k–$700k/yr SaaS; the
  triage tier (Rapid, QLUE Express LE, AI Tracer) exists precisely
  because small agencies are priced out — we ARE that tier, with court
  documentation none of the triage tools carry.
