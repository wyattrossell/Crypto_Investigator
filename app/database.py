"""
SQLite persistence layer.

Three concerns share one local database file:

1. Case management        (cases, traces, annotations)
2. Immutable HTTP cache   (http_cache) - confirmed on-chain data never changes,
                          so identical requests are served from disk forever.
3. Chain of custody       (custody_log) - every network pull is recorded with
                          its exact URL, UTC timestamp and SHA-256 of the raw
                          response so any analyst can re-run and verify it.

SQLite is used in autocommit mode with a short busy timeout; the app is
single-user/localhost so contention is minimal.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from app import config

# One connection per thread (uvicorn worker threads + background trace thread).
_thread_local = threading.local()

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cases (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        case_number TEXT,
        notes       TEXT DEFAULT '',
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS traces (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id       INTEGER NOT NULL REFERENCES cases(id),
        chain         TEXT NOT NULL,
        start_input   TEXT NOT NULL,   -- address or txid as typed by the user
        params_json   TEXT NOT NULL,   -- depth, dust threshold, etc.
        status        TEXT NOT NULL,   -- queued | running | finished | failed
        progress_note TEXT DEFAULT '',
        result_json   TEXT,            -- full graph + exits when finished
        error         TEXT,
        started_utc   TEXT NOT NULL,
        finished_utc  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS annotations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id     INTEGER NOT NULL REFERENCES cases(id),
        address     TEXT NOT NULL,
        chain       TEXT NOT NULL,
        role        TEXT,              -- investigator-assigned role tag
        note        TEXT DEFAULT '',
        updated_utc TEXT NOT NULL,
        UNIQUE (case_id, address, chain)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS http_cache (
        cache_key   TEXT PRIMARY KEY,  -- provider-defined, e.g. full URL
        body        BLOB NOT NULL,     -- raw response bytes (the evidence)
        sha256      TEXT NOT NULL,
        fetched_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS custody_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id    INTEGER,           -- NULL for pulls outside a trace
        source      TEXT NOT NULL,     -- provider name, e.g. 'mempool.space'
        url         TEXT NOT NULL,     -- exact request (re-runnable)
        http_status INTEGER,
        sha256      TEXT,              -- hash of the raw response body
        from_cache  INTEGER NOT NULL DEFAULT 0,
        fetched_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ic3_drafts (
        case_id     INTEGER PRIMARY KEY REFERENCES cases(id),
        data_json   TEXT NOT NULL,     -- IC3 worksheet answers (one per case)
        updated_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watches (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        address       TEXT NOT NULL,   -- normalised (lowercase for EVM)
        chain         TEXT NOT NULL,
        case_id       INTEGER REFERENCES cases(id),
        note          TEXT DEFAULT '',
        baseline_json TEXT,            -- last observed activity snapshot
        last_checked_utc TEXT,
        alert_json    TEXT,            -- movement details when detected
        alert_acknowledged INTEGER NOT NULL DEFAULT 0,
        created_utc   TEXT NOT NULL,
        UNIQUE (address, chain)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallet_flags (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        address     TEXT NOT NULL,     -- normalised (lowercase for EVM)
        chain       TEXT NOT NULL,
        reason      TEXT DEFAULT '',   -- why the agency flagged it
        case_id     INTEGER REFERENCES cases(id),  -- originating case (opt.)
        created_utc TEXT NOT NULL,
        UNIQUE (address, chain)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        created_utc   TEXT NOT NULL,
        provider      TEXT,              -- anthropic | openai | custom
        model         TEXT,
        purpose       TEXT,              -- trace_summary | trace_question |...
        trace_id      INTEGER,
        case_id       INTEGER,
        request_json  TEXT,              -- FULL prompt sent (audit record)
        response_text TEXT,              -- FULL response received
        duration_ms   INTEGER,
        error         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intel_lookups (
        provider     TEXT NOT NULL,     -- 'chainabuse'
        address      TEXT NOT NULL,     -- normalised
        chain        TEXT NOT NULL,
        fetched_utc  TEXT NOT NULL,
        report_count INTEGER NOT NULL DEFAULT 0,
        payload_json TEXT NOT NULL,     -- parsed, trimmed response
        PRIMARY KEY (provider, address, chain)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_usage (
        provider TEXT NOT NULL,
        period   TEXT NOT NULL,         -- 'YYYY-MM' (monthly budgets)
        count    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (provider, period)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS label_packs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source       TEXT NOT NULL UNIQUE,  -- labels.source key ('pack:...')
        agency       TEXT NOT NULL,
        contact      TEXT DEFAULT '',
        exported_utc TEXT,
        imported_utc TEXT NOT NULL,
        sha256       TEXT NOT NULL,
        count        INTEGER NOT NULL,
        note         TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS triage_runs (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id       INTEGER REFERENCES cases(id),
        created_utc   TEXT NOT NULL,
        params_json   TEXT NOT NULL,
        status        TEXT NOT NULL,   -- running | finished | failed
        progress_note TEXT DEFAULT '',
        result_json   TEXT,
        error         TEXT,
        finished_utc  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS labels (
        address     TEXT NOT NULL,     -- normalised (lowercase for EVM)
        chain       TEXT NOT NULL,
        entity_name TEXT NOT NULL,     -- e.g. 'Binance', 'OFAC SDN: Lazarus'
        category    TEXT NOT NULL,     -- exchange | sanctioned | mixer | other
        source      TEXT NOT NULL,     -- 'ofac_sdn' | 'seed_community' | ...
        confidence  TEXT NOT NULL,     -- high | medium | low
        updated_utc TEXT NOT NULL,
        PRIMARY KEY (address, chain, source)
    )
    """,
]


