# Integration & Legal-Process Research (2026-08-31)

## 1. Address-label sources (free)
- **GraphSense TagPacks** (github.com/graphsense/graphsense-tagpacks, MIT, active thru Jun 2026): YAML packs (~23.5MB, 100+ files) — Binance, Bitfinex, BitMEX (7 parts), Bybit, Crypto.com, Deribit, Huobi, KuCoin, OKX, SwissBorg exchange packs; also ofac/lazarus/tornado_cash/ransomware/walletexplorer/usdt_blacklist/miners/sextortion. Tens of thousands of tagged addresses, BTC-heavy. Raw: raw.githubusercontent.com/graphsense/graphsense-tagpacks/master/packs/<file>.yaml. Format: header (title, creator, confidence, category, source, lastmod, actor) + tags: [{address, currency, label}]. Many packs lastmod 2019-2022 — show provenance/lastmod.
- **Etherscan labels API**: Pro-Plus-only (NOT free). Free workaround: dawsbot/eth-labels (MIT, ~169k labels, EVM, eth-labels.com API) — Etherscan-scraped provenance.
- **Arkham API**: application-gated, credit-based; free tier unverified (LOW-MED). Opportunistic only.
- **OpenSanctions**: CryptoWallet schema, superset of OFAC (FBI Lazarus, Israel, Japan, UK, ransomwhe.re). BUT license is non-commercial; NOT clearly free for police depts. OFAC portion reproducible from SDN XML (already ingested).
- **0xB10C/ofac-sanctioned-digital-currency-addresses**: free per-asset flat lists (lists branch, sanctioned_addresses_XBT.txt / _ETH.txt), regenerated from SDN. HIGH.
- **WalletExplorer.com**: ALIVE (block 964778, 2026-08-30). BTC clustering labels, per-wallet CSV; label set skews pre-2017.
- **ScamSniffer scam-database** (GPL-3.0): daily updates w/ 7-day delay. raw .../scam-database/main/blacklist/address.json + domains.json. Mostly EVM drainer addresses. HIGH.
- **CryptoScamDB**: dead/legacy. **DefiLlama CEX wallets**: extractable from adapters config, moderate effort.

## 2. Scam-report DBs
- **Chainabuse Public API v1.2**: GET https://api.chainabuse.com/v0/reports?address=&chain=&perPage=50; Basic auth (API key as user AND pass); free key via account settings; **free tier 10 calls/month** (≈500 reports). LE partner tier: 5,000 calls/hr + private data (victim location, evidence, scammer IPs, socials) via chainabuse.com/partner-contact. Use as manual button; apply for LE tier.

## 3. Exchange legal-process directory (verified 2026-08-31)
- **SEARCH.org ISP List still maintained**: search.org/resources/isp-list/ (fallback).
- **Kodex generic gov signup**: app.kodexglobal.com/gov/signup (Coinbase, Crypto.com +).
- Coinbase: Kodex gov signup. Subpoena→subscriber records; warrant→content; freeze needs court order; preservation via portal. HIGH.
- Binance global: LERS binance.com/en/support/law-enforcement → Kodex app.kodexglobal.com/binance/signup (~3 business days). **Preservation 90 days renewable**; may notify user unless order prohibits. HIGH.
- Binance.US: **LERT@BINANCE.US**, BAM Trading Services Inc., 252 NW 29th St, 10th FL STE 1014, Miami FL 33127. **Probable-cause LE request → ~2-week freeze**. HIGH.
- Kraken: Compliance & Legal form support.kraken.com/articles/how-do-i-submit-a-legal-inquiry (form 35862482750484). MED-HIGH.
- Gemini: lawenforcement@gemini.com, 600 Third Ave 2nd Fl NY 10016. MED — verify before use.
- Crypto.com: Kodex gov signup; ~30-day response; foreign→MLAT. HIGH.
- OKX: Kodex app.kodexglobal.com/okx/signin; emergencies **enforcement@okx.com**; wants signed court order/letterhead + CSV of addresses/txids. Seychelles→MLAT. HIGH.
- Bybit: **NO verified channel**; "BYBIT159@amail.com" on bybitexcai.com is probable PHISHING clone — do not use. Holds non-binding. Dubai→MLAT. LOW.
- Bitfinex: processes LE requests via Kodex (customer story). BVI→MLAT. MED.
- Bitstamp: now "Bitstamp by Robinhood" (2025 acquisition) — stale-risk, verify; expect Robinhood consolidation. LOW.
- KuCoin: portal kucoin.com/legal/requests; guide /legal/law-enforcement-request-guidelines; EU: lawenforcement@kucoin.eu. Signed/sealed docs, English. **Freeze auto-lifts after 30 days** unless duration specified. HIGH.
- Poloniex: nothing verifiable. LOW.
- Cash App (Block): block.xyz/legal/government → inforequestform; **lawenforcement@squareup.com**; ~60-day min response; emergency path. HIGH.
- PayPal/Venmo: **safetyhub.paypal.com** portal; ~10 business days. HIGH.
- Robinhood: **LERequests@robinhood.com**; holds: **accountrestraints@robinhood.com** (LE guide PDF Nov 14 2025, cdn.robinhood.com/assets/robinhood/legal/Law-Enforcement-Request.pdf). Preservation accepted without formal process. HIGH.
- MEXC: LEORS mexc.com/support/requests/legal; access review 15-20 business days; **preservation 60 days**; **freeze max 30 days**; return only via court order. HIGH.
- HTX (Huobi): no public LE channel. LOW.
- Gate.io: **regulatory@gate.com**, from official gov domain, stamped/signed docs. MED.
- MLAT: portals = voluntary cooperation + preservation; compelled production from offshore = MLAT via DOJ OIA.

