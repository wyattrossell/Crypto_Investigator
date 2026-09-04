# Crypto Investigator — Setup Guide (for agency IT)

This guide is for the person who installs and maintains Crypto Investigator
on an agency workstation. The investigator's day-to-day guide is
[investigator-guide.md](investigator-guide.md).

## 1. What you are installing

Crypto Investigator is a **local** program. It runs a small web server on
the workstation itself (`127.0.0.1:8321`), opens the user's default browser
to it, and sits in the notification area (system tray) while it runs.
Nothing listens on the network; nothing is uploaded anywhere. The only
outbound traffic is the block-explorer, sanctions-list and label-list
requests the tool makes on the investigator's behalf, and (only if the
agency turns it on) requests to an AI provider.

| Item | Detail |
|---|---|
| Operating system | Windows 10 or 11, 64-bit (installed build). Source checkout runs on any OS with Python 3.10+. |
| Browser | Any current Edge, Chrome or Firefox. |
| Disk | ~150 MB for the program; the case database grows with use (label lists add ~250 MB). |
| Memory | 1 GB free is ample. |
| Privileges | **None required.** The installer defaults to a per-user install under `%LOCALAPPDATA%\Programs`. An all-users install under Program Files is offered when run elevated. |
| Network | Outbound HTTPS (443) to the hosts in section 5. No inbound rules. |

## 2. Install

### Option A — installer (recommended)

1. Run `CryptoInvestigator-Setup-v<version>.exe`.
2. Accept the defaults. A Start-menu entry (and optionally a desktop
   shortcut) is created.
3. First launch opens the browser automatically.

Windows SmartScreen may warn that the publisher is unknown: the build is
not code-signed. Choose *More info → Run anyway*, or sign the executable
with your agency's certificate (section 9).

### Option B — portable zip

Unzip `CryptoInvestigator-v<version>-portable.zip` anywhere the user can
write to, and run `CryptoInvestigator\CryptoInvestigator.exe`. Nothing is
registered with Windows; delete the folder to remove it.

### Option C — from source

```
python -m pip install -r requirements.txt
python run.py            # with a console (log output visible)
pythonw run.pyw          # no console window
powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1   # desktop shortcut
```

## 3. Where data lives

| Build | Data folder |
|---|---|
| Installed / portable | `%LOCALAPPDATA%\CryptoInvestigator\data` (per Windows user) |
| Source checkout | `<checkout>\data` |