def utc_now_iso() -> str:
    """Current UTC time in ISO-8601 with explicit offset (court-friendly)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    """Return this thread's SQLite connection, creating it on first use."""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DATABASE_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _thread_local.conn = conn
    return conn


def initialise_database() -> None:
    """Create all tables if they do not exist. Safe to call repeatedly."""
    conn = get_connection()
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()


# ---------------------------------------------------------------------------
# Settings helpers (API keys, endpoint overrides)
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    """Read one setting; returns `default` when unset. Secret settings
    (API keys) are decrypted transparently."""
    row = get_connection().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    if key in config.SECRET_SETTING_KEYS:
        from app import secretstore
        return secretstore.unprotect(row["value"])
    return row["value"]


def set_setting(key: str, value: str) -> None:
    """Insert or update one setting. Secret settings (API keys) are
    encrypted at rest with Windows DPAPI."""
    if key in config.SECRET_SETTING_KEYS:
        from app import secretstore
        value = secretstore.protect(value)
    conn = get_connection()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Immutable HTTP cache
# ---------------------------------------------------------------------------

def cache_get(cache_key: str):
    """Return (body_bytes, sha256) for a cached response, or None."""
    row = get_connection().execute(
        "SELECT body, sha256 FROM http_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    return (row["body"], row["sha256"]) if row else None


def cache_put(cache_key: str, body: bytes, sha256: str) -> None:
    """Store a raw response. Existing entries are never overwritten
    (on-chain data is immutable; first capture is the evidential copy)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO http_cache (cache_key, body, sha256, fetched_utc) "
        "VALUES (?, ?, ?, ?)",
        (cache_key, body, sha256, utc_now_iso()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Chain-of-custody log
# ---------------------------------------------------------------------------

def custody_log(source: str, url: str, http_status, sha256, from_cache: bool,
                trace_id=None) -> None:
    """Record one data acquisition event."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO custody_log "
        "(trace_id, source, url, http_status, sha256, from_cache, fetched_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trace_id, source, url, http_status, sha256,
         1 if from_cache else 0, utc_now_iso()),
    )
    conn.commit()


