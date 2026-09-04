"""
Tron data provider backed by the official TronGrid API.

Covers native TRX transfers and TRC-20 token transfers (USDT prioritised -
the dominant pig-butchering rail). Works keyless at a low rate; a free
TronGrid API key (Settings screen) raises the limit and is sent as a
HEADER, never in custody-logged URLs.

Same normalised movement shape as the other providers. Known limitations
(stated in the UI/report): focus transactions are not yet supported on
Tron, and the high-activity check is based on the most recent page of
transactions (an address with a full page of 200 plus more is treated as
busy).
"""

import hashlib
import json

from app import config, database
from app.providers.base import ProviderClient, ProviderError

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _hex_to_base58(hex_address: str) -> str:
    """Tron hex address ('41' + 20 bytes) -> base58check ('T...')."""
    raw = bytes.fromhex(hex_address)
    checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    number = int.from_bytes(raw + checksum, "big")
    encoded = ""
    while number > 0:
        number, remainder = divmod(number, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    for byte in raw:
        if byte == 0:
            encoded = "1" + encoded
        else:
            break
    return encoded


class TronProvider(ProviderClient):
    """TronGrid client for Tron mainnet (TRX + TRC-20)."""

    provider_name = "trongrid"

    def __init__(self, trace_id=None, memo=None):
        super().__init__(trace_id=trace_id, memo=memo)
        self.api_base = config.DEFAULT_TRONGRID_API_BASE
        key = database.get_setting("trongrid_api_key")
        if key:
            self._client.headers["TRON-PRO-API-KEY"] = key
            # A keyed account gets 15 req/s; use the faster throttle
            # bucket (the custody log shows which tier made each pull).
            self.provider_name = "trongrid-keyed"

    def _get_json(self, path: str, cacheable: bool) -> dict:
        try:
            body = self.fetch(f"{self.api_base}{path}", cacheable=cacheable,
                              cache_key=f"trongrid:{path}")
        except ProviderError as exc:
            if "HTTP 401" in str(exc):
                raise ProviderError(
                    "trongrid: request rejected (HTTP 401). An INVALID "
                    "TronGrid API key is rejected even for requests that "
                    "need no key - check the key in Settings (or clear "
                    "it).") from exc
            raise
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise ProviderError(
                f"trongrid: {payload.get('error', 'request failed')}")
        return payload

    # -- account facts -------------------------------------------------------

    def account_summary(self, address: str) -> dict:
        """Balance snapshot (watch checks). Not cached - balances change."""
        payload = self._get_json(f"/v1/accounts/{address}", cacheable=False)
        data = payload.get("data") or []
        balance_sun = data[0].get("balance", 0) if data else 0
        return {"address": address, "balance_raw": balance_sun}

    def _native_page(self, address: str, direction_param: str) -> dict:
        """Most recent page of native transactions involving the address.
        Memoised per trace, so the activity check and the movement pull
        share one request."""
        return self._get_json(
            f"/v1/accounts/{address}/transactions?only_confirmed=true"
            f"&{direction_param}=true&limit={config.TRONGRID_PAGE_SIZE}",
            cacheable=False)

    def outgoing_count(self, address: str) -> int:
        """Recent-activity estimate for the high-activity heuristic: the
        size of the newest page of sent transactions; a full page with more
        available is reported as above the threshold (basis text explains
        the estimate is page-based)."""
        payload = self._native_page(address, "only_from")
        items = payload.get("data") or []
        has_more = bool((payload.get("meta") or {}).get("fingerprint"))
        if len(items) >= config.TRONGRID_PAGE_SIZE and has_more:
            return config.HIGH_ACTIVITY_TX_THRESHOLD + 1
        return len(items)

    # -- movements -----------------------------------------------------------

    def _native_movements(self, address: str, max_txs: int,
                          outgoing: bool) -> list:
        payload = self._native_page(
            address, "only_from" if outgoing else "only_to")
        movements = []
        for tx in (payload.get("data") or [])[:max_txs]:
            contracts = (tx.get("raw_data") or {}).get("contract") or []
            if not contracts or contracts[0].get("type") != \
                    "TransferContract":
                continue   # smart-contract call or other type; TRC-20 is
                           # captured separately below
            value = (contracts[0].get("parameter") or {}).get("value") or {}
            amount_sun = value.get("amount", 0)
            owner_hex = value.get("owner_address", "")
            to_hex = value.get("to_address", "")
            if not owner_hex or not to_hex or amount_sun <= 0:
                continue
            try:
                source = _hex_to_base58(owner_hex)
                destination = _hex_to_base58(to_hex)
            except ValueError:
                continue
            timestamp_ms = tx.get("block_timestamp")
            movements.append({
                "txid": tx.get("txID", ""),
                "timestamp": int(timestamp_ms / 1000) if timestamp_ms
                             else None,
                "from_address": source,
                "to_address": destination,
                "asset": "TRX",
                "value": amount_sun / config.SUN_PER_TRX,
                "value_raw": amount_sun,
                "is_self_transfer": source == destination,
            })
        return movements

    def _trc20_movements(self, address: str, max_txs: int,
                         outgoing: bool) -> list:
        direction_param = "only_from" if outgoing else "only_to"
        payload = self._get_json(
            f"/v1/accounts/{address}/transactions/trc20"
            f"?only_confirmed=true&{direction_param}=true"
            f"&limit={config.TRONGRID_PAGE_SIZE}",
            cacheable=False)
        movements = []
        for tx in (payload.get("data") or [])[:max_txs]:
            if tx.get("type") != "Transfer":
                continue
            source = tx.get("from") or ""
            destination = tx.get("to") or ""
            if not source or not destination:
                continue
            token = tx.get("token_info") or {}
            contract = token.get("address", "")
            if contract == config.TRON_USDT_CONTRACT:
                symbol, decimals = "USDT", 6
            else:
                symbol = token.get("symbol") or "TRC20"
                try:
                    decimals = int(token.get("decimals", 6))
                except (TypeError, ValueError):
                    decimals = 6
            try:
                value_raw = int(tx.get("value", "0"))
            except (TypeError, ValueError):
                continue
            timestamp_ms = tx.get("block_timestamp")
            movements.append({
                "txid": tx.get("transaction_id", ""),
                "timestamp": int(timestamp_ms / 1000) if timestamp_ms
                             else None,
                "from_address": source,
                "to_address": destination,
                "asset": symbol,
                "value": value_raw / (10 ** decimals),
                "value_raw": value_raw,
                "token_contract": contract,
                "is_self_transfer": source == destination,
            })
        return movements

    def outgoing_movements(self, address: str, max_txs: int) -> list:
        """TRX + TRC-20 transfers sent FROM `address`, newest first."""
        return (self._native_movements(address, max_txs, outgoing=True)
                + self._trc20_movements(address, max_txs, outgoing=True))

    def incoming_movements(self, address: str, max_txs: int) -> list:
        """TRX + TRC-20 transfers RECEIVED by `address` (backward mode)."""
        return (self._native_movements(address, max_txs, outgoing=False)
                + self._trc20_movements(address, max_txs, outgoing=False))

    def transaction_output_movements(self, txid: str) -> list:
        """Focus transactions are not yet supported on Tron; the API layer
        rejects them before a trace starts, so this is a safety net."""
        raise ProviderError(
            "Focus transactions are not yet supported on Tron - trace the "
            "wallet without one.")
