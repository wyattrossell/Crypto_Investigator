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
                time.sleep(wait)
            ProviderClient._last_request_at[self.provider_name] = time.monotonic()

    # -- core fetch ---------------------------------------------------------

    def fetch(self, url: str, cacheable: bool = True,
              cache_key: str = None) -> bytes:
        """GET `url`, honouring memo/cache/throttle/retry/custody. Returns
        the raw response body. `cacheable=False` forces a live pull (e.g.
        for data that can still change, like an unconfirmed transaction)."""
        key = cache_key or url
        if self.memo is None:
            return self._fetch_uncoordinated(url, cacheable, key)

        outcome, value = self.memo.claim(key)
        if outcome == "ok":
            return value
        if outcome == "err":
            raise value
        try:
            body = self._fetch_uncoordinated(url, cacheable, key)
        except Exception as exc:
            self.memo.resolve(key, "err", exc)
            raise
        self.memo.resolve(key, "ok", body)
        return body

    def _guarded_get(self, url: str):
        """client.get with a HARD wall-clock deadline. Python's DNS
        resolution (getaddrinfo) has no timeout, so a wedged OS resolver
        can block a plain get() forever - observed in the wild freezing a
        trace at hop 0. The request runs in a daemon worker; on deadline
        the worker is abandoned (rare, bounded by the retry cap) and the
        caller sees an ordinary timeout that the retry loop handles."""
        outcome = {}

        def worker():
            try:
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
                             key: str) -> bytes:
        """The actual cache/throttle/retry/custody pipeline for one GET."""
        if cacheable:
            cached = database.cache_get(key)
            if cached is not None:
                body, sha256 = cached
                database.custody_log(self.provider_name, url, None, sha256,
                                     from_cache=True, trace_id=self.trace_id)
                return body

        last_error = None
        for attempt in range(config.MAX_RETRIES):
            self._throttle()
            try:
                response = self._guarded_get(url)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                time.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))
                continue

            if response.status_code == 200:
                body = response.content
                sha256 = hashlib.sha256(body).hexdigest()
                if cacheable:
                    database.cache_put(key, body, sha256)
                database.custody_log(self.provider_name, url,
                                     response.status_code, sha256,
                                     from_cache=False, trace_id=self.trace_id)
                return body

            # 404 is a definitive answer (address/tx does not exist) - do not
            # retry, but still log the attempt for completeness.
            if response.status_code == 404:
                database.custody_log(self.provider_name, url, 404, None,
                                     from_cache=False, trace_id=self.trace_id)
                raise ProviderError(f"{self.provider_name}: not found ({url})")

            # 429 / 5xx: back off and retry.
            last_error = f"HTTP {response.status_code}"
            time.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))

        database.custody_log(self.provider_name, url, None, None,
                             from_cache=False, trace_id=self.trace_id)
        raise ProviderError(
            f"{self.provider_name}: giving up after {config.MAX_RETRIES} "
            f"attempts ({last_error}) for {url}")

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()