Contents: `investigator.db` (SQLite: cases, traces, the evidence cache,
the chain-of-custody log, labels, flags, watches), `reports\` (generated
PDFs and ZIPs), `logs\crypto_investigator.log` (rotating program log),
and `labels\` — three **agency-editable** JSON files copied there on first
run and never overwritten by upgrades:

- `exchange_compliance.json` — the agency's compliant / non-compliant
  exchange designations (FATF-referenced, agency-owned).
- `custodian_contacts.json` — the legal-process directory (portals,
  emails, guidelines) used by freeze/preservation letters.
- `exchange_labels_seed.json` — the bundled seed exchange list.

The location is shown in *Settings*. To keep evidence elsewhere (an
encrypted volume, a case share), create a one-line text file named
`data-location.txt` containing the folder path, next to
`CryptoInvestigator.exe` (or next to `run.py`), **or** set the environment
variable `CRYPTO_INVESTIGATOR_DATA`. Restart the program afterwards.

**Backup:** copy the whole data folder while the program is closed. The
database is a standard SQLite file; `investigator.db-wal` and `-shm` must be
copied with it if present.

**Uninstall** removes the program only. The data folder is deliberately
left in place — evidence is not something an uninstaller should delete.

## 4. First-run configuration

Everything works with **no accounts** on free public APIs. Do these once,
from *Settings* (⚙ in the header):

1. **Label downloads** — press each once. They refresh themselves
   afterwards (weekly / monthly).
   - OFAC sanctions list (U.S. Treasury; official).
   - Exchange label packs (GraphSense TagPacks; ~337k addresses).
   - Scam blacklist (ScamSniffer; Ethereum drainers).
   - Ethereum name tags (eth-labels; ~87k Etherscan tags).
2. **Agency letterhead** — name, unit, address, officer details. Used on
   DRAFT freeze/preservation letters and on exported flag packs.
3. **Optional keys** (section 6) — only to raise rate limits or add
   capability.

The start screen shows a *Getting started* checklist that tracks these.

## 5. Network requirements (outbound HTTPS only)

Allow the workstation to reach these hosts. None is contacted until the
investigator does something that needs it.

| Purpose | Hosts |
|---|---|
| Bitcoin (keyless pool) | `mempool.space`, `blockstream.info`, `mempool.emzy.de` |
| Bitcoin (keyed mode) | `enterprise.blockstream.info`, `login.blockstream.com` |
| Ethereum (keyless) | `eth.blockscout.com`, `api.routescan.io` |
| Ethereum (keyed) | `api.etherscan.io`, `eth-mainnet.g.alchemy.com` |
| Tron | `api.trongrid.io` |
| USD prices | `api.coingecko.com`, `api.kraken.com`, `api.exchange.coinbase.com` |
| Sanctions & labels | `www.treasury.gov`, `api.github.com`, `raw.githubusercontent.com` |
| Scam reports (on demand) | `api.chainabuse.com` |
| AI assistant (off by default) | the provider the agency configures, or a local model server |

The program never phones home: there is no telemetry, update check or
crash reporting.

## 6. API keys and data-source modes

All keys are optional. Each agency registers its own (provider terms tie
keys to an organisation). Keys are stored encrypted with Windows DPAPI
(user-scoped): a database opened under a different Windows account shows
them as empty and they must be re-entered.

| Key / mode | Effect | Where to get it |
|---|---|---|
| Alchemy (Ethereum) | 25 req/s and **internal transactions**; preferred automatically when set | dashboard.alchemy.com (free: 30M CU/month) |
| Etherscan (Ethereum) | 3 req/s, 100k/day; paid plans from $49/month | etherscan.io/apis |
| Blockstream Explorer API (Bitcoin, mode *keyed*) | Dedicated quota, 500k requests/month free | help.blockstream.com → Blockstream Explorer API (client ID + secret) |
| Self-hosted node (Bitcoin mode *custom* / Blockscout URL) | Unlimited, fully private | Your own Esplora/mempool or Blockscout instance |
| TronGrid | 15 req/s instead of 1 | trongrid.io (free key) |
| CoinGecko demo | Reliable USD pricing | coingecko.com/en/api (free) |
| Chainabuse | Public scam-report lookups (10/month free; LE partner tier free for verified agencies) | chainabuse.com; partner tier via chainabuse.com/partner-contact |

*Settings → Data sources* shows every source, its tier, whether it is
active, and live counters (pulls, cache hits, throttle waits, rate-limit
retries, failures) — the first place to look when traces feel slow.

## 7. Security notes for reviewers

- Binds to `127.0.0.1` only; the port is fixed at 8321. A second copy
  started while one is running just reopens the browser tab.
- No authentication is needed because nothing is reachable from the
  network; workstation login is the access control.
- Secrets (API keys, the Blockstream client secret) are DPAPI-encrypted
  at rest and never appear in the chain-of-custody log, the program log
  or exported files.
- Every network acquisition is written to the custody log with URL, UTC
  timestamp, HTTP status and SHA-256 of the raw body. Immutable
  blockchain records are cached forever in the database and served from
  that evidence copy on repeat requests.
- The AI assistant is **off by default**. When enabled, trace data goes
  to the configured provider; a local model server keeps it on the
  machine. Every AI exchange is stored in full in an audit log.
- Dependencies are deliberately few (FastAPI, uvicorn, httpx, reportlab,
  pyyaml, pystray, Pillow). The complete source of every release is in
  `docs/listings/`, hashed in the Technical Validation Document.

## 8. Upgrading

Run the new installer over the old one (it asks the running program to
quit first), or replace the portable folder. The database schema updates
itself on first start; case data and the editable label files are kept.
Read `CHANGELOG.md` for what changed and each version's known limitations.

## 9. Building a release yourself

Needed only if the agency wants to audit or modify the source.

```
python -m pip install -r requirements.txt -r requirements-build.txt
python tools/build_release.py
```

This freezes the program with PyInstaller into `dist\CryptoInvestigator\`,
writes the portable zip, and — when Inno Setup 6 (free,
jrsoftware.org/isinfo.php) is installed — the `Setup.exe`. To code-sign,
sign `dist\CryptoInvestigator\CryptoInvestigator.exe` before running the
Inno step, then sign the setup file.

Release documentation is regenerated with `python tools/make_docs.py`
(program listing + Technical Validation Document + Overview PDF).

## 10. Troubleshooting

| Symptom | What to check |
|---|---|
| Nothing happens on launch | The tray icon may already be there (the program was running). Right-click it → *Open*. |
| "Port 8321 in use" message | Another program holds the port. Close it; the port is not configurable in this version. |
| Browser shows an old-looking UI after an upgrade | Press Ctrl+F5. The server sends no-cache headers, but some browsers keep a stale tab. |
| Traces are slow / "rate-limited" in Data sources | Public APIs are shared-IP throttled. Add a key (section 6) or switch to a keyed / self-hosted mode. |
| Keys show as empty after a profile change | DPAPI is per Windows user. Re-enter the keys. |
| A trace shows "interrupted" | The program was closed mid-trace. Run it again; cached data makes the re-run faster. |
| Where are the logs? | `<data folder>\logs\crypto_investigator.log` |

## 11. Support

Issues and questions: https://github.com/wyattrossell/Crypto_Investigator/issues
