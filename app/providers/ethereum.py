"""
Ethereum data provider with TWO interchangeable backends:

* etherscan   - the Etherscan V2 API (free API key required; higher limits).
* blockscout  - Blockscout's public Ethereum-mainnet instance, KEYLESS:
                its Etherscan-compatible API serves the account lists and
                its REST v2 API serves contract checks, activity counters
                and transaction lookups. No account needed at all.
* routescan   - Routescan's keyless Etherscan-compatible endpoint
                (documented free keyless tier, 2 req/s / 10k per day):
                a true drop-in, used when Blockscout misbehaves.
* alchemy     - Alchemy JSON-RPC (free key: 30M CU/month, 25 req/s).
                alchemy_getAssetTransfers returns external, INTERNAL and
                ERC-20 transfers with block timestamps in one call.
                Requests are POSTs; the custody log records a key-free
                descriptor (alchemy://eth-mainnet/<method>?<params>).

Backend selection ('ethereum_api_mode' setting): auto (default - Alchemy
when its key is set, else Etherscan when its key is set, else keyless
Blockscout), or forced 'alchemy' / 'etherscan' / 'blockscout' /
'routescan'. A self-hosted Blockscout can replace the public instance via
the 'blockscout_api_base' setting.

Provides the same normalised movement shape as the Bitcoin provider,
covering native ETH transfers and ERC-20 token transfers (USDT/USDC
prioritised, all others captured with their reported symbol/decimals).

EIP-7702 note (Pectra, 2025): an externally owned account can carry
DELEGATED code beginning 0xef0100. Such wallets are ordinary personal
wallets, not smart contracts, and both backends treat them accordingly.
"""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from app import config, database
from app.providers.base import ProviderClient, ProviderError

# How many records to request per list call. 100 is far above the
# per-address expansion cap, so a single page is always sufficient.
ETHERSCAN_PAGE_SIZE = 100

# eth_getTransactionCount returns the account nonce = number of transactions
# ever SENT. Above this, the address is treated as a probable service.
HIGH_ACTIVITY_NONCE_THRESHOLD = config.HIGH_ACTIVITY_TX_THRESHOLD

# EIP-7702 delegation designator: code at an EOA starting with this marks a
# delegated personal wallet, NOT a deployed contract.
EIP7702_CODE_PREFIX = "0xef0100"


