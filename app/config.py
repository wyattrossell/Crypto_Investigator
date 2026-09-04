"""
Central configuration and named constants for Crypto Investigator.

Every tunable number, URL, and default lives here so that behaviour is
auditable in one place. Nothing in this file performs network or disk I/O.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------

APP_NAME = "Crypto Investigator"
APP_VERSION = "0.11.0"

# The tool binds to localhost ONLY. Evidence must not be exposed on a network
# interface without a deliberate deployment decision (Phase 3+).
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8321

# ---------------------------------------------------------------------------
# Storage locations
# ---------------------------------------------------------------------------
#
# Two layouts, resolved once at import time:
#
# * SOURCE CHECKOUT (python run.py): everything lives inside the project
#   folder - data/ next to app/ - exactly as before.
# * INSTALLED BUILD (PyInstaller, sys.frozen): the program folder under
#   Program Files is read-only, so case data lives in the user's profile:
#   %LOCALAPPDATA%\CryptoInvestigator\data. The bundled read-only
#   resources (UI files, default label files, icon) live in the bundle.
#
# Either layout can be overridden - e.g. to keep evidence on an encrypted
# volume - with the CRYPTO_INVESTIGATOR_DATA environment variable, or by
# placing a one-line text file named data-location.txt (containing the
# folder path) next to run.py / the installed executable. Documented in
# the setup guide; the resolved folder is shown in Settings.

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # PyInstaller: the executable's folder holds the program; bundled
    # resources are extracted alongside it (onedir build) under _internal.
    PROGRAM_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", PROGRAM_DIR))
    PROJECT_ROOT = BUNDLE_DIR
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    PROGRAM_DIR = PROJECT_ROOT
    BUNDLE_DIR = PROJECT_ROOT

DATA_LOCATION_FILE = PROGRAM_DIR / "data-location.txt"


def _resolve_data_dir() -> Path:
    """Where mutable state (database, reports, logs, editable label files)
    lives. Precedence: environment variable > data-location.txt > default
    for the layout."""
    override = os.environ.get("CRYPTO_INVESTIGATOR_DATA", "").strip()
    if not override:
        try:
            override = DATA_LOCATION_FILE.read_text(
                encoding="utf-8").strip().splitlines()[0].strip()
        except (OSError, IndexError):
            override = ""
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    if FROZEN:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(local) / "CryptoInvestigator" / "data"
    return PROJECT_ROOT / "data"


DATA_DIR = _resolve_data_dir()
DATABASE_PATH = DATA_DIR / "investigator.db"
REPORTS_DIR = DATA_DIR / "reports"
LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "crypto_investigator.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 3

# Read-only bundled resources.
STATIC_DIR = BUNDLE_DIR / "app" / "static"
ICON_PATH = BUNDLE_DIR / "assets" / "crypto_investigator.ico"
# Default copies of the agency-editable label files ship with the program;
# on first run they are copied into DATA_DIR/labels (see bootstrap_data_dir)
# and the copies are what the tool reads, so agency edits survive upgrades.
DEFAULT_LABELS_DIR = BUNDLE_DIR / "data" / "labels"
LABELS_DIR = DATA_DIR / "labels"
SEED_LABELS_PATH = LABELS_DIR / "exchange_labels_seed.json"
# Agency-editable compliant / non-compliant exchange designations
# (FATF-referenced; see the file's own source_note for the honesty rules).
EXCHANGE_COMPLIANCE_PATH = LABELS_DIR / "exchange_compliance.json"
# Agency-editable directory of custodian legal-process contacts (portals,
# guidelines) used by the freeze/preservation request generator. Contacts
# change; the file's source_note tells the investigator to verify.
CUSTODIAN_CONTACTS_PATH = LABELS_DIR / "custodian_contacts.json"
EDITABLE_LABEL_FILES = ("exchange_labels_seed.json",
                        "exchange_compliance.json",
                        "custodian_contacts.json")


def bootstrap_data_dir() -> None:
    """Create the data folders and seed the editable label files from the
    bundled defaults when they are absent. Never overwrites an existing
    file (agency edits are preserved). In a source checkout DATA_DIR and
    the defaults are the same folder, so this is a no-op there."""
    import shutil
    for folder in (DATA_DIR, LABELS_DIR, REPORTS_DIR, LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    if DEFAULT_LABELS_DIR.resolve() == LABELS_DIR.resolve():
        return
    for name in EDITABLE_LABEL_FILES:
        target = LABELS_DIR / name
        source = DEFAULT_LABELS_DIR / name
        if not target.exists() and source.exists():
            shutil.copyfile(source, target)

# ---------------------------------------------------------------------------
# Supported chains (Bitcoin + Ethereum/ERC-20 + Tron/TRC-20)
# ---------------------------------------------------------------------------

CHAIN_BITCOIN = "bitcoin"
CHAIN_ETHEREUM = "ethereum"
CHAIN_TRON = "tron"
SUPPORTED_CHAINS = (CHAIN_BITCOIN, CHAIN_ETHEREUM, CHAIN_TRON)

# Chains we can RECOGNISE from address format but not yet trace. The UI must
# tell the user honestly what will happen for each of these.
CHAIN_MONERO = "monero"        # privacy coin - tracing is NOT possible
CHAIN_LITECOIN = "litecoin"    # Phase 3

# Trace direction: forward follows the victim's money out; backward traces
# where a wallet's funds CAME from (source-of-funds / forfeiture work).
DIRECTION_FORWARD = "forward"
DIRECTION_BACKWARD = "backward"

# ---------------------------------------------------------------------------
# Data-source endpoints (all public). Each is swappable via the settings
# screen; these are only the defaults.
# ---------------------------------------------------------------------------

# mempool.space exposes the Esplora REST API. No API key required.
DEFAULT_BITCOIN_API_BASE = "https://mempool.space/api"
# Blockstream runs the same Esplora API - drop-in fallback.
FALLBACK_BITCOIN_API_BASE = "https://blockstream.info/api"
# Additional community Esplora hosts (identical API) for the round-robin;
# volunteer-run, so keep the per-host throttle polite.
ESPLORA_EXTRA_BASES = ("https://mempool.emzy.de/api",)

# Etherscan V2 endpoint (chainid parameter selects the network).
DEFAULT_ETHERSCAN_API_BASE = "https://api.etherscan.io/v2/api"
ETHERSCAN_CHAIN_ID_MAINNET = 1

# Blockscout's public Ethereum-mainnet instance: KEYLESS Etherscan-
# compatible API + REST v2. Used automatically when no Etherscan key is
# configured, so Ethereum tracing needs NO account at all; an Etherscan
# key remains an optional rate-limit upgrade. Mode override via the
# 'ethereum_api_mode' setting: auto (default) | etherscan | blockscout.
DEFAULT_BLOCKSCOUT_API_BASE = "https://eth.blockscout.com/api"
# Routescan: a second KEYLESS Etherscan-compatible endpoint (documented
# free keyless tier: 2 req/s, 10k/day) - the drop-in fallback when
# Blockscout misbehaves.
DEFAULT_ROUTESCAN_API_BASE = ("https://api.routescan.io/v2/network/mainnet"
                              "/evm/1/etherscan/api")
ETHEREUM_API_MODE_AUTO = "auto"
ETHEREUM_API_MODES = ("auto", "etherscan", "blockscout", "routescan")

# TronGrid (official Tron API). Works keyless at a low rate; a free API key
# (Settings screen) raises the limit and is sent as a header, never in
# custody-logged URLs.
DEFAULT_TRONGRID_API_BASE = "https://api.trongrid.io"
# TRC-20 USDT contract (the dominant pig-butchering rail).
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID_PAGE_SIZE = 200

# OFAC Specially Designated Nationals list (official US Treasury source).
# Digital-currency addresses appear as ID records inside sdn.xml.
OFAC_SDN_XML_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"

# GraphSense TagPacks (github.com/graphsense/graphsense-tagpacks, MIT):
# community-maintained exchange/mixer address attribution. The pack listing
# is fetched live so renamed/added packs keep working; only packs whose
# filename suggests exchange/mixer content are downloaded, and oversized
# packs are skipped (counted and reported honestly).
GRAPHSENSE_PACK_LISTING_URL = ("https://api.github.com/repos/graphsense/"
                               "graphsense-tagpacks/contents/packs")
GRAPHSENSE_PACK_NAME_PATTERN = r"exchange|wallet|tornado|mixer"
GRAPHSENSE_MAX_PACK_BYTES = 6_000_000
# TagPack currency codes -> our chain identifiers (others kept raw, like
# the OFAC importer does, so the data is retained for later phases).
TAGPACK_CURRENCY_TO_CHAIN = {
    "BTC": CHAIN_BITCOIN,
    "ETH": CHAIN_ETHEREUM,
    "TRX": CHAIN_TRON,
    "LTC": CHAIN_LITECOIN,
}

# ScamSniffer open drainer/phishing blacklist (GPL-3.0, EVM addresses,
# daily updates with a 7-day delay on the public data).
SCAMSNIFFER_ADDRESSES_URL = ("https://raw.githubusercontent.com/scamsniffer/"
                             "scam-database/main/blacklist/address.json")
# Label category for third-party scam-report data (not the agency's own
# flags): shown on nodes and raised as a finding, never a stopping point.
LABEL_CATEGORY_SCAM_REPORT = "scam_report"

# CoinGecko public API (no key). Spot prices drive the header ticker and the
# dust-threshold USD hints; the /history endpoint values traced movements at
# the transaction date. USD figures are CONTEXT, not market appraisals.
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_SPOT_URL = (f"{COINGECKO_API_BASE}/simple/price"
                      f"?ids=bitcoin,ethereum,tron&vs_currencies=usd")

# KEYLESS price fallbacks (public market data; no account needed):
# Kraken serves spot + ~2 years of daily OHLC for BTC/ETH/TRX; Coinbase
# Exchange serves arbitrary historical daily candles for BTC/ETH. Order:
# CoinGecko -> Kraken -> Coinbase -> CoinGecko per-date (last resort).
KRAKEN_API_BASE = "https://api.kraken.com/0/public"
KRAKEN_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "TRX": "TRXUSD"}
COINBASE_API_BASE = "https://api.exchange.coinbase.com"
COINBASE_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
COINBASE_MAX_CANDLES = 290      # API cap is 300 per request; stay under
SPOT_PRICE_TTL_SECONDS = 60          # in-memory ticker cache
# Assets valued via CoinGecko (symbol -> CoinGecko coin id); stablecoins are
# valued at $1.00 without an API call; other tokens are left unvalued.
COINGECKO_COIN_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "TRX": "tron"}
STABLECOIN_USD = {"USDT": 1.0, "USDC": 1.0}

# Stablecoin ISSUERS can freeze/blacklist tokens at ANY address, including
# self-custodied ones - the freeze-request generator targets the issuer when
# traced stablecoins sit at a wallet with no custodian label. Entity names
# must match entries in the custodian directory.
STABLECOIN_ISSUERS = {"USDT": "Tether", "USDC": "Circle"}

# ---------------------------------------------------------------------------
# Rate limiting / retry behaviour per provider
# ---------------------------------------------------------------------------

# Minimum seconds between requests to the same provider (token-bucket style).
MIN_REQUEST_INTERVAL_SECONDS = {
    "mempool.space": 0.6,
    "etherscan": 0.35,   # free tier is 3 req/s (2026); stay under it
    "ofac": 5.0,
    "coingecko": 3.0,    # demo tier is ~30 req/min; stay well under
    "blockstream.info": 0.6,   # second Esplora host (round-robin partner)
    "graphsense": 0.5,   # raw.githubusercontent.com label-pack downloads
    "scamsniffer": 0.5,  # raw.githubusercontent.com blacklist download
    "trongrid": 1.1,     # documented anonymous tier is 1 req/s
    "trongrid-keyed": 0.2,   # free key allows 15 req/s; stay well under
    "blockscout": 0.5,   # keyless tier is 300 req/min; stay well under
    "routescan": 0.6,    # keyless tier is 2 req/s, 10k/day
    "kraken": 1.1,       # public endpoints ~1 req/s per IP
    "coinbase": 0.4,     # public market data, 10 req/s documented
    "mempool.emzy.de": 1.0,   # volunteer-run community Esplora host
}
DEFAULT_MIN_REQUEST_INTERVAL = 1.0

HTTP_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 4
# Exponential backoff: wait BACKOFF_BASE * 2^attempt seconds after a
# rate-limit (429) or server (5xx) response.
BACKOFF_BASE_SECONDS = 1.5

# ---------------------------------------------------------------------------
# Tracing engine defaults and hard limits
# ---------------------------------------------------------------------------

# Background prefetch workers that warm the per-trace fetch memo for queued
# addresses while the engine processes the current one. Requests stay inside
# every per-provider rate limit (the throttle is shared); prefetching only
# overlaps network latency with processing. 0 disables prefetching.
PREFETCH_WORKERS = 3

DEFAULT_MAX_DEPTH = 3          # hops followed from the starting point
MAX_DEPTH_LIMIT = 6            # normal mode never follows more than this

# "Extended" mode: keep following until every branch resolves into a
# classified outcome (exchange, funds at rest, service, mixer, ...), up
# to this many hops. Works with or without a focus transaction. The
# address/edge caps below still apply.
EXTENDED_MAX_DEPTH = 25

# Probable-exchange-deposit (sweep) pattern: an address that forwards at
# least this share of its traced inflow, within this many hours, to a
# high-activity wallet matches the classic deposit-address fingerprint.
SWEEP_MIN_FORWARD_RATIO = 0.90
SWEEP_MAX_GAP_HOURS = 72

# Cap per finding type so reports stay readable (rest itemised in JSON).
MAX_FINDINGS_PER_TYPE = 10

# ---------------------------------------------------------------------------
# Search patterns (branch-selection policy per expanded address).
# Every skipped branch is counted and reported - selective search must
# never present itself as complete search.
# ---------------------------------------------------------------------------

SEARCH_PATTERN_RAPID = "rapid"          # follow the bulk of the value
SEARCH_PATTERN_BALANCED = "balanced"    # meaningful branches only
SEARCH_PATTERN_THOROUGH = "thorough"    # every movement above dust
SEARCH_PATTERNS = (SEARCH_PATTERN_RAPID, SEARCH_PATTERN_BALANCED,
                   SEARCH_PATTERN_THOROUGH)
DEFAULT_SEARCH_PATTERN = SEARCH_PATTERN_BALANCED

# Rapid: per asset, follow the largest movements until this share of the
# address's outgoing value is covered, capped per address.
RAPID_VALUE_COVERAGE = 0.80
RAPID_MAX_BRANCHES = 3
# Balanced: per asset, follow movements carrying at least this fraction
# of the address's outgoing value, capped per address.
BALANCED_MIN_BRANCH_FRACTION = 0.05
BALANCED_MAX_BRANCHES = 8

# Ignore outputs/transfers below these values ("dust") while tracing.
DEFAULT_DUST_THRESHOLD_BTC = 0.0001      # ~ a few dollars
DEFAULT_DUST_THRESHOLD_ETH = 0.001
DEFAULT_DUST_THRESHOLD_TOKEN_USD = 1.0   # for USDT/USDC (1 unit ~= 1 USD)
DEFAULT_DUST_THRESHOLD_TRX = 5.0         # ~ a dollar or two

# Safety valves so a trace cannot explode on a busy address (e.g. if an
# unlabelled exchange hot wallet is expanded by mistake).
MAX_OUTGOING_TXS_PER_ADDRESS = 25   # newest-first cap per address expansion
MAX_TOTAL_ADDRESSES_PER_TRACE = 400
MAX_TOTAL_EDGES_PER_TRACE = 1500

# An address with more than this many total transactions is almost certainly
# a service (exchange, gambling site, payment processor), not a personal
# wallet. We stop expanding and flag it instead of flooding the graph.
HIGH_ACTIVITY_TX_THRESHOLD = 500

# Unit conversions.
SATOSHIS_PER_BTC = 100_000_000
WEI_PER_ETH = 10**18
SUN_PER_TRX = 1_000_000

# ---------------------------------------------------------------------------
# ERC-20 tokens prioritised in Phase 1
# ---------------------------------------------------------------------------

# contract address (lowercase) -> (symbol, decimals)
PRIORITY_ERC20_TOKENS = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
}

# ---------------------------------------------------------------------------
# Node roles used across the graph, database and report
# ---------------------------------------------------------------------------

ROLE_VICTIM = "victim"
ROLE_INTERMEDIARY = "intermediary"
ROLE_EXCHANGE = "exchange"          # custodial off-ramp -> legal target
ROLE_SANCTIONED = "sanctioned"      # on the OFAC SDN list
ROLE_MIXER = "mixer"                # labelled mixing service - trail obscured
ROLE_HIGH_ACTIVITY = "high_activity_service"  # probable service, unlabelled
ROLE_CONTRACT = "contract"          # smart contract (not followed in v0.1)
ROLE_UNEXPANDED = "unexpanded"      # beyond depth/limits, not yet explored

# Label category used for the agency's own fraud designations (wallet_flags
# table). A flagged wallet is still EXPANDED (following its money out is the
# point); the flag rides along as a finding and on every report.
LABEL_CATEGORY_FLAGGED = "flagged"
LABEL_SOURCE_AGENCY_FLAG = "agency_flag"

# ---------------------------------------------------------------------------
# Background scheduler: wallet watches + label auto-refresh
# ---------------------------------------------------------------------------

SCHEDULER_TICK_SECONDS = 60
# How often each watched wallet is re-checked for movement.
WATCH_DEFAULT_INTERVAL_MINUTES = 30
# Label sources refresh automatically once this many days old - but only
# sources the user has already downloaded at least once (the tool never
# starts a bulk download the user did not ask for).
LABEL_AUTOREFRESH_DAYS = {
    "ofac_sdn": 7,
    "scamsniffer": 7,
    "graphsense_tagpack": 30,
}

# Settings whose values are secrets: encrypted at rest with Windows DPAPI
# (user-scoped). On a machine/profile where DPAPI cannot decrypt them the
# value reads back empty and must be re-entered - documented behaviour.
SECRET_SETTING_KEYS = ("etherscan_api_key", "coingecko_api_key",
                       "trongrid_api_key", "ai_api_key")

# ---------------------------------------------------------------------------
# Optional AI assistant (OFF by default; nothing is sent anywhere until the
# investigator configures a provider in Settings)
# ---------------------------------------------------------------------------

# Provider "custom" is any OpenAI-compatible endpoint - including LOCAL
# models (Ollama, LM Studio), which keep case data on the machine entirely.
AI_PROVIDERS = ("anthropic", "openai", "custom")
AI_DEFAULT_BASES = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "custom": "http://localhost:11434/v1",   # Ollama's default
}
AI_DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-5",
    "custom": "",                            # user must name the model
}
AI_MAX_RESPONSE_TOKENS = 4000
AI_REQUEST_TIMEOUT_SECONDS = 240.0
# Trace context sent to the model is capped (largest movements first, with
# an honest truncation note) so huge traces still fit any model's window.
AI_MAX_CONTEXT_CHARS = 60_000
AI_MAX_CONTEXT_EDGES = 250
AI_MAX_CONTEXT_NODES = 150

# Confidence levels attached to every attribution the tool makes.
CONFIDENCE_HIGH = "high"        # official list (OFAC) or multi-source match
CONFIDENCE_MEDIUM = "medium"    # single reputable community/explorer tag
CONFIDENCE_LOW = "low"          # heuristic inference only
