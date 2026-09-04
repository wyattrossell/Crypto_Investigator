"""
USD price service backed by CoinGecko's free public API (no key).

Two jobs:

1. SPOT prices (BTC/ETH) - the header ticker and the "your dust threshold
   is about $X" hints. Cached in memory for SPOT_PRICE_TTL_SECONDS;
   deliberately NOT written to the evidence cache (a live quote is not
   evidence and changes constantly).

2. HISTORICAL daily prices - value every traced movement in USD at the
   transaction's date. Fetched through the standard ProviderClient, so
   each pull is throttled, custody-logged, and (for past dates, which are
   immutable) cached permanently like any other acquisition.

Honesty rules: values are marked as approximations from a daily closing
price, stablecoins are valued at $1.00 by definition of the peg, and
anything else is left unvalued rather than guessed. A price failure never
fails a trace - it degrades to "USD unavailable" with a warning.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from app import config
from app.providers.base import ProviderClient, ProviderError

USD_VALUATION_NOTE = (
    "Approximate USD values use the daily price for each transaction's "
    "UTC date, from CoinGecko where available, otherwise from public "
    "market data (Kraken, then Coinbase Exchange, daily opening prices); "
    "each acquisition's exact source URL is in the chain-of-custody log. "
    "Stablecoins (USDT/USDC) are valued at $1.00. These figures are "
    "context for the reader, not market appraisals, and assets without a "
    "reliable free price source are left unvalued.")


class KrakenClient(ProviderClient):
    """Kraken public market data (keyless spot + ~2yr daily OHLC)."""
    provider_name = "kraken"


class CoinbaseClient(ProviderClient):
    """Coinbase Exchange public market data (keyless historical candles)."""
    provider_name = "coinbase"


class CoinGeckoClient(ProviderClient):
    """CoinGecko puller (throttled + custody-logged like every source).

    A free 'demo' API key (Settings screen) is sent as a HEADER, never in
    the URL, so custody-log entries stay shareable and key-free. Keyless
    access still works but shares an IP-throttled public pool and is
    unreliable - the UI recommends getting the free key."""
    provider_name = "coingecko"

    def __init__(self, trace_id=None, memo=None):
        super().__init__(trace_id=trace_id, memo=memo)
        from app import database   # local import avoids a startup cycle
        key = database.get_setting("coingecko_api_key")
        if key:
            self._client.headers["x-cg-demo-api-key"] = key


# ---------------------------------------------------------------------------
# Spot prices (ticker + dust hints)
# ---------------------------------------------------------------------------

_spot_lock = threading.Lock()
_spot_cache = {"at": 0.0, "data": None}


def _coingecko_spot() -> dict:
    client = CoinGeckoClient()
    try:
        raw = client.fetch(config.COINGECKO_SPOT_URL, cacheable=False)
    finally:
        client.close()
    payload = json.loads(raw)
    prices = {
        "BTC": payload.get("bitcoin", {}).get("usd"),
        "ETH": payload.get("ethereum", {}).get("usd"),
        "TRX": payload.get("tron", {}).get("usd"),
    }
    if not prices["BTC"]:
        raise ProviderError("coingecko: no BTC spot price")
    return prices, "CoinGecko"


def _kraken_spot() -> dict:
    """Keyless spot fallback (public market data)."""
    pairs = ",".join(config.KRAKEN_PAIRS.values())
    client = KrakenClient()
    try:
        raw = client.fetch(f"{config.KRAKEN_API_BASE}/Ticker?pair={pairs}",
                           cacheable=False)
    finally:
        client.close()
    payload = json.loads(raw)
    if payload.get("error"):
        raise ProviderError(f"kraken: {payload['error']}")
    prices = {}
    for key, entry in (payload.get("result") or {}).items():
        try:
            last = float(entry["c"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if "XBT" in key:
            prices["BTC"] = last
        elif "ETH" in key:
            prices["ETH"] = last
        elif "TRX" in key:
            prices["TRX"] = last
    if "BTC" not in prices:
        raise ProviderError("kraken: no BTC spot price")
    return prices, "Kraken (public market data)"


def get_spot() -> dict:
    """Current BTC/ETH/TRX prices in USD, from CoinGecko with a keyless
    Kraken fallback. Served from a short in-memory cache so the UI can
    poll freely."""
    with _spot_lock:
        fresh = (_spot_cache["data"] is not None and
                 time.monotonic() - _spot_cache["at"] <
                 config.SPOT_PRICE_TTL_SECONDS)
        if fresh:
            return _spot_cache["data"]

    try:
        prices, source = _coingecko_spot()
    except Exception:
        prices, source = _kraken_spot()
    data = dict(prices)
    data["fetched_utc"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    data["source"] = source
    with _spot_lock:
        _spot_cache["data"] = data
        _spot_cache["at"] = time.monotonic()
    return data


# ---------------------------------------------------------------------------
# Historical valuation of trace movements
# ---------------------------------------------------------------------------

def _utc_date(unix_ts) -> str:
    """Unix seconds -> 'YYYY-MM-DD' in UTC."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime(
        "%Y-%m-%d")