class EthereumProvider(ProviderClient):
    """Ethereum mainnet client (Etherscan V2 or keyless Blockscout)."""

    provider_name = "etherscan"

    def __init__(self, trace_id=None, memo=None):
        super().__init__(trace_id=trace_id, memo=memo)
        self.api_base = database.get_setting(
            "etherscan_api_base", config.DEFAULT_ETHERSCAN_API_BASE)
        self.api_key = database.get_setting("etherscan_api_key", "")
        self.alchemy_key = database.get_setting("alchemy_api_key", "")
        custom_blockscout = database.get_setting(
            "blockscout_api_base", "").strip().rstrip("/")
        mode = database.get_setting("ethereum_api_mode",
                                    config.ETHEREUM_API_MODE_AUTO)
        if mode not in config.ETHEREUM_API_MODES:
            mode = config.ETHEREUM_API_MODE_AUTO
        self.mode_note = ""
        if mode == config.ETHEREUM_API_MODE_AUTO:
            if self.alchemy_key:
                self.backend = "alchemy"
            elif self.api_key:
                self.backend = "etherscan"
            else:
                self.backend = "blockscout"
        else:
            self.backend = mode
        if self.backend == "alchemy" and not self.alchemy_key:
            self.mode_note = ("Ethereum mode 'alchemy' selected but no "
                              "Alchemy key is set; using keyless Blockscout.")
            self.backend = "blockscout"
        if self.backend == "alchemy":
            self.provider_name = "alchemy"
            self.alchemy_url = config.ALCHEMY_API_BASE + self.alchemy_key
        elif self.backend == "blockscout":
            # Keys the throttle table and the custody log honestly.
            if custom_blockscout:
                self.provider_name = "blockscout-custom"
                self.blockscout_base = custom_blockscout
            else:
                self.provider_name = "blockscout"
                self.blockscout_base = config.DEFAULT_BLOCKSCOUT_API_BASE
            self.blockscout_root = self.blockscout_base.rsplit("/api", 1)[0]
        elif self.backend == "routescan":
            self.provider_name = "routescan"
            self.api_base = config.DEFAULT_ROUTESCAN_API_BASE

    def _require_key(self) -> None:
        if not self.api_key:
            raise ProviderError(
                "The Ethereum API mode is forced to 'etherscan' but no "
                "Etherscan API key is configured. Paste a free key in "
                "Settings, or switch the mode back to 'auto' to use the "
                "keyless Blockscout API.")

    # -- raw endpoint wrappers ----------------------------------------------

    def _call(self, cacheable: bool, **params) -> object:
        """One Etherscan-compatible API call (both backends speak this
        format for account lists). Any API key is excluded from the cache
        key and custody log so logs stay shareable and keys stay private."""
        if self.backend == "blockscout":
            loggable_url = f"{self.blockscout_base}?{urlencode(params)}"
            real_url = loggable_url
        elif self.backend == "routescan":
            loggable_url = f"{self.api_base}?{urlencode(params)}"
            real_url = loggable_url
        else:
            self._require_key()
            base_params = {"chainid": config.ETHERSCAN_CHAIN_ID_MAINNET,
                           **params}
            # Loggable URL: re-runnable by another analyst with their key.
            loggable_url = f"{self.api_base}?{urlencode(base_params)}"
            real_url = f"{loggable_url}&apikey={self.api_key}"

        body = self.fetch(real_url, cacheable=cacheable,
                          cache_key=loggable_url)
        payload = json.loads(body)

        # All backends signal errors inside a 200 response. Empty result
        # sets are phrased differently per backend ("No transactions
        # found", "No token transfers found", ...) - all mean [].
        if isinstance(payload, dict) and payload.get("status") == "0":
            message = str(payload.get("message", ""))
            result = str(payload.get("result", ""))
            combined = message + result
            if "No " in combined and " found" in combined:
                return []
            raise ProviderError(
                f"{self.provider_name}: {message} {result}".strip())
        return payload.get("result") if isinstance(payload, dict) else payload

    def _rpc(self, method: str, params: list, cacheable: bool) -> object:
        """One Alchemy JSON-RPC call. The custody log and cache key carry a
        key-free descriptor; the real URL (with the key) never leaves the
        process."""
        descriptor = (f"alchemy://eth-mainnet/{method}?"
                      f"{json.dumps(params, sort_keys=True, separators=(',', ':'))}")
        body = self.fetch(self.alchemy_url, cacheable=cacheable,
                          cache_key=descriptor, log_url=descriptor,
                          json_body={"jsonrpc": "2.0", "id": 1,
                                     "method": method, "params": params})
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            raise ProviderError(
                f"alchemy: {error.get('message', error) if isinstance(error, dict) else error}")
        return payload.get("result") if isinstance(payload, dict) else payload

    def _alchemy_transfers(self, address: str, direction: str) -> list:
        """External + internal + ERC-20 transfers touching `address`
        (direction 'from' or 'to'), newest first, one call (120 CU)."""
        params = {
            "fromBlock": "0x0", "toBlock": "latest",
            "category": list(config.ALCHEMY_TRANSFER_CATEGORIES),
            "withMetadata": True, "excludeZeroValue": True,
            "order": "desc", "maxCount": hex(ETHERSCAN_PAGE_SIZE),
        }
        params["fromAddress" if direction == "from" else "toAddress"] = address
        result = self._rpc("alchemy_getAssetTransfers", [params],
                           cacheable=False) or {}
        return result.get("transfers") or []

    @staticmethod
    def _movement_from_transfer(transfer: dict):
        """Normalise one Alchemy transfer into the shared movement shape
        (None for kinds the tracer does not follow)."""
        category = transfer.get("category")
        if category not in config.ALCHEMY_TRANSFER_CATEGORIES:
            return None
        source = (transfer.get("from") or "").lower()
        destination = (transfer.get("to") or "").lower()
        if not source or not destination:
            return None
        raw = transfer.get("rawContract") or {}
        try:
            value_raw = int(raw.get("value") or "0x0", 16)
        except (TypeError, ValueError):
            value_raw = 0
        timestamp = None
        block_ts = (transfer.get("metadata") or {}).get("blockTimestamp")
        if block_ts:
            try:
                timestamp = int(datetime.fromisoformat(
                    block_ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                timestamp = None
        movement = {
            "txid": transfer.get("hash"),
            "timestamp": timestamp,
            "from_address": source,
            "to_address": destination,
            "is_self_transfer": source == destination,
            "transfer_kind": category,
        }
        if category == "erc20":
            contract = (raw.get("address") or "").lower()
            if contract in config.PRIORITY_ERC20_TOKENS:
                symbol, decimals = config.PRIORITY_ERC20_TOKENS[contract]
            else:
                symbol = transfer.get("asset") or "ERC20"
                try:
                    decimals = int(raw.get("decimal") or "0x12", 16)
                except (TypeError, ValueError):
                    decimals = 18
            movement.update({"asset": symbol, "value_raw": value_raw,
                             "value": value_raw / (10 ** decimals),
                             "token_contract": contract})
        else:
            movement.update({"asset": "ETH", "value_raw": value_raw,
                             "value": value_raw / config.WEI_PER_ETH})
        if movement["value_raw"] <= 0:
            return None
        return movement

    def _alchemy_movements(self, address: str, direction: str,
                           max_txs: int) -> list:
        """Movements for the tracer from one transfers call, capped like
        the Etherscan path: `max_txs` native (external+internal) and
        `max_txs` token movements, newest first."""
        native, tokens = [], []
        for transfer in self._alchemy_transfers(address, direction):
            movement = self._movement_from_transfer(transfer)
            if movement is None:
                continue
            (tokens if movement["asset"] != "ETH" or
             movement.get("token_contract") else native).append(movement)
        return native[:max_txs] + tokens[:max_txs]

    def _v2(self, path: str, cacheable: bool) -> dict:
        """One Blockscout REST v2 call (blockscout backend only)."""
        url = f"{self.blockscout_root}/api/v2{path}"
        body = self.fetch(url, cacheable=cacheable,
                          cache_key=f"blockscout-v2:{path}")
        return json.loads(body)

    # -- account facts -------------------------------------------------------

    def is_contract(self, address: str) -> bool:
        """True when the address holds DEPLOYED bytecode (a smart
        contract). EIP-7702 delegated personal wallets are NOT contracts."""
        if self.backend == "blockscout":
            info = self._v2(f"/addresses/{address}", cacheable=False)
            return bool(info.get("is_contract")) and \
                info.get("proxy_type") != "eip7702"
        if self.backend == "alchemy":
            result = self._rpc("eth_getCode", [address, "latest"], True)
        else:
            result = self._call(True, module="proxy", action="eth_getCode",
                                address=address, tag="latest")
        if not isinstance(result, str) or result in ("0x", "0x0", ""):
            return False
        return not result.lower().startswith(EIP7702_CODE_PREFIX)

    def outgoing_count(self, address: str) -> int:
        """Activity estimate for the high-activity/service heuristic:
        the account nonce (etherscan) or the indexed transaction count
        (blockscout). Not cached - it grows over time."""
        if self.backend == "blockscout":
            counters = self._v2(f"/addresses/{address}/counters",
                                cacheable=False)
            try:
                return int(counters.get("transactions_count") or 0)
            except (TypeError, ValueError):
                return 0
        if self.backend == "alchemy":
            result = self._rpc("eth_getTransactionCount",
                               [address, "latest"], False)
        else:
            result = self._call(False, module="proxy",
                                action="eth_getTransactionCount",
                                address=address, tag="latest")
        try:
            return int(result, 16)
        except (TypeError, ValueError):
            return 0

    def balance_raw(self, address: str) -> int:
        """Current ETH balance in wei (watch checks). Not cached. The
        account/balance action works identically on all three backends
        (routescan does not serve proxy eth_getBalance)."""
        if self.backend == "alchemy":
            try:
                return int(self._rpc("eth_getBalance", [address, "latest"],
                                     False), 16)
            except (TypeError, ValueError):
                return 0
        result = self._call(False, module="account", action="balance",
                            address=address)
        try:
            return int(result)
        except (TypeError, ValueError):
            return 0

    # -- normalised movements --------------------------------------------------

    def outgoing_movements(self, address: str, max_txs: int) -> list:
        """ETH + ERC-20 transfers sent FROM `address`, newest first, capped at
        `max_txs` of each kind. Contract-interaction transactions that move no
        value are skipped; token movements are captured separately below."""
        address = address.lower()
        if self.backend == "alchemy":
            return self._alchemy_movements(address, "from", max_txs)
        movements = []

        # --- native ETH transfers ---
        normal_txs = self._call(False, module="account", action="txlist",
                                address=address, startblock=0,
                                endblock=99999999, page=1,
                                offset=ETHERSCAN_PAGE_SIZE, sort="desc") or []
        for tx in normal_txs[:max_txs]:
            if tx.get("from", "").lower() != address:
                continue                       # inbound - not followed forward
            if tx.get("isError") == "1":
                continue                       # reverted transaction
            value_wei = int(tx.get("value", "0"))
            if value_wei == 0:
                continue                       # pure contract call, no ETH moved
            destination = (tx.get("to") or "").lower()
            if not destination:
                continue                       # contract creation
            movements.append({
                "txid": tx["hash"],
                "timestamp": int(tx.get("timeStamp", 0)) or None,
                "from_address": address,
                "to_address": destination,
                "asset": "ETH",
                "value": value_wei / config.WEI_PER_ETH,
                "value_raw": value_wei,
                "is_self_transfer": destination == address,
            })

        # --- ERC-20 token transfers ---
        token_txs = self._call(False, module="account", action="tokentx",
                               address=address, startblock=0,
                               endblock=99999999, page=1,
                               offset=ETHERSCAN_PAGE_SIZE, sort="desc") or []
        for tx in token_txs[:max_txs]:
            if tx.get("from", "").lower() != address:
                continue
            destination = (tx.get("to") or "").lower()
            if not destination:
                continue
            contract = (tx.get("contractAddress") or "").lower()
            # Prefer our vetted symbol/decimals for priority stablecoins;
            # fall back to what the explorer reports for other tokens.
            if contract in config.PRIORITY_ERC20_TOKENS:
                symbol, decimals = config.PRIORITY_ERC20_TOKENS[contract]
            else:
                symbol = tx.get("tokenSymbol") or "ERC20"
                try:
                    decimals = int(tx.get("tokenDecimal", 18))
                except ValueError:
                    decimals = 18
            value_raw = int(tx.get("value", "0"))
            movements.append({
                "txid": tx["hash"],
                "timestamp": int(tx.get("timeStamp", 0)) or None,
                "from_address": address,
                "to_address": destination,
                "asset": symbol,
                "value": value_raw / (10 ** decimals),
                "value_raw": value_raw,
                "token_contract": contract,
                "is_self_transfer": destination == address,
            })

        return movements

    def incoming_movements(self, address: str, max_txs: int) -> list:
        """ETH + ERC-20 transfers RECEIVED by `address`, newest first
        (backward tracing): who funded this wallet."""
        address = address.lower()
        if self.backend == "alchemy":
            return self._alchemy_movements(address, "to", max_txs)
        movements = []

        normal_txs = self._call(False, module="account", action="txlist",
                                address=address, startblock=0,
                                endblock=99999999, page=1,
                                offset=ETHERSCAN_PAGE_SIZE, sort="desc") or []
        for tx in normal_txs[:max_txs]:
            if (tx.get("to") or "").lower() != address:
                continue                       # outbound - not a funding
            if tx.get("isError") == "1":
                continue
            value_wei = int(tx.get("value", "0"))
            if value_wei == 0:
                continue
            source = (tx.get("from") or "").lower()
            if not source:
                continue
            movements.append({
                "txid": tx["hash"],
                "timestamp": int(tx.get("timeStamp", 0)) or None,
                "from_address": source,
                "to_address": address,
                "asset": "ETH",
                "value": value_wei / config.WEI_PER_ETH,
                "value_raw": value_wei,
                "is_self_transfer": source == address,
            })

        token_txs = self._call(False, module="account", action="tokentx",
                               address=address, startblock=0,
                               endblock=99999999, page=1,
                               offset=ETHERSCAN_PAGE_SIZE, sort="desc") or []
        for tx in token_txs[:max_txs]:
            if (tx.get("to") or "").lower() != address:
                continue
            source = (tx.get("from") or "").lower()
            if not source:
                continue
            contract = (tx.get("contractAddress") or "").lower()
            if contract in config.PRIORITY_ERC20_TOKENS:
                symbol, decimals = config.PRIORITY_ERC20_TOKENS[contract]
            else:
                symbol = tx.get("tokenSymbol") or "ERC20"
                try:
                    decimals = int(tx.get("tokenDecimal", 18))
                except ValueError:
                    decimals = 18
            value_raw = int(tx.get("value", "0"))
            movements.append({
                "txid": tx["hash"],
                "timestamp": int(tx.get("timeStamp", 0)) or None,
                "from_address": source,
                "to_address": address,
                "asset": symbol,
                "value": value_raw / (10 ** decimals),
                "value_raw": value_raw,
                "token_contract": contract,
                "is_self_transfer": source == address,
            })

        return movements

    def _transaction_head(self, txid: str) -> dict:
        """Sender/recipient/value/timestamp of one transaction."""
        if self.backend == "blockscout":
            tx = self._v2(f"/transactions/{txid}", cacheable=True)
            if not tx or not tx.get("hash"):
                raise ProviderError(
                    f"blockscout: transaction not found ({txid})")
            timestamp = None
            if tx.get("timestamp"):
                try:
                    timestamp = int(datetime.fromisoformat(
                        tx["timestamp"].replace("Z", "+00:00")
                    ).astimezone(timezone.utc).timestamp())
                except ValueError:
                    timestamp = None
            return {
                "sender": ((tx.get("from") or {}).get("hash") or "").lower(),
                "destination": ((tx.get("to") or {}).get("hash")
                                or "").lower(),
                "value_wei": int(tx.get("value") or 0),
                "timestamp": timestamp,
            }
        if self.backend == "alchemy":
            result = self._rpc("eth_getTransactionByHash", [txid], True)
            if not result:
                raise ProviderError(f"alchemy: transaction not found ({txid})")
            timestamp = None
            block_number = result.get("blockNumber")
            if block_number:
                block = self._rpc("eth_getBlockByNumber",
                                  [block_number, False], True) or {}
                try:
                    timestamp = int(block.get("timestamp"), 16)
                except (TypeError, ValueError):
                    timestamp = None
            return {
                "sender": (result.get("from") or "").lower(),
                "destination": (result.get("to") or "").lower(),
                "value_wei": int(result.get("value", "0x0"), 16),
                "timestamp": timestamp,
            }
        result = self._call(True, module="proxy",
                            action="eth_getTransactionByHash", txhash=txid)
        if not result:
            raise ProviderError(f"etherscan: transaction not found ({txid})")
        return {
            "sender": (result.get("from") or "").lower(),
            "destination": (result.get("to") or "").lower(),
            "value_wei": int(result.get("value", "0x0"), 16),
            "timestamp": None,   # proxy call has no timestamp; the engine
                                 # backfills it from sibling transfers
        }

    def transaction_output_movements(self, txid: str) -> list:
        """Movements for a trace that starts from a transaction hash: the
        transaction's own value transfer plus any ERC-20 transfers it caused
        (found by querying the recipient's token history for that hash)."""
        head = self._transaction_head(txid)
        sender = head["sender"]
        destination = head["destination"]
        value_wei = head["value_wei"]

        movements = []
        if value_wei > 0 and destination:
            movements.append({
                "txid": txid,
                "timestamp": head["timestamp"],
                "from_address": sender,
                "to_address": destination,
                "asset": "ETH",
                "value": value_wei / config.WEI_PER_ETH,
                "value_raw": value_wei,
                "is_self_transfer": False,
            })

        # Token transfers triggered by this hash: search the sender's recent
        # token history for the same transaction hash.
        if sender and self.backend == "alchemy":
            for transfer in self._alchemy_transfers(sender, "from"):
                if (transfer.get("hash") or "").lower() != txid.lower():
                    continue
                movement = self._movement_from_transfer(transfer)
                if movement and movement.get("token_contract"):
                    movement["is_self_transfer"] = False
                    movements.append(movement)
            return movements
        if sender:
            token_txs = self._call(False, module="account", action="tokentx",
                                   address=sender, startblock=0,
                                   endblock=99999999, page=1,
                                   offset=ETHERSCAN_PAGE_SIZE, sort="desc") or []
            for tx in token_txs:
                if tx.get("hash", "").lower() != txid.lower():
                    continue
                contract = (tx.get("contractAddress") or "").lower()
                if contract in config.PRIORITY_ERC20_TOKENS:
                    symbol, decimals = config.PRIORITY_ERC20_TOKENS[contract]
                else:
                    symbol = tx.get("tokenSymbol") or "ERC20"
                    decimals = int(tx.get("tokenDecimal", 18))
                value_raw = int(tx.get("value", "0"))
                movements.append({
                    "txid": txid,
                    "timestamp": int(tx.get("timeStamp", 0)) or None,
                    "from_address": (tx.get("from") or "").lower(),
                    "to_address": (tx.get("to") or "").lower(),
                    "asset": symbol,
                    "value": value_raw / (10 ** decimals),
                    "value_raw": value_raw,
                    "token_contract": contract,
                    "is_self_transfer": False,
                })
        return movements