def custody_entries_for_trace(trace_id: int) -> list:
    """All custody entries for one trace, oldest first (for the report)."""
    rows = get_connection().execute(
        "SELECT * FROM custody_log WHERE trace_id = ? ORDER BY id", (trace_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Labels (OFAC + community attribution)
# ---------------------------------------------------------------------------

def labels_replace_source(source: str, rows: list) -> None:
    """Atomically replace every label from one source.
    `rows` items: (address, chain, entity_name, category, confidence)."""
    conn = get_connection()
    now = utc_now_iso()
    with conn:
        conn.execute("DELETE FROM labels WHERE source = ?", (source,))
        conn.executemany(
            "INSERT OR REPLACE INTO labels "
            "(address, chain, entity_name, category, source, confidence, updated_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(a, c, e, cat, source, conf, now) for (a, c, e, cat, conf) in rows],
        )


def labels_lookup(address: str, chain: str) -> list:
    """All labels for one address (there may be several sources)."""
    rows = get_connection().execute(
        "SELECT entity_name, category, source, confidence FROM labels "
        "WHERE address = ? AND chain = ?",
        (address, chain),
    ).fetchall()
    return [dict(r) for r in rows]


def label_upsert(address: str, chain: str, entity_name: str,
                 category: str, source: str, confidence: str) -> None:
    """Insert or update ONE label (used by on-demand lookups)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO labels "
        "(address, chain, entity_name, category, source, confidence, "
        "updated_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (address, chain, entity_name, category, source, confidence,
         utc_now_iso()))
    conn.commit()


def label_delete(address: str, chain: str, source: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM labels WHERE address = ? AND chain = ? "
                 "AND source = ?", (address, chain, source))
    conn.commit()


def labels_delete_source(source: str) -> int:
    conn = get_connection()
    cur = conn.execute("DELETE FROM labels WHERE source = ?", (source,))
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# On-demand intelligence lookups (cache + monthly API budgets)
# ---------------------------------------------------------------------------

def intel_cache_get(provider: str, address: str, chain: str):
    row = get_connection().execute(
        "SELECT * FROM intel_lookups WHERE provider = ? AND address = ? "
        "AND chain = ?", (provider, address, chain)).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["payload"] = json.loads(record.pop("payload_json") or "{}")
    return record


def intel_cache_put(provider: str, address: str, chain: str,
                    report_count: int, payload: dict) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO intel_lookups "
        "(provider, address, chain, fetched_utc, report_count, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (provider, address, chain, utc_now_iso(), report_count,
         json.dumps(payload)))
    conn.commit()


def intel_cache_count(provider: str) -> int:
    row = get_connection().execute(
        "SELECT COUNT(*) AS n FROM intel_lookups WHERE provider = ?",
        (provider,)).fetchone()
    return row["n"] if row else 0


def api_usage_get(provider: str, period: str) -> int:
    row = get_connection().execute(
        "SELECT count FROM api_usage WHERE provider = ? AND period = ?",
        (provider, period)).fetchone()
    return row["count"] if row else 0


def api_usage_increment(provider: str, period: str) -> int:
    conn = get_connection()
    conn.execute(
        "INSERT INTO api_usage (provider, period, count) VALUES (?, ?, 1) "
        "ON CONFLICT(provider, period) DO UPDATE SET count = count + 1",
        (provider, period))
    conn.commit()
    return api_usage_get(provider, period)


# ---------------------------------------------------------------------------
# Imported flag packs (another agency's designations, kept separate)
# ---------------------------------------------------------------------------

def label_pack_add(source: str, agency: str, contact: str,
                   exported_utc, sha256: str, count: int,
                   note: str = "") -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT OR REPLACE INTO label_packs (source, agency, contact, "
        "exported_utc, imported_utc, sha256, count, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source, agency, contact, exported_utc, utc_now_iso(), sha256,
         count, note))
    conn.commit()
    return cur.lastrowid


def label_packs_list() -> list:
    rows = get_connection().execute(
        "SELECT * FROM label_packs ORDER BY imported_utc DESC").fetchall()
    return [dict(r) for r in rows]


def label_pack_get(pack_id: int):
    row = get_connection().execute(
        "SELECT * FROM label_packs WHERE id = ?", (pack_id,)).fetchone()
    return dict(row) if row else None


def label_pack_remove(pack_id: int) -> bool:
    pack = label_pack_get(pack_id)
    if pack is None:
        return False
    labels_delete_source(pack["source"])
    conn = get_connection()
    conn.execute("DELETE FROM label_packs WHERE id = ?", (pack_id,))
    conn.commit()
    return True


def label_pack_by_agency(agency: str):
    row = get_connection().execute(
        "SELECT * FROM label_packs WHERE lower(agency) = lower(?)",
        (agency,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Bulk triage runs
# ---------------------------------------------------------------------------

def triage_create(case_id, params: dict) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO triage_runs (case_id, created_utc, params_json, status) "
        "VALUES (?, ?, ?, 'running')",
        (case_id, utc_now_iso(), json.dumps(params)))
    conn.commit()
    return cur.lastrowid


def triage_update(run_id: int, status: str, progress_note: str = "",
                  result: dict = None, error: str = None) -> None:
    conn = get_connection()
    finished = utc_now_iso() if status in ("finished", "failed") else None
    conn.execute(
        "UPDATE triage_runs SET status = ?, progress_note = ?, "
        "result_json = COALESCE(?, result_json), error = ?, "
        "finished_utc = COALESCE(?, finished_utc) WHERE id = ?",
        (status, progress_note,
         json.dumps(result) if result is not None else None,
         error, finished, run_id))
    conn.commit()


def triage_get(run_id: int):
    row = get_connection().execute(
        "SELECT * FROM triage_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def triage_list(limit: int = 20) -> list:
    rows = get_connection().execute(
        "SELECT t.id, t.case_id, t.created_utc, t.status, t.finished_utc, "
        "t.params_json, c.name AS case_name FROM triage_runs t "
        "LEFT JOIN cases c ON c.id = t.case_id "
        "ORDER BY t.id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def labels_count_by_source() -> dict:
    """{source: count} - shown in the UI so the user knows what's loaded."""
    rows = get_connection().execute(
        "SELECT source, COUNT(*) AS n FROM labels GROUP BY source"
    ).fetchall()
    return {r["source"]: r["n"] for r in rows}


def labels_freshness() -> dict:
    """{source: last updated_utc} - drives staleness hints and the
    auto-refresh scheduler (only sources already loaded appear)."""
    rows = get_connection().execute(
        "SELECT source, MAX(updated_utc) AS last FROM labels GROUP BY source"
    ).fetchall()
    return {r["source"]: r["last"] for r in rows}


# ---------------------------------------------------------------------------
# Wallet flags (the agency's own fraud designations)
# ---------------------------------------------------------------------------

def flag_add(address: str, chain: str, reason: str, case_id=None) -> int:
    """Flag (or re-flag with a new reason) one wallet. Returns the row id."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO wallet_flags (address, chain, reason, case_id, "
        "created_utc) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(address, chain) DO UPDATE SET "
        "reason = excluded.reason, case_id = excluded.case_id, "
        "created_utc = excluded.created_utc",
        (address, chain, reason, case_id, utc_now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def flag_remove(address: str, chain: str) -> bool:
    """Remove one wallet flag. Returns True when a row was deleted."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM wallet_flags WHERE address = ? AND chain = ?",
        (address, chain),
    )
    conn.commit()
    return cur.rowcount > 0


def flags_list() -> list:
    """Every flagged wallet, newest first, with the originating case name."""
    rows = get_connection().execute(
        "SELECT f.*, c.name AS case_name, c.case_number "
        "FROM wallet_flags f LEFT JOIN cases c ON c.id = f.case_id "
        "ORDER BY f.id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def flag_lookup(address: str, chain: str):
    """The flag row for one wallet (with case name), or None."""
    row = get_connection().execute(
        "SELECT f.*, c.name AS case_name, c.case_number "
        "FROM wallet_flags f LEFT JOIN cases c ON c.id = f.case_id "
        "WHERE f.address = ? AND f.chain = ?",
        (address, chain),
    ).fetchone()
    return dict(row) if row else None


def flags_count() -> int:
    row = get_connection().execute(
        "SELECT COUNT(*) AS n FROM wallet_flags").fetchone()
    return row["n"]


# ---------------------------------------------------------------------------
# AI assistant audit log
# ---------------------------------------------------------------------------

def ai_log_add(provider: str, model: str, purpose: str, trace_id, case_id,
               request_json: str, response_text: str, duration_ms: int,
               error: str = None) -> None:
    """Record one AI interaction in full - the glass-box audit record."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO ai_log (created_utc, provider, model, purpose, "
        "trace_id, case_id, request_json, response_text, duration_ms, "
        "error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (utc_now_iso(), provider, model, purpose, trace_id, case_id,
         request_json, response_text, duration_ms, error),
    )
    conn.commit()


def ai_log_list(limit: int = 50) -> list:
    """Newest AI interactions (prompt/response sizes, not full bodies)."""
    rows = get_connection().execute(
        "SELECT id, created_utc, provider, model, purpose, trace_id, "
        "case_id, LENGTH(request_json) AS request_chars, "
        "LENGTH(response_text) AS response_chars, duration_ms, error "
        "FROM ai_log ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def ai_log_get(log_id: int):
    """One full AI interaction record (for the audit view)."""
    row = get_connection().execute(
        "SELECT * FROM ai_log WHERE id = ?", (log_id,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Wallet watches (movement alerts)
# ---------------------------------------------------------------------------

def watch_add(address: str, chain: str, note: str, case_id=None) -> int:
    """Watch (or re-note) one wallet. Returns the row id."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO watches (address, chain, note, case_id, created_utc) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(address, chain) DO UPDATE SET "
        "note = excluded.note, case_id = excluded.case_id",
        (address, chain, note, case_id, utc_now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def watch_remove(address: str, chain: str) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM watches WHERE address = ? AND chain = ?",
        (address, chain),
    )
    conn.commit()
    return cur.rowcount > 0


def watches_list() -> list:
    """Every watch, alerts first, then newest, with the case name."""
    rows = get_connection().execute(
        "SELECT w.*, c.name AS case_name, c.case_number "
        "FROM watches w LEFT JOIN cases c ON c.id = w.case_id "
        "ORDER BY (w.alert_json IS NOT NULL AND w.alert_acknowledged = 0) "
        "DESC, w.id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def watch_lookup(address: str, chain: str):
    row = get_connection().execute(
        "SELECT * FROM watches WHERE address = ? AND chain = ?",
        (address, chain),
    ).fetchone()
    return dict(row) if row else None


def watches_due(older_than_utc: str) -> list:
    """Watches never checked, or last checked before `older_than_utc`."""
    rows = get_connection().execute(
        "SELECT * FROM watches WHERE last_checked_utc IS NULL "
        "OR last_checked_utc < ? ORDER BY last_checked_utc",
        (older_than_utc,),
    ).fetchall()
    return [dict(r) for r in rows]


def watch_update_check(watch_id: int, baseline: dict,
                       alert: dict = None) -> None:
    """Record a completed check; sets/keeps the alert when movement was
    detected (an unacknowledged alert is never silently overwritten)."""
    conn = get_connection()
    if alert is not None:
        conn.execute(
            "UPDATE watches SET baseline_json = ?, last_checked_utc = ?, "
            "alert_json = ?, alert_acknowledged = 0 WHERE id = ?",
            (json.dumps(baseline), utc_now_iso(), json.dumps(alert),
             watch_id),
        )
    else:
        conn.execute(
            "UPDATE watches SET baseline_json = ?, last_checked_utc = ? "
            "WHERE id = ?",
            (json.dumps(baseline), utc_now_iso(), watch_id),
        )
    conn.commit()


def watch_acknowledge(watch_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE watches SET alert_acknowledged = 1 WHERE id = ?",
        (watch_id,),
    )
    conn.commit()


def watch_alert_count() -> int:
    row = get_connection().execute(
        "SELECT COUNT(*) AS n FROM watches "
        "WHERE alert_json IS NOT NULL AND alert_acknowledged = 0"
    ).fetchone()
    return row["n"]


# ---------------------------------------------------------------------------
# Annotations (per-address investigator notes, scoped to a case)
# ---------------------------------------------------------------------------

def annotation_set(case_id: int, address: str, chain: str,
                   note: str) -> None:
    """Save (or clear, when note is empty) one investigator note."""
    conn = get_connection()
    if note.strip():
        conn.execute(
            "INSERT INTO annotations (case_id, address, chain, note, "
            "updated_utc) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(case_id, address, chain) DO UPDATE SET "
            "note = excluded.note, updated_utc = excluded.updated_utc",
            (case_id, address, chain, note.strip(), utc_now_iso()),
        )
    else:
        conn.execute(
            "DELETE FROM annotations WHERE case_id = ? AND address = ? "
            "AND chain = ?", (case_id, address, chain),
        )
    conn.commit()


def annotations_for_case(case_id: int) -> list:
    rows = get_connection().execute(
        "SELECT address, chain, note, updated_utc FROM annotations "
        "WHERE case_id = ? ORDER BY updated_utc DESC", (case_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cases and traces
# ---------------------------------------------------------------------------

def create_case(name: str, case_number: str, notes: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO cases (name, case_number, notes, created_utc) "
        "VALUES (?, ?, ?, ?)",
        (name, case_number, notes, utc_now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def list_cases() -> list:
    rows = get_connection().execute(
        "SELECT * FROM cases ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_case(case_id: int):
    row = get_connection().execute(
        "SELECT * FROM cases WHERE id = ?", (case_id,)
    ).fetchone()
    return dict(row) if row else None


def create_trace(case_id: int, chain: str, start_input: str, params: dict) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO traces (case_id, chain, start_input, params_json, status, "
        "started_utc) VALUES (?, ?, ?, ?, 'queued', ?)",
        (case_id, chain, start_input, json.dumps(params), utc_now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def update_trace_status(trace_id: int, status: str, progress_note: str = "",
                        result: dict = None, error: str = None) -> None:
    """Move a trace through queued -> running -> finished/failed."""
    conn = get_connection()
    finished = utc_now_iso() if status in ("finished", "failed") else None
    conn.execute(
        "UPDATE traces SET status = ?, progress_note = ?, "
        "result_json = COALESCE(?, result_json), error = ?, "
        "finished_utc = COALESCE(?, finished_utc) WHERE id = ?",
        (status, progress_note,
         json.dumps(result) if result is not None else None,
         error, finished, trace_id),
    )
    conn.commit()


def fail_orphaned_triage_runs() -> int:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE triage_runs SET status = 'failed', "
        "error = 'Interrupted: the program was closed before this triage "
        "finished.', finished_utc = ? WHERE status = 'running'",
        (utc_now_iso(),))
    conn.commit()
    return cur.rowcount


def fail_orphaned_traces() -> int:
    """Mark traces left 'queued'/'running' by a previous process as failed.

    Traces run in threads of the server process, so at startup nothing can
    legitimately be in progress: any such row was interrupted by a shutdown
    (Quit, crash, power loss). Without this the trace shows as running
    forever. Returns the number of traces marked."""
    conn = get_connection()
    now = utc_now_iso()
    cursor = conn.execute(
        "UPDATE traces SET status = 'failed', "
        "error = COALESCE(NULLIF(error, ''), ?), "
        "progress_note = progress_note || ' (interrupted)', "
        "finished_utc = COALESCE(finished_utc, ?) "
        "WHERE status IN ('queued', 'running')",
        ("Interrupted: the program was closed before this trace finished. "
         "Run the trace again (cached data makes the re-run faster).", now),
    )
    conn.commit()
    return cursor.rowcount


def get_trace(trace_id: int):
    row = get_connection().execute(
        "SELECT * FROM traces WHERE id = ?", (trace_id,)
    ).fetchone()
    return dict(row) if row else None


def list_traces_for_case(case_id: int) -> list:
    rows = get_connection().execute(
        "SELECT id, chain, start_input, status, started_utc, finished_utc "
        "FROM traces WHERE case_id = ? ORDER BY id DESC",
        (case_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_finished_trace_for_case(case_id: int):
    """Most recent finished trace with a result, or None (IC3 prefill)."""
    row = get_connection().execute(
        "SELECT * FROM traces WHERE case_id = ? AND status = 'finished' "
        "AND result_json IS NOT NULL ORDER BY id DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Case export / import (agency continuity, cross-machine transfer)
# ---------------------------------------------------------------------------

def export_case(case_id: int) -> dict:
    """Everything belonging to one case as a portable JSON document."""
    conn = get_connection()
    case = get_case(case_id)
    traces = [dict(r) for r in conn.execute(
        "SELECT * FROM traces WHERE case_id = ? ORDER BY id",
        (case_id,)).fetchall()]
    trace_ids = [t["id"] for t in traces]
    custody = []
    if trace_ids:
        placeholders = ",".join("?" * len(trace_ids))
        custody = [dict(r) for r in conn.execute(
            f"SELECT * FROM custody_log WHERE trace_id IN ({placeholders}) "
            f"ORDER BY id", trace_ids).fetchall()]
    ic3 = ic3_draft_get(case_id)
    flags = [dict(r) for r in conn.execute(
        "SELECT * FROM wallet_flags WHERE case_id = ?",
        (case_id,)).fetchall()]
    watches = [dict(r) for r in conn.execute(
        "SELECT * FROM watches WHERE case_id = ?", (case_id,)).fetchall()]
    return {
        "format": "crypto-investigator-case",
        "format_version": 1,
        "exported_by": f"{config.APP_NAME} v{config.APP_VERSION}",
        "exported_utc": utc_now_iso(),
        "case": case,
        "traces": traces,
        "custody_log": custody,
        "ic3_draft": ic3,
        "annotations": annotations_for_case(case_id),
        "flags": flags,
        "watches": watches,
    }


def import_case(payload: dict) -> int:
    """Recreate an exported case (new ids); returns the new case id.
    Flags and watches are upserted globally (they are agency-wide) and
    re-linked to the imported case."""
    conn = get_connection()
    case = payload["case"]
    with conn:
        cur = conn.execute(
            "INSERT INTO cases (name, case_number, notes, created_utc) "
            "VALUES (?, ?, ?, ?)",
            (case.get("name", "Imported case"),
             case.get("case_number", ""),
             case.get("notes", ""),
             case.get("created_utc") or utc_now_iso()))
        new_case_id = cur.lastrowid
        trace_id_map = {}
        for trace in payload.get("traces", []):
            cur = conn.execute(
                "INSERT INTO traces (case_id, chain, start_input, "
                "params_json, status, progress_note, result_json, error, "
                "started_utc, finished_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_case_id, trace.get("chain", ""),
                 trace.get("start_input", ""),
                 trace.get("params_json", "{}"),
                 trace.get("status", "finished"),
                 trace.get("progress_note", ""),
                 trace.get("result_json"),
                 trace.get("error"),
                 trace.get("started_utc") or utc_now_iso(),
                 trace.get("finished_utc")))
            trace_id_map[trace["id"]] = cur.lastrowid
        for entry in payload.get("custody_log", []):
            conn.execute(
                "INSERT INTO custody_log (trace_id, source, url, "
                "http_status, sha256, from_cache, fetched_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trace_id_map.get(entry.get("trace_id")),
                 entry.get("source", ""), entry.get("url", ""),
                 entry.get("http_status"), entry.get("sha256"),
                 entry.get("from_cache", 0),
                 entry.get("fetched_utc") or utc_now_iso()))
        if payload.get("ic3_draft"):
            conn.execute(
                "INSERT INTO ic3_drafts (case_id, data_json, updated_utc) "
                "VALUES (?, ?, ?)",
                (new_case_id,
                 json.dumps(payload["ic3_draft"].get("data", {})),
                 payload["ic3_draft"].get("updated_utc")
                 or utc_now_iso()))
        for note in payload.get("annotations", []):
            conn.execute(
                "INSERT OR REPLACE INTO annotations (case_id, address, "
                "chain, role, note, updated_utc) "
                "VALUES (?, ?, ?, NULL, ?, ?)",
                (new_case_id, note.get("address", ""),
                 note.get("chain", ""), note.get("note", ""),
                 note.get("updated_utc") or utc_now_iso()))
        for flag in payload.get("flags", []):
            conn.execute(
                "INSERT INTO wallet_flags (address, chain, reason, "
                "case_id, created_utc) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(address, chain) DO UPDATE SET "
                "reason = excluded.reason",
                (flag.get("address", ""), flag.get("chain", ""),
                 flag.get("reason", ""), new_case_id,
                 flag.get("created_utc") or utc_now_iso()))
        for watch in payload.get("watches", []):
            conn.execute(
                "INSERT INTO watches (address, chain, case_id, note, "
                "created_utc) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(address, chain) DO UPDATE SET "
                "note = excluded.note",
                (watch.get("address", ""), watch.get("chain", ""),
                 new_case_id, watch.get("note", ""),
                 watch.get("created_utc") or utc_now_iso()))
    return new_case_id


# ---------------------------------------------------------------------------
# IC3 complaint drafts (one worksheet per case)
# ---------------------------------------------------------------------------

def ic3_draft_get(case_id: int):
    """The saved IC3 worksheet for a case, or None if never saved."""
    row = get_connection().execute(
        "SELECT data_json, updated_utc FROM ic3_drafts WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if row is None:
        return None
    return {"data": json.loads(row["data_json"]),
            "updated_utc": row["updated_utc"]}


def ic3_draft_save(case_id: int, data: dict) -> None:
    """Insert or update the IC3 worksheet for a case."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO ic3_drafts (case_id, data_json, updated_utc) "
        "VALUES (?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET "
        "data_json = excluded.data_json, updated_utc = excluded.updated_utc",
        (case_id, json.dumps(data), utc_now_iso()),
    )
    conn.commit()
