"""
Shared HTTP client for every block-explorer provider.

Responsibilities, in order, for each request:

1. CACHE   - immutable on-chain data is served from the local SQLite cache
             (keyed by the exact URL) and never re-fetched.
2. THROTTLE- a per-provider minimum interval between live requests keeps us
             politely inside free-tier rate limits.
3. RETRY   - 429/5xx responses trigger exponential backoff up to MAX_RETRIES.
4. CUSTODY - every acquisition (cache hit or live) is written to the
             chain-of-custody log with URL, UTC timestamp, HTTP status and
             the SHA-256 of the raw body.

Providers built on this class therefore inherit court-defensible provenance
for free.
"""

import hashlib
import threading
import time

import httpx

from app import config, database


class ProviderError(Exception):
    """Raised when a provider cannot return usable data after retries."""


class ProviderStats:
    """Process-wide, per-provider counters for the Data Sources panel and
    the per-trace data-source summary. Counts are facts about THIS run of
    the program (they reset on restart); the custody log remains the
    durable record of every pull."""

    FIELDS = ("requests", "cache_hits", "throttle_wait_s", "rate_limited",
              "server_errors", "network_errors", "failures", "not_found")

    _lock = threading.Lock()
    _by_provider: dict = {}

    @classmethod
    def bump(cls, provider: str, field: str, amount=1) -> None:
        with cls._lock:
            entry = cls._by_provider.setdefault(
                provider, {f: 0 for f in cls.FIELDS})
            entry[field] = entry.get(field, 0) + amount
            entry["last_activity_utc"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @classmethod
    def note_error(cls, provider: str, text: str) -> None:
        with cls._lock:
            entry = cls._by_provider.setdefault(
                provider, {f: 0 for f in cls.FIELDS})
            entry["last_error"] = text[:300]
            entry["last_error_utc"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @classmethod
    def snapshot(cls) -> dict:
        with cls._lock:
            return {k: dict(v) for k, v in cls._by_provider.items()}

    @classmethod
    def delta(cls, before: dict) -> dict:
        """Counters accumulated since `before` (a prior snapshot)."""
        now = cls.snapshot()
        out = {}
        for provider, entry in now.items():
            base = before.get(provider, {})
            diff = {f: entry.get(f, 0) - base.get(f, 0) for f in cls.FIELDS}
            if any(diff.values()):
                diff["throttle_wait_s"] = round(diff["throttle_wait_s"], 1)
                if entry.get("last_error") and \
                        entry.get("last_error_utc") != base.get("last_error_utc"):
                    diff["last_error"] = entry["last_error"]
                out[provider] = diff
        return out


class FetchMemo:
    """Per-trace, in-memory, single-flight store of fetch results.

    Shared between the tracing engine and its background prefetch workers so
    that each distinct request is issued AT MOST ONCE per trace, no matter
    which thread asks first. Later askers block until the first fetch
    finishes and then receive the same bytes (or the same failure) without a
    second network pull or a duplicate custody entry - the acquisition was
    already custody-logged when it actually happened.

    Side effect (documented in the changelog): within one trace, repeated
    reads of the same URL now produce ONE custody entry instead of several,
    and every consumer sees one consistent snapshot of mutable data (e.g. an
    address's transaction list) for the duration of the trace.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._done = {}     # key -> ("ok", bytes) | ("err", Exception)
        self._events = {}   # key -> Event set when the owner resolves it

    def claim(self, key: str) -> tuple:
        """Return ("owner", None) when the caller must perform the fetch
        itself; otherwise wait for the in-flight owner and return the stored
        ("ok", body) or ("err", exception)."""
        with self._lock:
            if key in self._done:
                return self._done[key]
            event = self._events.get(key)
            if event is None:
                self._events[key] = threading.Event()
                return ("owner", None)
        event.wait()
        with self._lock:
            return self._done.get(
                key, ("err", ProviderError(f"memo: fetch abandoned ({key})")))

    def resolve(self, key: str, status: str, value) -> None:
        """Store the owner's result and wake every waiter."""
        with self._lock:
            self._done[key] = (status, value)
            event = self._events.pop(key, None)
        if event is not None:
            event.set()


class ProviderClient:
    """Base class wrapping httpx with cache + throttle + retry + custody."""

    # Subclasses set this to the provider's display name; it keys the
    # rate-limit table and appears in the custody log.
    provider_name = "generic"

    # Class-level lock table so all instances of the same provider share one
    # throttle (traces run in a background thread; settings calls elsewhere).
    _throttle_locks: dict = {}
    _last_request_at: dict = {}
    _table_guard = threading.Lock()

    def __init__(self, trace_id=None, memo: "FetchMemo" = None):
        # trace_id ties custody entries to a specific investigation trace.
        self.trace_id = trace_id
        # Optional per-trace single-flight memo (see FetchMemo above).
        self.memo = memo
        self._client = httpx.Client(
            timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"},
            # treasury.gov (and others) 302-redirect to their current file
            # hosts; the custody log records the URL we requested.
            follow_redirects=True,
        )

    # -- throttling ---------------------------------------------------------

    def _throttle(self) -> None:
        """Block until this provider's minimum request interval has elapsed."""
        with ProviderClient._table_guard:
            lock = ProviderClient._throttle_locks.setdefault(
                self.provider_name, threading.Lock())
        interval = config.MIN_REQUEST_INTERVAL_SECONDS.get(
            self.provider_name, config.DEFAULT_MIN_REQUEST_INTERVAL)
        with lock:
            last = ProviderClient._last_request_at.get(self.provider_name, 0.0)
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                ProviderStats.bump(self.provider_name, "throttle_wait_s", wait)
                time.sleep(wait)
            ProviderClient._last_request_at[self.provider_name] = time.monotonic()

    # -- core fetch ---------------------------------------------------------

    def fetch(self, url: str, cacheable: bool = True,
              cache_key: str = None, json_body: dict = None,
              log_url: str = None) -> bytes:
        """GET `url` (or POST `json_body` to it), honouring memo/cache/
        throttle/retry/custody. Returns the raw response body.
        `cacheable=False` forces a live pull (e.g. for data that can still
        change, like an unconfirmed transaction). For POST (JSON-RPC)
        providers, `log_url` is the re-runnable, key-free descriptor that
        goes into the custody log and doubles as the cache key."""
        key = cache_key or log_url or url
        if self.memo is None:
            return self._fetch_uncoordinated(url, cacheable, key,
                                             json_body, log_url)

        outcome, value = self.memo.claim(key)
        if outcome == "ok":
            return value
        if outcome == "err":
            raise value
        try:
            body = self._fetch_uncoordinated(url, cacheable, key,
                                             json_body, log_url)
        except Exception as exc:
            self.memo.resolve(key, "err", exc)
            raise
        self.memo.resolve(key, "ok", body)
        return body

    def _guarded_get(self, url: str, json_body: dict = None):
        """client.get with a HARD wall-clock deadline. Python's DNS
        resolution (getaddrinfo) has no timeout, so a wedged OS resolver
        can block a plain get() forever - observed in the wild freezing a
        trace at hop 0. The request runs in a daemon worker; on deadline
        the worker is abandoned (rare, bounded by the retry cap) and the
        caller sees an ordinary timeout that the retry loop handles."""
        outcome = {}

        def worker():
            try:
                if json_body is not None:
                    outcome["response"] = self._client.post(url, json=json_body)
                else:
                    outcome["response"] = self._client.get(url)
            except Exception as exc:      # re-raised on the caller side
                outcome["error"] = exc

        thread = threading.Thread(target=worker, daemon=True,
                                  name=f"{self.provider_name}-fetch")
        thread.start()
        thread.join(config.HTTP_TIMEOUT_SECONDS + 15)
        if thread.is_alive():
            raise httpx.ConnectTimeout(
                f"hard deadline exceeded (hung DNS/connect) for {url}")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["response"]

    def _fetch_uncoordinated(self, url: str, cacheable: bool,
                             key: str, json_body: dict = None,
                             log_url: str = None) -> bytes:
        """The actual cache/throttle/retry/custody pipeline for one
        request. The custody log records `log_url` when given (POST
        providers) so keys embedded in URLs never reach the log."""
        logged = log_url or url
        if cacheable:
            cached = database.cache_get(key)
            if cached is not None:
                body, sha256 = cached
                ProviderStats.bump(self.provider_name, "cache_hits")
                database.custody_log(self.provider_name, logged, None, sha256,
                                     from_cache=True, trace_id=self.trace_id)
                return body

        last_error = None
        for attempt in range(config.MAX_RETRIES):
            self._throttle()
            ProviderStats.bump(self.provider_name, "requests")
            try:
                response = self._guarded_get(url, json_body)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                ProviderStats.bump(self.provider_name, "network_errors")
                time.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))
                continue

            if response.status_code == 200:
                body = response.content
                sha256 = hashlib.sha256(body).hexdigest()
                if cacheable:
                    database.cache_put(key, body, sha256)
                database.custody_log(self.provider_name, logged,
                                     response.status_code, sha256,
                                     from_cache=False, trace_id=self.trace_id)
                return body

            # 404 is a definitive answer (address/tx does not exist) - do not
            # retry, but still log the attempt for completeness.
            if response.status_code == 404:
                ProviderStats.bump(self.provider_name, "not_found")
                database.custody_log(self.provider_name, logged, 404, None,
                                     from_cache=False, trace_id=self.trace_id)
                raise ProviderError(f"{self.provider_name}: not found ({logged})")

            # 429 / 5xx: back off and retry.
            last_error = f"HTTP {response.status_code}"
            ProviderStats.bump(self.provider_name,
                               "rate_limited" if response.status_code == 429
                               else "server_errors")
            time.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))

        ProviderStats.bump(self.provider_name, "failures")
        ProviderStats.note_error(self.provider_name,
                                 f"{last_error} for {logged}")
        database.custody_log(self.provider_name, logged, None, None,
                             from_cache=False, trace_id=self.trace_id)
        raise ProviderError(
            f"{self.provider_name}: giving up after {config.MAX_RETRIES} "
            f"attempts ({last_error}) for {logged}")

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()