## 4. Freeze/preservation norms (US)
- 18 U.S.C. § 2703(f): preserve 90 days, renewable once +90; no judicial approval; snapshot-in-time. Applicability to exchanges unsettled — template should cite "to the extent applicable" + request voluntary cooperation in the alternative; counsel review flag.
- Template contents (USCG M5810.1G generic template): letterhead, date, provider legal name/address, citation, case number, precise identifiers (wallet addresses + tx hashes copy-pasteable), preserve-all-records-90-days language, non-disclosure request, "not a production request", officer contact. Exchanges also want incident summary, amounts, dates, fund-flow (OKX: CSV).
- Freeze ≠ preservation: voluntary freezes short/discretionary (Binance.US ~2wk, MEXC ≤30d, KuCoin 30d auto-lift, Bybit non-binding). Durable restraint = court order / seizure warrant (18 U.S.C. § 981(b), 21 U.S.C. § 853(f)); then funds → government wallet.
- DOJ: Asset Forfeiture Policy Manual 2025 (digital-asset seizure; immediate transfer to agency wallet; DAC Network; admin forfeiture ≤$500k).

## 5. Speed API facts
- Esplora: /address/:addr/txs = up to 50 mempool + 25 confirmed; /txs/chain/:last_txid = 25 confirmed/page. blockstream.info ~50 req/s nginx (unofficial), keyed Explorer API 500k free/mo exists; mempool.space undisclosed limits, 429s. Design ≤2-4 req/s sustained + failover.
- **Etherscan V2 free = 3 calls/s, 100k/day** (reduced from 5/s). getblocknobytime: module=block&action=getblocknobytime&timestamp=&closest=before|after.
- **CoinGecko Demo (free)**: signup key via x-cg-demo-api-key header; 10k calls/month; per-minute 30-100 (design for 30); keyless is shared-IP throttled/unreliable. market_chart/range granularity auto: ≤1d→5-min, 1-90d→hourly, >90d→daily(00:00 UTC); hourly span max 90d/request; **history limited to past 365 days on free/demo**.

## Top 5 integrations by value/effort
1. GraphSense TagPacks bulk import (high/low).
2. Chainabuse GET /v0/reports button (high/low; 10 calls/mo budget; apply LE tier).
3. Exchange legal-process directory + 2703(f) letter generator (very high/medium) — only HIGH-confidence channels; print guide URL on letters; "to the extent applicable / voluntary in the alternative" clause.
4. ScamSniffer address.json + 0xB10C OFAC lists nightly local blacklists (med-high/trivial).
5. Etherscan getblocknobytime time-windows + Esplora pagination w/ dual-host failover (med/low).

Unverified/stale-risk flags: Gemini email (single source), Bitstamp/Bitfinex page contents, Bybit/HTX/Poloniex channels, Arkham free tier, Chainabuse LE cost, OpenSanctions gov licensing, mempool.space numeric limits.
