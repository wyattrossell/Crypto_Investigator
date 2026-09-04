# Keyless public APIs — live-verified survey (2026-08-31)

Purpose: let any agency run Crypto Investigator with ZERO accounts.
Every endpoint below was live-tested on 2026-08-31 from a US law-
enforcement network (both by the implementing session and by a research
pass). "Verified" = the exact URL was fetched and returned the described
data. Implemented in v0.9.1.

## Ethereum (keyless) — IMPLEMENTED

- **Blockscout eth.blockscout.com — PRIMARY keyless backend.**
  Etherscan-compatible `module=account&action=txlist|tokentx|balance`
  verified working keyless; `module=proxy` is NOT supported (HTTP 400) —
  the tool uses Blockscout REST v2 instead: `/api/v2/addresses/{addr}`
  (`is_contract` + `proxy_type`, which distinguishes EIP-7702 delegated
  wallets), `/api/v2/addresses/{addr}/counters` (transaction count) and
  `/api/v2/transactions/{hash}` (includes timestamps Etherscan's proxy
  lacks). Documented keyless limit: 300 requests/min per IP
  (docs.blockscout.com/devs/apis/requests-and-limits). Open source,
  free use permitted. Risk: Blockscout has announced eventual
  deprecation of per-instance APIs toward a multichain service.
- **Routescan — keyless BACKUP backend.**
  `https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api` —
  true Etherscan drop-in verified keyless including
  `proxy&action=eth_getCode|eth_getTransactionCount|
  eth_getTransactionByHash` (NOT eth_getBalance — use
  `account&action=balance`). Documented "Free (Keyless)": 2 req/s,
  10,000/day (routescan.io/documentation/plans-and-limits/rate-limits).
- **Public JSON-RPC pools** (proxy-style calls only; no account history):
  ethereum-rpc.publicnode.com ✓, eth.drpc.org ✓ (~free public tier),
  1rpc.io/eth ✓ (~10k/day). NOT keyless any more: rpc.ankr.com (401),
  cloudflare-eth.com (defunct), eth.llamarpc.com (down). Not needed by
  the tool (Blockscout/Routescan cover it) — listed as reserves.
- **Ethplorer `apiKey=freekey`**: works (shared literal key) but 1k/day,
  30-day timestamp window, "not for production" — reserve only.
- **Blockchair keyless: REJECTED** — 30/min, ~1k/day, personal/testing
  use only per docs, and the shared agency IP was already HTTP-430
  blacklisted during testing.

## Tron (keyless) — ALREADY IMPLEMENTED (v0.9.0), throttles corrected

- **TronGrid anonymous tier: 1 req/s** (documented; stepwise reductions
  ended at 1 QPS). The tool's throttle now respects it; a free key
  (15 req/s, 100k/day) switches to a faster throttle bucket.
- **TronScan apilist.tronscanapi.com**: works keyless today
  (`/api/token_trc20/transfers`, `/api/transaction`, `/api/account`;
  `accountv2` already 401s), but keyless service is officially
  deprecated since 2025-08-31 — NOT implemented as a dependency.

## USD prices (keyless) — IMPLEMENTED as fallback chain

Order: CoinGecko batched range → Kraken → Coinbase → CoinGecko per-date.
- **Kraken public** (~1 req/s per IP): `Ticker` spot for BTC/ETH/TRX ✓;
  `OHLC?interval=1440` daily candles ✓ — window verified at ~721 recent
  candles (~2 years). TRX history exists back to 2020-03 only via the
  Trades endpoint (not implemented; out of window = unvalued).
- **Coinbase Exchange public** (10 req/s): daily candles verified back
  to 2015 (BTC-USD) / 2016 (ETH-USD); ≤300 candles/request; TRX not
  listed (404).
- **CoinGecko keyless**: spot works; historical >365 days is blocked
  (error 10012, verified); 5–15 req/min.
- **DEAD keyless (verified): CryptoCompare** (401, absorbed into
  CoinDesk, key mandatory) and **CoinCap** (v2 gone; v3 requires key).
- **Binance notes**: api.binance.com BLOCKS US IPs — never use.
  api.binance.us klines work keyless but thin TRX volume and short
  history — reserve only.

## Bitcoin (keyless) — extended

- mempool.space (undisclosed limits; 429s observed — the tool's backoff
  handles it), blockstream.info, and NEW third host mempool.emzy.de
  (community-run, verified, polite 1 req/s throttle). Reserve:
  blockchain.info `rawaddr` (legacy shape).

## Remaining gaps (honest)

- TRX/USD older than ~2 years: no keyless source; movements stay
  unvalued with the standard warning.
- Etherscan internal transactions: keyed-only; keyless tracing covers
  native + token transfers.
- Keyless tiers are per-IP: heavy shared agency networks may hit limits
  sooner; optional keys remain the upgrade path.