def _range_daily_prices(client: CoinGeckoClient, coin_id: str,
                        dates: set) -> dict:
    """Daily USD prices for many dates in ONE market_chart/range call
    (instead of one throttled /history call per date - the difference
    between seconds and minutes on a trace spanning many dates).

    Returns {date_iso: usd} for every requested date the range supplied;
    missing dates are the caller's problem (per-date fallback). The first
    price point of each UTC day approximates the 00:00 daily snapshot the
    /history endpoint reports. The URL is derived only from the requested
    dates, so identical requests cache; a range touching today is still
    moving and is fetched live."""
    days = sorted(dates)
    start = datetime.strptime(days[0], "%Y-%m-%d").replace(
        tzinfo=timezone.utc)
    end = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    from_ts = int(start.timestamp()) - 3600
    to_ts = int(end.timestamp()) + 90000     # past end-of-day, with margin
    url = (f"{config.COINGECKO_API_BASE}/coins/{coin_id}/market_chart/range"
           f"?vs_currency=usd&from={from_ts}&to={to_ts}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = client.fetch(url, cacheable=(days[-1] < today))
    payload = json.loads(raw)
    by_date = {}
    for point in payload.get("prices", []):
        date_iso = _utc_date(point[0] / 1000)
        if date_iso not in by_date:      # first point of the day ~ 00:00
            by_date[date_iso] = float(point[1])
    return {d: by_date[d] for d in dates if d in by_date}


def _kraken_daily_prices(client: KrakenClient, asset: str,
                         dates: set) -> dict:
    """Keyless daily prices from Kraken OHLC (one call; covers roughly the
    most recent two years). Returns {date_iso: usd} for the dates it can
    supply; the daily OPEN approximates the 00:00 UTC price."""
    pair = config.KRAKEN_PAIRS.get(asset)
    if not pair:
        return {}
    raw = client.fetch(
        f"{config.KRAKEN_API_BASE}/OHLC?pair={pair}&interval=1440",
        cacheable=False)
    payload = json.loads(raw)
    if payload.get("error"):
        raise ProviderError(f"kraken: {payload['error']}")
    result = payload.get("result") or {}
    candles = next((v for k, v in result.items() if k != "last"), [])
    by_date = {}
    for candle in candles:
        try:
            by_date[_utc_date(int(candle[0]))] = float(candle[1])
        except (TypeError, ValueError, IndexError):
            continue
    return {d: by_date[d] for d in dates if d in by_date}


def _coinbase_daily_prices(client: CoinbaseClient, asset: str,
                           dates: set) -> dict:
    """Keyless daily prices from Coinbase Exchange candles (BTC/ETH;
    arbitrary history, up to ~290 days per request). The daily OPEN
    approximates the 00:00 UTC price. Fully-past ranges cache."""
    product = config.COINBASE_PRODUCTS.get(asset)
    if not product:
        return {}
    supplied = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    remaining = sorted(dates)
    while remaining:
        start = datetime.strptime(remaining[0], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
        window_end_date = min(
            remaining[-1],
            (start + timedelta(days=config.COINBASE_MAX_CANDLES))
            .strftime("%Y-%m-%d"))
        url = (f"{config.COINBASE_API_BASE}/products/{product}/candles"
               f"?granularity=86400&start={remaining[0]}T00:00:00Z"
               f"&end={window_end_date}T00:00:00Z")
        raw = client.fetch(url, cacheable=(window_end_date < today))
        for candle in json.loads(raw):
            try:
                # Candle shape: [time, low, high, open, close, volume].
                supplied[_utc_date(int(candle[0]))] = float(candle[3])
            except (TypeError, ValueError, IndexError):
                continue
        remaining = [d for d in remaining if d > window_end_date]
    return {d: supplied[d] for d in dates if d in supplied}


def _history_price_usd(client: CoinGeckoClient, coin_id: str,
                       date_iso: str) -> float:
    """Daily price of `coin_id` on `date_iso` (YYYY-MM-DD), in USD.
    Past dates are immutable and cached forever; today's price is still
    moving, so it is fetched live and not cached."""
    day, month, year = date_iso[8:10], date_iso[5:7], date_iso[0:4]
    url = (f"{config.COINGECKO_API_BASE}/coins/{coin_id}/history"
           f"?date={day}-{month}-{year}&localization=false")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = client.fetch(url, cacheable=(date_iso != today))
    payload = json.loads(raw)
    price = (payload.get("market_data", {})
             .get("current_price", {}).get("usd"))
    if price is None:
        raise ProviderError(
            f"coingecko: no USD price for {coin_id} on {date_iso}")
    return float(price)


def annotate_usd(edges: list, exits: dict, trace_id, progress,
                 warnings: list) -> None:
    """Attach `value_usd` (and `usd_price_date`) to every movement that can
    be valued: BTC/ETH via the historical daily price, stablecoins at $1.
    Also sums `totals_usd` per exit. Mutates in place; never raises."""
    # Distinct (coin, date) pairs needed - one API call each, then reused.
    needed = set()
    for edge in edges:
        if edge["asset"] in config.COINGECKO_COIN_IDS and edge["timestamp"]:
            needed.add((edge["asset"], _utc_date(edge["timestamp"])))

    price_by_key = {}
    failures = set()
    if needed:
        by_asset = {}
        for asset, date_iso in needed:
            by_asset.setdefault(asset, set()).add(date_iso)
        gecko = CoinGeckoClient(trace_id=trace_id)
        kraken = KrakenClient(trace_id=trace_id)
        coinbase = CoinbaseClient(trace_id=trace_id)
        try:
            for asset, dates in sorted(by_asset.items()):
                coin_id = config.COINGECKO_COIN_IDS[asset]
                progress(f"Valuing movements in USD ({asset}: "
                         f"{len(dates)} price date(s))...")
                # Source chain, cheapest-first: CoinGecko batched range ->
                # Kraken OHLC (keyless, ~2yr) -> Coinbase candles (keyless,
                # BTC/ETH any age) -> CoinGecko per-date (slow last resort).
                missing = set(dates)
                for fetch in (
                        lambda d: _range_daily_prices(gecko, coin_id, d),
                        lambda d: _kraken_daily_prices(kraken, asset, d),
                        lambda d: _coinbase_daily_prices(coinbase, asset, d),
                ):
                    if not missing:
                        break
                    try:
                        supplied = fetch(missing)
                    except (ProviderError, ValueError, KeyError,
                            IndexError):
                        continue
                    for date_iso, price in supplied.items():
                        price_by_key[(asset, date_iso)] = price
                    missing -= set(supplied)
                for index, date_iso in enumerate(sorted(missing), 1):
                    progress(f"Valuing movements in USD ({asset}: "
                             f"{index}/{len(missing)} stubborn dates)...")
                    try:
                        price_by_key[(asset, date_iso)] = _history_price_usd(
                            gecko, coin_id, date_iso)
                    except (ProviderError, ValueError, KeyError) as exc:
                        failures.add(f"{asset} on {date_iso} ({exc})")
        finally:
            gecko.close()
            kraken.close()
            coinbase.close()

    def value_edge(movement: dict) -> None:
        asset = movement.get("asset")
        if asset in config.STABLECOIN_USD:
            movement["value_usd"] = round(
                movement["value"] * config.STABLECOIN_USD[asset], 2)
            movement["usd_price_date"] = "pegged $1.00"
            return
        if not movement.get("timestamp"):
            return
        key = (asset, _utc_date(movement["timestamp"]))
        if key in price_by_key:
            movement["value_usd"] = round(
                movement["value"] * price_by_key[key], 2)
            movement["usd_price_date"] = key[1]

    for edge in edges:
        value_edge(edge)
    for exit_entry in exits.values():
        totals_usd = 0.0
        any_valued = False
        for funding in exit_entry["funding"]:
            value_edge(funding)
            if funding.get("value_usd") is not None:
                totals_usd += funding["value_usd"]
                any_valued = True
        if any_valued:
            exit_entry["totals_usd"] = round(totals_usd, 2)

    if failures:
        warnings.append(
            "USD valuation unavailable for some movements: "
            + "; ".join(sorted(failures))
            + ". Amounts remain shown in crypto units.")
