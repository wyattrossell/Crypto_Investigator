/*
 * Crypto Investigator front-end (vanilla JS, no build step).
 *
 * Flow: pick/create case -> paste starting point (auto-detected) ->
 * set depth -> launch trace -> poll status -> render exits + Cytoscape
 * graph -> download report/exports.
 */

"use strict";

// How often to poll a running trace, in milliseconds.
const TRACE_POLL_INTERVAL_MS = 1500;

// Node colours must match the CSS legend.
const ROLE_COLORS = {
  victim: "#1857b8",
  intermediary: "#8195a8",
  exchange: "#1e8a4c",
  sanctioned: "#c22525",
  mixer: "#5b2333",
  contract: "#7a3fa8",
  high_activity_service: "#d97a06",
  unexpanded: "#c3ccd6",
};

// Plain-language explanations shown when a node is clicked.
const ROLE_EXPLANATIONS = {
  victim: "Starting point you supplied.",
  intermediary: "Funds passed through this address. No label matched it.",
  exchange: "Attributed to a custodial exchange — this is where a subpoena " +
            "or warrant for account records can be served.",
  sanctioned: "This address is on the official OFAC sanctions list.",
  mixer: "A known mixing service. Mixers deliberately obscure the trail — " +
            "funds entering are flagged and NOT followed; no continuity is " +
            "guessed.",
  contract: "A smart contract (swap/bridge/other). Funds entering it are " +
            "flagged but NOT followed in this version.",
  high_activity_service: "Heuristic: so many transactions that it is " +
            "probably a business service, not a personal wallet. Not " +
            "expanded, to avoid mixing unrelated funds into your trace.",
  unexpanded: "Edge of the trace — not explored (depth or size limit). " +
            "You can start a new trace from this address.",
};

const $ = (id) => document.getElementById(id);

/** JSON fetch helper that surfaces API error details. */
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

const shortAddress = (a) =>
  a.length > 16 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a;

const EXPLORERS = {
  bitcoin: (a) => `https://mempool.space/address/${a}`,
  ethereum: (a) => `https://etherscan.io/address/${a}`,
  tron: (a) => `https://tronscan.org/#/address/${a}`,
};
const explorerFor = (chain, address) =>
  (EXPLORERS[chain] || EXPLORERS.bitcoin)(address);

/* ===================== startup ===================== */

let lastMeta = null;

async function refreshMeta() {
  const meta = await api("/api/meta");
  lastMeta = meta;
  $("version-badge").textContent = `v${meta.version}`;
  $("btn-quit").classList.toggle("hidden", !meta.can_shutdown);
  const ofacCount = meta.labels_loaded.ofac_sdn || 0;
  const exchangeCount = (meta.labels_loaded.seed_community || 0) +
    (meta.labels_loaded.graphsense_tagpack || 0);
  const scamCount = meta.labels_loaded.scamsniffer || 0;
  $("label-status").textContent =
    `Labels: ${exchangeCount.toLocaleString()} exchange/mixer · ` +
    `${ofacCount.toLocaleString()} OFAC · ` +
    `${scamCount.toLocaleString()} scam-list · ` +
    `${meta.flags_count || 0} flagged` +
    (ofacCount === 0 ? " — download lists in Settings!" : "");
  const badge = $("watch-alert-badge");
  if (meta.watch_alerts > 0) {
    badge.textContent = String(meta.watch_alerts);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
  return meta;
}

/* ===================== wallet flags ===================== */

let walletFlags = new Map();   // "chain|address" -> flag record

async function refreshFlags() {
  try {
    const flags = await api("/api/flags");
    walletFlags = new Map(flags.map((f) => [`${f.chain}|${f.address}`, f]));
  } catch (_) { /* flags are never worth blocking the UI over */ }
}

const flagFor = (chain, address) => walletFlags.get(`${chain}|${address}`);

async function addFlag(address, chain, reason, caseId) {
  await api("/api/flags", {
    method: "POST",
    body: JSON.stringify({ address, chain, reason, case_id: caseId || null }),
  });
  await refreshFlags();
  refreshMeta();
}

async function removeFlag(address, chain) {
  await api(`/api/flags?address=${encodeURIComponent(address)}` +
            `&chain=${encodeURIComponent(chain)}`, { method: "DELETE" });
  await refreshFlags();
  refreshMeta();
}

function renderFlagsList() {
  const container = $("flags-list");
  if (!walletFlags.size) {
    container.innerHTML = "<p class='muted'>No wallets flagged yet. Flag " +
      "one above, or from any address panel on the money-flow map.</p>";
    return;
  }
  const rows = [...walletFlags.values()].map((f) => `
    <tr><td><code>${f.address}</code></td><td>${f.chain}</td>
        <td>${f.reason || "—"}</td>
        <td>${f.case_name
          ? f.case_name + (f.case_number ? ` (${f.case_number})` : "")
          : "—"}</td>
        <td>${(f.created_utc || "").slice(0, 10)}</td>
        <td><button class="secondary small"
          data-unflag="${f.chain}|${f.address}">Remove</button></td></tr>`)
    .join("");
  container.innerHTML = `<table class="panel-table">
    <tr><th>Address</th><th>Chain</th><th>Reason</th><th>Case</th>
        <th>Flagged</th><th></th></tr>${rows}</table>`;
  container.querySelectorAll("[data-unflag]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const separator = btn.dataset.unflag.indexOf("|");
      const chain = btn.dataset.unflag.slice(0, separator);
      const address = btn.dataset.unflag.slice(separator + 1);
      await removeFlag(address, chain);
      renderFlagsList();
      if (cy) redrawGraph();
    });
  });
}

async function openFlags() {
  await refreshFlags();
  renderFlagsList();
  $("flag-add-status").textContent = "";
  $("flags-dialog").showModal();
}

/* DRAFT preservation/freeze request link for an address that received
   traced funds in the current trace (empty string otherwise). */
function freezeLinkHtml(address) {
  if (!currentTraceId || !currentResult) return "";
  const touched = (currentResult.edges || []).some(
    (e) => e.to_address === address ||
      (currentResult.direction === "backward" &&
       e.from_address === address));
  if (!touched) return "";
  return `<a class="button secondary small"
    href="/api/traces/${currentTraceId}/freeze-request.pdf?address=${
      encodeURIComponent(address)}"
    target="_blank"
    title="DRAFT preservation / asset-freeze request letter with the funding
transactions as Attachment A. Review by counsel/prosecutor before service.">
    Draft freeze/seizure request (PDF)</a>`;
}

async function refreshCases(selectId) {
  const cases = await api("/api/cases");
  const select = $("case-select");
  select.innerHTML = "";
  if (cases.length === 0) {
    select.append(new Option("— create a case below —", ""));
  }
  for (const c of cases) {
    const label = c.case_number ? `${c.name} (${c.case_number})` : c.name;
    select.append(new Option(label, c.id));
  }
  if (selectId) select.value = String(selectId);
}

/* ===================== price ticker + dust hints ===================== */

// Refresh the header ticker every minute (server caches upstream calls).
const TICKER_REFRESH_MS = 60000;
let spotPrices = null;

async function refreshTicker() {
  try {
    spotPrices = await api("/api/prices/spot");
    $("price-ticker").innerHTML =
      `<b>BTC</b> $${Number(spotPrices.BTC).toLocaleString()} · ` +
      `<b>ETH</b> $${Number(spotPrices.ETH).toLocaleString()}`;
  } catch (_) {
    // No prices is never an error worth interrupting an investigation for.
    $("price-ticker").textContent = "";
    spotPrices = null;
  }
  updateDustHints();
}

/** Show what the crypto dust thresholds are worth in dollars right now,
    so "ignore below 0.0001 BTC" is a meaningful choice. */
function updateDustHints() {
  const hint = (inputId, hintId, price) => {
    const amount = Number($(inputId).value);
    $(hintId).textContent =
      (price && isFinite(amount) && amount > 0)
        ? `≈ $${(amount * price).toLocaleString(undefined,
            { maximumFractionDigits: 2 })} at the current price`
        : "";
  };
  hint("dust-btc", "dust-btc-usd", spotPrices && spotPrices.BTC);
  hint("dust-eth", "dust-eth-usd", spotPrices && spotPrices.ETH);
}

/* ===================== wallet watches ===================== */

let walletWatches = new Map();   // "chain|address" -> watch record

async function refreshWatches() {
  try {
    const payload = await api("/api/watches");
    walletWatches = new Map(payload.watches.map(
      (w) => [`${w.chain}|${w.address}`, w]));
  } catch (_) { /* watches never block the UI */ }
}

const watchFor = (chain, address) => walletWatches.get(`${chain}|${address}`);

async function addWatch(address, chain, note, caseId) {
  await api("/api/watches", {
    method: "POST",
    body: JSON.stringify({ address, chain, note, case_id: caseId || null }),
  });
  await refreshWatches();
  refreshMeta();
}

async function removeWatch(address, chain) {
  await api(`/api/watches?address=${encodeURIComponent(address)}` +
            `&chain=${encodeURIComponent(chain)}`, { method: "DELETE" });
  await refreshWatches();
  refreshMeta();
}

function renderWatchesList() {
  const container = $("watches-list");
  if (!walletWatches.size) {
    container.innerHTML = "<p class='muted'>No wallets watched yet. Watch " +
      "one above, or from any address panel — FUNDS AT REST findings are " +
      "the natural candidates.</p>";
    return;
  }
  const rows = [...walletWatches.values()].map((w) => {
    const alertActive = w.alert && !w.alert_acknowledged;
    const status = alertActive
      ? `<div class="watch-alert">⚠ ${w.alert.summary}<br>
         <span class="muted">Detected ${w.alert.detected_utc}</span>
         <button class="secondary small" data-ack="${w.id}">
           Acknowledge</button></div>`
      : (w.last_checked_utc
          ? `<span class="muted">No movement. Last check
             ${w.last_checked_utc.replace("+00:00", "Z")}</span>`
          : "<span class='muted'>Not checked yet</span>");
    return `
    <div class="watch-row${alertActive ? " alerting" : ""}">
      <div><code>${w.address}</code> <span class="muted">(${w.chain})</span>
        ${w.note ? " — " + w.note : ""}
        ${w.case_name ? `<span class="muted"> · case ${w.case_name}</span>`
                      : ""}</div>
      <div>${status}</div>
      <div class="panel-actions">
        <button class="secondary small" data-retrace="${w.chain}|${w.address}"
          title="Fill the trace form with this address">Re-trace from
          here</button>
        <button class="secondary small" data-unwatch="${w.chain}|${w.address}">
          Stop watching</button>
      </div>
    </div>`;
  }).join("");
  container.innerHTML = rows;
  container.querySelectorAll("[data-ack]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/watches/${btn.dataset.ack}/acknowledge`,
                { method: "POST" });
      await refreshWatches();
      refreshMeta();
      renderWatchesList();
    });
  });
  container.querySelectorAll("[data-unwatch]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const separator = btn.dataset.unwatch.indexOf("|");
      await removeWatch(btn.dataset.unwatch.slice(separator + 1),
                        btn.dataset.unwatch.slice(0, separator));
      renderWatchesList();
    });
  });
  container.querySelectorAll("[data-retrace]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const separator = btn.dataset.retrace.indexOf("|");
      $("chain-select").value = btn.dataset.retrace.slice(0, separator);
      $("victim-input").value = btn.dataset.retrace.slice(separator + 1);
      $("focus-tx-input").value = "";
      $("watches-dialog").close();
      $("victim-input").dispatchEvent(new Event("input"));
      $("victim-input").scrollIntoView({ behavior: "smooth" });
    });
  });
}

async function openWatches() {
  await refreshWatches();
  renderWatchesList();
  $("watch-add-status").textContent = "";
  $("watches-dialog").showModal();
}

/* ===================== custodian directory ===================== */

let custodianDirectory = null;

async function openCustodians() {
  if (!custodianDirectory) {
    custodianDirectory = await api("/api/custodians");
  }
  $("custodians-note").textContent = custodianDirectory.source_note || "";
  renderCustodians("");
  $("custodians-dialog").showModal();
}

function renderCustodians(filter) {
  const needle = filter.trim().toLowerCase();
  const items = (custodianDirectory.custodians || []).filter((c) =>
    !needle ||
    `${c.entity} ${c.legal_name} ${c.jurisdiction} ${c.process_notes}`
      .toLowerCase().includes(needle));
  $("custodians-list").innerHTML = items.map((c) => `
    <div class="custodian-card">
      <b>${c.entity}</b> <span class="muted">— ${c.legal_name || ""}
        (${c.jurisdiction || "jurisdiction unknown"})</span><br>
      ${c.portal ? `Portal: <a href="${c.portal}" target="_blank">
        ${c.portal}</a><br>` : ""}
      ${c.email ? `Email: <code>${c.email}</code><br>` : ""}
      ${c.guidelines ? `Guidelines: <a href="${c.guidelines}"
        target="_blank">${c.guidelines}</a><br>` : ""}
      <span class="muted">${c.process_notes || ""}</span>
    </div>`).join("") ||
    "<p class='muted'>No custodian matches that filter. The directory is " +
    "agency-editable: data/labels/custodian_contacts.json.</p>";
}

/* ===================== annotations (investigator notes) ============== */

let caseAnnotations = new Map();   // "chain|address" -> note (current case)

async function refreshAnnotations() {
  caseAnnotations = new Map();
  const caseId = Number($("case-select").value);
  if (!caseId) return;
  try {
    const notes = await api(`/api/cases/${caseId}/annotations`);
    caseAnnotations = new Map(notes.map(
      (n) => [`${n.chain}|${n.address}`, n.note]));
  } catch (_) { /* notes never block the UI */ }
}

/* ===================== input detection ===================== */

let lastDetection = null;       // victim wallet field
let lastFocusDetection = null;  // focus transaction field

async function detectVictimInput() {
  const value = $("victim-input").value.trim();
  const box = $("detect-message");
  if (value.length < 8) { box.classList.add("hidden"); lastDetection = null; return; }
  lastDetection = await api("/api/detect", {
    method: "POST", body: JSON.stringify({ value }),
  });
  if (lastDetection.kind === "txid") {
    box.textContent = "That looks like a transaction ID, not a wallet " +
      "address. Put the victim's wallet address here — the transaction " +
      "hash goes in Step 3 below.";
    box.classList.remove("hidden");
    box.classList.add("blocked");
    return;
  }
  box.textContent = lastDetection.message;
  box.classList.remove("hidden");
  box.classList.toggle("blocked", !lastDetection.traceable);
  if (lastDetection.traceable && lastDetection.chain) {
    $("chain-select").value = lastDetection.chain;
  }
}

async function detectFocusTxInput() {
  const value = $("focus-tx-input").value.trim();
  const box = $("focus-detect-message");
  if (value.length < 8) {
    box.classList.add("hidden");
    lastFocusDetection = null;
    setExtendedAvailable(false);
    return;
  }
  lastFocusDetection = await api("/api/detect", {
    method: "POST", body: JSON.stringify({ value }),
  });
  if (lastFocusDetection.kind !== "txid") {
    box.textContent = "This doesn't look like a transaction hash (it " +
      "should be 64 hex characters, starting 0x on Ethereum). If it is a " +
      "wallet address, it belongs in Step 2 instead.";
    box.classList.remove("hidden");
    box.classList.add("blocked");
    setExtendedAvailable(false);
    return;
  }
  box.textContent = lastFocusDetection.message +
    " Only this payment's outgoing funds will be followed.";
  box.classList.remove("hidden");
  box.classList.remove("blocked");
  setExtendedAvailable(true);
}

/** Extended mode works with or without a focus transaction; the hint
    just reminds the user what a Step 3 hash adds (direction + hop
    counting from the first suspect wallet). */
function setExtendedAvailable(hasFocusTx) {
  $("extended-hint").textContent = hasFocusTx
    ? "The payment in Step 3 sets the direction; hops count from its " +
      "recipient (the first suspect wallet). Every branch is followed " +
      "until it resolves — named exchange, funds at rest, busy service, " +
      "mixer, or trail edge — up to 25 hops."
    : "Works with or without a transaction hash in Step 3. Every branch " +
      "is followed until it ends in a classified outcome — a named " +
      "exchange, funds sitting unspent, a busy service, a mixer, or a " +
      "trail edge — presented as ranked findings with a disposition-of-" +
      "funds accounting.";
}

/* ===================== trace lifecycle ===================== */

let pollTimer = null;

async function startTrace() {
  const caseId = Number($("case-select").value);
  if (!caseId) { alert("Create or choose a case first (Step 1)."); return; }
  const victimAddress = $("victim-input").value.trim();
  if (victimAddress.length < 8) {
    alert("Paste the victim's wallet address first (Step 2)."); return;
  }
  if (lastDetection && lastDetection.kind === "txid") {
    alert("Step 2 needs a wallet address, not a transaction ID — the " +
          "transaction hash goes in Step 3."); return;
  }
  if (lastDetection && !lastDetection.traceable) {
    alert("This wallet can't be traced by this version — see the note " +
          "under the input box."); return;
  }
  const focusTxid = $("focus-tx-input").value.trim();
  if (focusTxid && lastFocusDetection && lastFocusDetection.kind !== "txid") {
    alert("Step 3 needs a transaction hash (or leave it blank to follow " +
          "every outgoing payment)."); return;
  }
  const direction = document.querySelector(
    "input[name='trace-direction']:checked").value;
  if (focusTxid && direction === "backward") {
    alert("A backward (source-of-funds) trace examines every incoming " +
          "payment — leave the Step 3 focus transaction blank."); return;
  }
  if (focusTxid && $("chain-select").value === "tron") {
    alert("Focus transactions are not yet supported on Tron — leave " +
          "Step 3 blank."); return;
  }

  $("btn-trace").disabled = true;
  $("trace-status").classList.remove("hidden", "failed");
  $("trace-status").textContent = "Submitting trace…";

  try {
    const { trace_id } = await api("/api/traces", {
      method: "POST",
      body: JSON.stringify({
        case_id: caseId,
        victim_address: victimAddress,
        focus_txid: focusTxid,
        extended: $("extended-mode").checked,
        direction,
        search_pattern: document.querySelector(
          "input[name='search-pattern']:checked").value,
        chain: $("chain-select").value,
        max_depth: Number($("depth-input").value),
        dust_btc: Number($("dust-btc").value),
        dust_eth: Number($("dust-eth").value),
        dust_trx: Number($("dust-trx").value),
        dust_token: Number($("dust-token").value),
      }),
    });
    pollTrace(trace_id);
  } catch (error) {
    $("trace-status").textContent = `Could not start: ${error.message}`;
    $("trace-status").classList.add("failed");
    $("btn-trace").disabled = false;
  }
}

function pollTrace(traceId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const trace = await api(`/api/traces/${traceId}`);
    if (trace.status === "running" || trace.status === "queued") {
      $("trace-status").textContent =
        `Working… ${trace.progress_note || ""} (data pulls are rate-limited; ` +
        `deep traces take a few minutes)`;
      return;
    }
    clearInterval(pollTimer);
    $("btn-trace").disabled = false;
    if (trace.status === "failed") {
      $("trace-status").textContent = `Trace failed: ${trace.error}`;
      $("trace-status").classList.add("failed");
      return;
    }
    $("trace-status").textContent = "Trace finished.";
    renderResults(traceId, trace.result);
  }, TRACE_POLL_INTERVAL_MS);
}

/* ===================== results rendering ===================== */

let currentTraceId = null;

function renderResults(traceId, result) {
  currentTraceId = traceId;
  currentResult = result;   // needed before exits render (freeze links)
  $("results-empty").classList.add("hidden");
  $("results").classList.remove("hidden");

  refreshAnnotations();
  renderDisposition(result);
  renderFindings(result);
  renderExits(result);
  renderWarnings(result);
  renderGraph(result);

  const downloadable = traceId !== null;
  $("dl-pdf").href = downloadable ? `/api/traces/${traceId}/report.pdf` : "#";
  $("dl-custody").href = downloadable
    ? `/api/traces/${traceId}/custody.csv` : "#";
  $("dl-json").href = downloadable
    ? `/api/traces/${traceId}/export.json` : "#";
  $("dl-evidence").href = downloadable
    ? `/api/traces/${traceId}/evidence-package.zip` : "#";
  $("dl-affidavit").href = downloadable
    ? `/api/traces/${traceId}/affidavit.txt` : "#";
  // Overlay views are display-only: per-trace downloads don't apply.
  for (const id of ["dl-pdf", "dl-custody", "dl-json", "dl-evidence",
                    "dl-affidavit"]) {
    $(id).classList.toggle("hidden", !downloadable);
  }

  // Fresh AI conversation per trace.
  aiConversation = [];
  $("ai-conversation").innerHTML = "";
  updateAiPanel();
}

/* ===================== findings + disposition ===================== */

const FINDING_ICONS = {
  named_exchange: "🏛", funds_at_rest: "💰",
  flagged_wallet_contact: "🚩", scam_reported_contact: "⚠️",
  probable_exchange_deposit: "🎯", unidentified_service: "🏢",
  consolidation_point: "🔗", sanctioned_contact: "⛔",
  mixer_contact: "🌀", unresolved_edges: "⋯",
};

function renderDisposition(result) {
  const container = $("disposition");
  container.innerHTML = "";
  const rows = result.disposition || [];
  if (!rows.length) return;
  const heading = document.createElement("h2");
  heading.textContent = "Where the money stands (disposition of funds)";
  container.append(heading);
  const table = document.createElement("table");
  table.className = "panel-table";
  table.innerHTML = "<tr><th>Outcome</th><th>Addresses/branches</th>" +
    "<th>Traced value received</th><th>≈ USD</th><th>Share</th></tr>" +
    rows.map((row) => `<tr>
      <td>${row.bucket}</td><td>${row.count}</td>
      <td>${Object.entries(row.totals).map(([asset, value]) =>
        `${fmtAmount(value)} ${asset}`).join(", ") || "—"}</td>
      <td>${row.usd_complete && row.usd ? fmtUsd(row.usd) : "—"}</td>
      <td>${row.share !== undefined && row.share !== null
        ? row.share + "%" : "—"}</td></tr>`).join("");
  container.append(table);
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = "Amounts are traced value arriving at each terminal " +
    "point (taint-by-touch; co-mingled funds included). 'Not examined' " +
    "totals what the dust threshold and search pattern skipped.";
  container.append(note);
}

function renderFindings(result) {
  const container = $("findings");
  container.innerHTML = "";
  const findings = result.findings || [];
  const heading = document.createElement("h2");
  heading.textContent = `Investigative findings (${findings.length})`;
  container.append(heading);
  if (!findings.length) {
    const div = document.createElement("div");
    div.className = "no-exit";
    div.textContent = "No findings - the trace produced no terminal " +
      "outcomes (this is unusual; check the warnings).";
    container.append(div);
    return;
  }
  for (const finding of findings) {
    const card = document.createElement("div");
    card.className = `finding-card p${finding.priority}`;
    const links = (finding.addresses || []).slice(0, 2).map((address) => {
      return `<a href="${explorerFor(result.chain, address)}"
           target="_blank">explorer</a> ·
        <a href="https://www.chainabuse.com/address/${address}"
           target="_blank">Chainabuse</a>
        <code>${shortAddress(address)}</code>`;
    }).join(" &nbsp; ");
    card.innerHTML = `
      <div class="finding-title">${FINDING_ICONS[finding.type] || "•"}
        ${finding.title}
        ${finding.usd ? `<span class="finding-usd">≈ ${fmtUsd(finding.usd)}
          </span>` : ""}</div>
      <div class="finding-detail">${finding.detail}</div>
      <div class="finding-action"><b>Next step:</b> ${finding.action}</div>
      <div class="finding-links muted">Look up: ${links}</div>`;

    // Funds at rest: one-click watch — the natural next action.
    if (finding.type === "funds_at_rest" &&
        (finding.addresses || []).length) {
      const address = finding.addresses[0];
      const actions = document.createElement("div");
      actions.className = "panel-actions";
      const watchBtn = document.createElement("button");
      watchBtn.className = "secondary small";
      const watched = watchFor(result.chain, address);
      watchBtn.textContent = watched
        ? "👁 Watching (manage in Watches)" : "👁 Watch this wallet";
      watchBtn.disabled = !!watched;
      watchBtn.addEventListener("click", async () => {
        try {
          await addWatch(address, result.chain,
                         "Funds at rest (auto-added from finding)",
                         Number($("case-select").value) || null);
          watchBtn.textContent = "👁 Watching ✓";
          watchBtn.disabled = true;
        } catch (error) {
          alert(`Watch failed: ${error.message}`);
        }
      });
      actions.append(watchBtn);
      if (currentTraceId !== null) {
        const freezeHtml = freezeLinkHtml(address);
        if (freezeHtml) actions.insertAdjacentHTML("beforeend", freezeHtml);
      }
      card.append(actions);
    }
    container.append(card);
  }
}

/* Warrant-ready plain-text traceroute for one exit: victim wallet to the
   exchange deposit address, one block per hop. */
function tracerouteText(result, exit) {
  const generated = new Date().toISOString().replace("T", " ")
    .slice(0, 19) + " UTC";
  const chainName = result.chain.charAt(0).toUpperCase() +
    result.chain.slice(1);
  const backward = result.direction === "backward";
  const lines = [
    "CRYPTOCURRENCY FUND-FLOW PATH (TRACEROUTE)",
    `Generated by Crypto Investigator v${result.app_version} on ` +
      `${generated} — trace #${currentTraceId}`,
    `Blockchain: ${chainName}`,
    (backward ? "Target wallet (funds traced backward from): "
              : "Victim wallet: ") +
      (result.victim_address || result.start_input),
  ];
  if (result.focus_txid) {
    lines.push(`Focus transaction: ${result.focus_txid} ` +
      "(only this payment was followed)");
  }
  lines.push(
    (backward
      ? `Identified source: ${exit.entity} — address ${exit.address}`
      : `Destination: ${exit.entity} — deposit address ${exit.address}`),
    `Attribution: ${exit.confidence.toUpperCase()} confidence ` +
      `(source: ${exit.source})`);
  if (exit.compliance) {
    lines.push(
      `Compliance designation: ${exit.compliance.status === "compliant"
        ? "COMPLIANT" : "NON-COMPLIANT"} — designated by the ` +
      "investigating agency with reference to FATF guidance (not an " +
      "official FATF publication)" +
      (exit.compliance.note ? `; ${exit.compliance.note}` : ""));
  }
  lines.push(
    `Path length: ${exit.path.length} hop(s) from the victim wallet`,
    "");
  for (const hop of exit.path) {
    const usd = (hop.value_usd === null || hop.value_usd === undefined)
      ? "" : ` (approx. ${fmtUsd(hop.value_usd)} USD at the ` +
             "transaction date's daily price)";
    lines.push(
      `Hop ${hop.hop} of ${exit.path.length}`,
      `  From:         ${hop.from_address}`,
      `  To:           ${hop.to_address}`,
      `  Amount:       ${fmtAmount(hop.value)} ${hop.asset}${usd}`,
      `  Transaction:  ${hop.txid}`,
      `  Date/time:    ${fmtDateTime(hop.timestamp)}`,
      "");
  }
  lines.push(
    "NOTES: Transaction IDs, addresses, amounts and timestamps above are " +
    "public blockchain records, independently verifiable on any block " +
    "explorer. The exchange attribution and USD figures are inferences/" +
    "estimates whose sources and confidence are stated above and " +
    "detailed in the accompanying fund-tracing report.");
  return lines.join("\n");
}

function renderExits(result) {
  const container = $("exit-summary");
  container.innerHTML = "";
  const backward = result.direction === "backward";
  const heading = document.createElement("h2");
  heading.textContent = backward
    ? `Where the money came from — ${result.exits.length} named ` +
      `source${result.exits.length === 1 ? "" : "s"} identified`
    : `Where the money went — ${result.exits.length} ` +
      `exit point${result.exits.length === 1 ? "" : "s"} found`;
  container.append(heading);

  if (result.exits.length === 0) {
    const div = document.createElement("div");
    div.className = "no-exit";
    div.innerHTML = backward
      ? "<b>No known exchange identified as a source yet.</b> This does " +
        "not mean the funds have no custodial origin — the free label " +
        "lists are incomplete and the origin may lie past the traced " +
        "depth. Try more hops, or re-trace from an address at the edge " +
        "of the map (light grey)."
      : "<b>No known exchange reached yet.</b> This does not mean there " +
        "is no off-ramp — the free label lists are incomplete and the " +
        "trail may continue past the traced depth. Try more hops, or " +
        "re-trace from an address at the edge of the map (light grey).";
    container.append(div);
    return;
  }

  result.exits.forEach((exit, index) => {
    const card = document.createElement("div");
    card.className = "exit-card" +
      (exit.confidence === "high" ? "" : " low-confidence");
    const totals = Object.entries(exit.totals)
      .map(([asset, value]) => `${value.toLocaleString(undefined,
        { maximumFractionDigits: 6 })} ${asset}`).join(", ") +
      (exit.totals_usd !== undefined && exit.totals_usd !== null
        ? ` (≈ $${Number(exit.totals_usd).toLocaleString(undefined,
            { maximumFractionDigits: 2 })} at the transaction dates)`
        : "");
    let complianceBadge = "";
    if (exit.compliance) {
      const compliant = exit.compliance.status === "compliant";
      complianceBadge = `<span class="compliance-badge ${compliant
          ? "compliant" : "noncompliant"}"
        title="${exit.compliance.source_note}">${compliant
          ? "COMPLIANT" : "NON-COMPLIANT"} — agency designation
        (FATF-referenced)</span>`;
    }
    card.innerHTML = `
      <h3>#${index + 1} ${exit.entity} ${complianceBadge}</h3>
      ${exit.compliance ? `<div class="muted compliance-line">${
        exit.compliance.status === "compliant"
          ? "Designated compliant: expect this custodian to respond to " +
            "subpoenas/warrants through normal channels."
          : "Designated NON-compliant: this custodian may not respond to " +
            "US legal process — consider MLAT, asset freezing at the " +
            "platform, or tracing onward before funds move."}${
        exit.compliance.note ? " (" + exit.compliance.note + ")" : ""}
        <i>Designation is the agency's own, referenced to FATF guidance — ` +
        `edit data/labels/exchange_compliance.json.</i></div>` : ""}
      <div>${result.direction === "backward" ? "Sending" :
        "Deposit/receiving"} address: <code>${exit.address}</code></div>
      <div>${result.direction === "backward" ? "Sent" : "Received"}
        <b>${totals}</b> of traced funds,
        ${exit.depth} hop${exit.depth === 1 ? "" : "s"} from the start,
        across ${exit.funding.length} transaction(s).</div>
      <div class="muted">Attribution confidence:
        <b>${exit.confidence.toUpperCase()}</b> (source: ${exit.source}).
        The PDF report includes the funding transactions and draft
        records-request language for this custodian.</div>`;

    if (exit.path && exit.path.length) {
      const actions = document.createElement("div");
      actions.className = "exit-actions";
      const copyBtn = document.createElement("button");
      copyBtn.className = "secondary small";
      copyBtn.textContent = "Copy traceroute (for warrant)";
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(tracerouteText(result, exit));
          copyBtn.textContent = "Copied ✓";
        } catch (_) { copyBtn.textContent = "Copy failed"; }
      });
      const dlBtn = document.createElement("button");
      dlBtn.className = "secondary small";
      dlBtn.textContent = "Download traceroute (.txt)";
      dlBtn.addEventListener("click", () => {
        const blob = new Blob([tracerouteText(result, exit)],
                              { type: "text/plain" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `traceroute_trace${currentTraceId}_` +
          `${exit.entity.replace(/[^A-Za-z0-9]+/g, "_")}.txt`;
        link.click();
        URL.revokeObjectURL(link.href);
      });
      const note = document.createElement("span");
      note.className = "muted";
      note.textContent = ` ${exit.path.length} hop(s) from the victim ` +
        "wallet — hop-by-hop path with hashes, amounts and dates.";
      actions.append(copyBtn, dlBtn, note);
      card.append(actions);
    }

    // Freeze/preservation request for this deposit address.
    const freezeHtml = freezeLinkHtml(exit.address);
    if (freezeHtml) {
      const freezeRow = document.createElement("div");
      freezeRow.className = "exit-actions";
      freezeRow.innerHTML = freezeHtml +
        ` <span class="muted">DRAFT letter with the funding transactions as
        an attachment — counsel/prosecutor review required; verify the
        custodian's channel on its own site before service.</span>`;
      card.append(freezeRow);
    }
    container.append(card);
  });
}

function renderWarnings(result) {
  const container = $("warnings");
  container.innerHTML = "";
  for (const warning of result.warnings || []) {
    const div = document.createElement("div");
    div.className = "warning-item";
    div.textContent = warning;
    container.append(div);
  }
}

let cy = null;
let currentResult = null;
let graphSpread = 1.0;   // spacing multiplier (Spread out / Tighten buttons)

const fmtAmount = (v) =>
  Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 });
const fmtUsd = (v) => (v === null || v === undefined) ? "—" :
  "$" + Number(v).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDateTime = (ts) => ts
  ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC"
  : "—";

function collapseOptions() {
  return {
    chains: $("collapse-chains").checked,
    entities: $("collapse-entities").checked,
    minor: $("collapse-minor").checked,
  };
}

/*
 * View model: turns the raw trace result into DISPLAY nodes and edges.
 * Collapsing is a display operation ONLY — every address and transaction
 * stays in the result, the PDF and the exports; the panels for collapsed
 * elements list everything they hide.
 */
function buildViewModel(result, options) {
  const nodeByAddress = new Map(result.nodes.map((n) => [n.address, n]));
  const display = new Map();   // display id -> descriptor
  const idFor = new Map();     // address -> display id

  // Pass 1: address nodes, optionally grouped into exchange entities.
  for (const node of result.nodes) {
    let id = node.address;
    if (options.entities && node.role === "exchange") {
      const label = (node.labels || []).find(
        (l) => l.category === "exchange");
      if (label) id = "entity:" + label.entity_name;
    }
    idFor.set(node.address, id);
    if (!display.has(id)) {
      display.set(id, id.startsWith("entity:")
        ? { id, kind: "entity", entity: id.slice(7), members: [],
            role: "exchange" }
        : { id, kind: "address", node, role: node.role });
    }
    if (id.startsWith("entity:")) display.get(id).members.push(node);
  }

  // Pass 2: aggregate raw movements into one flow per pair per asset.
  const groups = new Map();
  for (const edge of result.edges) {
    const from = idFor.get(edge.from_address) || edge.from_address;
    const to = idFor.get(edge.to_address) || edge.to_address;
    if (from === to) continue;   // movement inside one entity group
    for (const [id, address] of [[from, edge.from_address],
                                 [to, edge.to_address]]) {
      if (!display.has(id)) {    // e.g. synthetic source from old traces
        display.set(id, { id, kind: "address",
          node: nodeByAddress.get(address) || null, role: "victim" });
      }
    }
    const key = from + "|" + to + "|" + edge.asset;
    let group = groups.get(key);
    if (!group) {
      group = { id: "g:" + key, from, to, asset: edge.asset, kind: "flow",
                value: 0, valueUsd: 0, usdComplete: true, txs: [] };
      groups.set(key, group);
    }
    group.value += edge.value;
    if (edge.value_usd === null || edge.value_usd === undefined) {
      group.usdComplete = false;
    } else {
      group.valueUsd += edge.value_usd;
    }
    group.txs.push(edge);
  }
  let edges = [...groups.values()];

  if (options.chains) edges = collapseChains(display, edges);
  if (options.minor) edges = foldMinorBranches(display, edges);

  // Drop display nodes no longer connected to anything (hidden by a
  // collapse), keeping the victim anchor(s).
  const used = new Set();
  for (const e of edges) { used.add(e.from); used.add(e.to); }
  for (const [id, d] of [...display]) {
    if (!used.has(id) && !(d.kind === "address" && d.role === "victim")) {
      display.delete(id);
    }
  }
  return { display, edges };
}

/* Contract runs of one-in/one-out pass-through addresses (peel chains)
   into a single dashed edge that remembers everything it hides. */
function collapseChains(display, edges) {
  let changed = true;
  while (changed) {
    changed = false;
    const incoming = new Map(), outgoing = new Map();
    for (const e of edges) {
      if (!incoming.has(e.to)) incoming.set(e.to, []);
      incoming.get(e.to).push(e);
      if (!outgoing.has(e.from)) outgoing.set(e.from, []);
      outgoing.get(e.from).push(e);
    }
    for (const [id, d] of display) {
      if (d.kind !== "address" || !d.node) continue;
      if (d.role !== "intermediary") continue;
      const ins = incoming.get(id) || [];
      const outs = outgoing.get(id) || [];
      if (ins.length !== 1 || outs.length !== 1) continue;
      const inE = ins[0], outE = outs[0];
      if (inE.from === id || outE.to === id || inE.from === outE.to) continue;
      const chainNodes = [
        ...(inE.kind === "chain" ? inE.chainNodes : []),
        d.node,
        ...(outE.kind === "chain" ? outE.chainNodes : []),
      ];
      const segments = [
        ...(inE.kind === "chain" ? inE.segments : [inE]),
        ...(outE.kind === "chain" ? outE.segments : [outE]),
      ];
      edges = edges.filter((e) => e !== inE && e !== outE);
      edges.push({
        id: "chain:" + inE.from + ">" + outE.to + ":" + chainNodes[0].address,
        from: inE.from, to: outE.to, kind: "chain",
        chainNodes, segments,
        entryValue: segments[0].value, entryAsset: segments[0].asset,
      });
      display.delete(id);
      changed = true;
      break;   // degree tables are stale now — rescan
    }
  }
  return edges;
}

/* Fold a spray of small dead-end branches from one address into a single
   "+N minor branches" stub (keeps the 3 largest visible). */
function foldMinorBranches(display, edges) {
  const incoming = new Map(), outgoing = new Map();
  for (const e of edges) {
    if (!incoming.has(e.to)) incoming.set(e.to, []);
    incoming.get(e.to).push(e);
    if (!outgoing.has(e.from)) outgoing.set(e.from, []);
    outgoing.get(e.from).push(e);
  }
  const foldedEdges = new Set();
  const newEdges = [];
  for (const [id] of [...display]) {
    const outs = (outgoing.get(id) || []).filter((e) => e.kind === "flow");
    const leaves = outs.filter((e) => {
      const target = display.get(e.to);
      return target && target.kind === "address" && target.node &&
        (target.role === "intermediary" || target.role === "unexpanded") &&
        (outgoing.get(e.to) || []).length === 0 &&
        (incoming.get(e.to) || []).length === 1;
    });
    if (leaves.length <= 6) continue;
    const ranked = leaves.slice().sort((a, b) =>
      ((b.usdComplete ? b.valueUsd : b.value)) -
      ((a.usdComplete ? a.valueUsd : a.value)));
    const fold = ranked.slice(3);
    const stubId = "stub:" + id;
    display.set(stubId, { id: stubId, kind: "stub", parent: id,
                          folded: fold });
    for (const e of fold) {
      foldedEdges.add(e);
      display.delete(e.to);
    }
    newEdges.push({ id: "e" + stubId, from: id, to: stubId,
                    kind: "stubEdge", folded: fold });
  }
  return edges.filter((e) => !foldedEdges.has(e)).concat(newEdges);
}

/* Left-to-right layered layout: money flows from the victim (left) toward
   exits (right). Columns = longest path from a root; rows ordered by the
   average position of each node's senders to reduce crossings. */
function layoutPositions(view) {
  const { display, edges } = view;
  const col = new Map();
  const hasIncoming = new Set(edges.map((e) => e.to));
  for (const [id, d] of display) {
    if ((d.kind === "address" && d.role === "victim") ||
        !hasIncoming.has(id)) {
      col.set(id, 0);
    }
  }
  const n = display.size || 1;
  for (let pass = 0; pass < n; pass++) {
    let moved = false;
    for (const e of edges) {
      const base = col.get(e.from);
      if (base === undefined) continue;
      const want = Math.min(base + 1, n);
      if ((col.get(e.to) || 0) < want) { col.set(e.to, want); moved = true; }
    }
    if (!moved) break;
  }
  for (const [id] of display) if (!col.has(id)) col.set(id, 0);

  const byCol = new Map();
  for (const [id] of display) {
    const c = col.get(id);
    if (!byCol.has(c)) byCol.set(c, []);
    byCol.get(c).push(id);
  }
  const preds = new Map();
  for (const e of edges) {
    if (!preds.has(e.to)) preds.set(e.to, []);
    preds.get(e.to).push(e.from);
  }
  const xGap = 250 * graphSpread, yGap = 90 * graphSpread;
  const pos = new Map();
  for (const c of [...byCol.keys()].sort((a, b) => a - b)) {
    let ids = byCol.get(c);
    if (c > 0) {
      const score = (id) => {
        const ys = (preds.get(id) || [])
          .map((p) => (pos.get(p) || {}).y)
          .filter((y) => y !== undefined);
        return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0;
      };
      ids = ids.slice().sort((a, b) => score(a) - score(b));
    }
    ids.forEach((id, i) => pos.set(id,
      { x: c * xGap, y: (i - (ids.length - 1) / 2) * yGap }));
  }
  return pos;
}

function displayLabel(d) {
  if (d.kind === "entity") {
    return d.members.length === 1
      ? `${d.entity}\n${shortAddress(d.members[0].address)}`
      : `${d.entity}\n${d.members.length} deposit addresses`;
  }
  if (d.kind === "stub") {
    return `+${d.folded.length} minor branches`;
  }
  const node = d.node;
  if (!node) return shortAddress(d.id);
  const label = (node.labels || []).find((l) =>
    l.category === "exchange" || l.category === "sanctioned" ||
    l.category === "mixer");
  let text = label ? `${label.entity_name}\n${shortAddress(node.address)}`
                   : shortAddress(node.address);
  if (node.overlay_hits > 1) {
    text += `\nin ${node.overlay_hits} traces`;
  }
  return text;
}

function displayConverged(d) {
  if (d.kind === "entity") return d.members.some((m) => m.converged);
  return !!(d.kind === "address" && d.node && d.node.converged);
}

function displayRole(d) {
  if (d.kind === "entity") return "exchange";
  if (d.kind === "stub") return "unexpanded";
  return d.node ? d.node.role : "victim";
}

function displayFlagged(d) {
  if (d.kind === "entity") {
    return d.members.some((m) => flagFor(m.chain, m.address));
  }
  if (d.kind === "address" && d.node) {
    return !!flagFor(d.node.chain, d.node.address);
  }
  return false;
}

function edgeLabel(e) {
  if (e.kind === "chain") {
    return `${e.chainNodes.length} pass-through address` +
      (e.chainNodes.length === 1 ? "" : "es") +
      ` · entered as ${fmtAmount(e.entryValue)} ${e.entryAsset}`;
  }
  if (e.kind === "stubEdge") {
    return `${e.folded.length} small branches`;
  }
  return `${fmtAmount(e.value)} ${e.asset}` +
    (e.txs.length > 1 ? ` (${e.txs.length} txs)` : "");
}

function renderGraph(result) {
  currentResult = result;
  graphSpread = 1.0;
  redrawGraph();
}

function redrawGraph() {
  if (!currentResult) return;
  const view = buildViewModel(currentResult, collapseOptions());
  const pos = layoutPositions(view);

  const elements = [];
  for (const [id, d] of view.display) {
    elements.push({ data: { id, label: displayLabel(d),
                            role: displayRole(d), kind: d.kind, d,
                            flagged: displayFlagged(d) ? 1 : 0,
                            converged: displayConverged(d) ? 1 : 0 },
                    position: pos.get(id) });
  }
  for (const e of view.edges) {
    elements.push({ data: { id: e.id, source: e.from, target: e.to,
                            label: edgeLabel(e), kind: e.kind, e } });
  }

  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("graph"),
    elements,
    style: [
      { selector: "node", style: {
        "background-color": (el) => ROLE_COLORS[el.data("role")] || "#999",
        label: "data(label)", "text-wrap": "wrap", "font-size": 9,
        "text-valign": "bottom", "text-margin-y": 4, width: 26, height: 26,
        "border-width": 2, "border-color": "#ffffff",
      }},
      { selector: "node[role='exchange']", style: { width: 38, height: 38 }},
      { selector: "node[role='victim']", style: { width: 34, height: 34 }},
      { selector: "node[kind='entity']", style: {
        shape: "round-rectangle", width: 46, height: 34 }},
      { selector: "node[kind='stub']", style: {
        shape: "diamond", width: 22, height: 22 }},
      { selector: "edge", style: {
        width: 3, "curve-style": "bezier",
        "target-arrow-shape": "triangle", "arrow-scale": 1.1,
        "line-color": "#9fb0c1", "target-arrow-color": "#9fb0c1",
        label: "data(label)", "font-size": 8, "text-rotation": "autorotate",
        "text-background-color": "#ffffff", "text-background-opacity": 0.85,
        "text-background-padding": 2,
      }},
      { selector: "edge[kind='chain']", style: {
        "line-style": "dashed", "line-color": "#7d8ea0",
        "target-arrow-color": "#7d8ea0",
      }},
      { selector: "edge[kind='stubEdge']", style: {
        "line-style": "dotted", "line-color": "#b8c3ce",
        "target-arrow-color": "#b8c3ce",
      }},
      { selector: "node[flagged = 1]", style: {
        "border-color": "#c22525", "border-width": 4,
      }},
      { selector: "node[converged = 1]", style: {
        "border-color": "#d97a06", "border-width": 5,
        "border-style": "double",
      }},
      { selector: ":selected", style: {
        "border-color": "#1857b8", "border-width": 3,
      }},
    ],
    layout: { name: "preset", fit: true, padding: 30 },
    wheelSensitivity: 0.3,
  });

  cy.on("tap", "node", (ev) => showDisplayNodeDetails(ev.target.data("d")));
  cy.on("tap", "edge", (ev) => showDisplayEdgeDetails(ev.target.data("e")));
  cy.on("dbltap", (ev) => { if (ev.target === cy) cy.fit(undefined, 30); });
}

/* Wire a "copy" button rendered inside the details panel to the clipboard
   (tab-separated, ready to paste into a warrant table or spreadsheet). */
function attachCopyButton(text) {
  const btn = $("btn-copy-panel");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied ✓";
    } catch (_) {
      btn.textContent = "Copy failed";
    }
  });
}

function txTableHtml(txs) {
  const rows = txs.slice().sort((a, b) =>
    (a.timestamp || 0) - (b.timestamp || 0)).map((tx) => `
    <tr><td>${fmtDateTime(tx.timestamp)}</td>
        <td>${fmtAmount(tx.value)} ${tx.asset}</td>
        <td>${fmtUsd(tx.value_usd)}</td>
        <td><code>${tx.txid}</code></td></tr>`).join("");
  return `<table class="panel-table">
    <tr><th>Date/time</th><th>Amount</th><th>≈ USD then</th>
        <th>Transaction hash</th></tr>${rows}</table>`;
}

function txTableText(txs) {
  const lines = ["date_utc\tamount\tasset\tusd_at_date\ttxid"];
  for (const tx of txs.slice().sort((a, b) =>
      (a.timestamp || 0) - (b.timestamp || 0))) {
    lines.push([fmtDateTime(tx.timestamp), tx.value, tx.asset,
                (tx.value_usd === null || tx.value_usd === undefined)
                  ? "" : tx.value_usd, tx.txid].join("\t"));
  }
  return lines.join("\n");
}

function showAddressDetails(node) {
  const panel = $("node-details");
  panel.classList.remove("hidden");
  if (!node) { panel.textContent = "Aggregated transaction inputs."; return; }
  const labelLines = (node.labels || []).map((l) =>
    `<li>${l.entity_name} — ${l.category}, ${l.confidence} confidence ` +
    `(source: ${l.source})</li>`).join("");
  const explorerUrl = explorerFor(node.chain, node.address);
  const flag = flagFor(node.chain, node.address);
  const watch = watchFor(node.chain, node.address);
  const note = caseAnnotations.get(`${node.chain}|${node.address}`) || "";
  panel.innerHTML = `
    <b>Address:</b> <code>${node.address}</code><br>
    <b>Role in this trace:</b> ${node.role.replace(/_/g, " ")} —
    ${ROLE_EXPLANATIONS[node.role] || ""}<br>
    <b>Hops from start:</b> ${node.depth}<br>
    ${node.basis ? `<b>Basis:</b> ${node.basis}<br>` : ""}
    ${labelLines ? `<b>Labels:</b><ul>${labelLines}</ul>` : ""}
    ${(node.flags || []).length
      ? `<b>Flags:</b> ${node.flags.join(", ")}<br>` : ""}
    <b>Check elsewhere:</b>
    <a href="${explorerUrl}" target="_blank">block explorer</a> ·
    <a href="https://www.chainabuse.com/address/${node.address}"
       target="_blank">Chainabuse scam reports</a>
    <span class="muted">(opens external sites — free public databases of
    reported scam addresses)</span>
    ${flag ? `<div class="flag-info">🚩 Flagged by this agency${
      flag.case_name ? ` in case “${flag.case_name}”` : ""}${
      flag.created_utc ? ` on ${flag.created_utc.slice(0, 10)}` : ""}${
      flag.reason ? ` — ${flag.reason}` : ""}</div>` : ""}
    <div class="panel-actions">
      <button id="btn-toggle-flag" class="secondary small">${flag
        ? "Remove agency flag" : "🚩 Flag as fraudulent"}</button>
      <button id="btn-toggle-watch" class="secondary small">${watch
        ? "👁 Stop watching" : "👁 Watch for movement"}</button>
      ${freezeLinkHtml(node.address)}
    </div>
    <div class="annotation-box">
      <label class="field-label">Investigator note (saved to this case;
        appears in the PDF address table)
        <textarea id="annotation-input" rows="2"
          placeholder="e.g. identified via subpoena return #12, believed
suspect-controlled">${note}</textarea>
      </label>
      <button id="btn-save-annotation" class="secondary small">Save
        note</button>
      <span id="annotation-status" class="muted"></span>
    </div>`;
  $("btn-toggle-flag").addEventListener("click", async () => {
    try {
      if (flag) {
        await removeFlag(node.address, node.chain);
      } else {
        const reason = prompt(
          "Reason for flagging (shown on findings and reports):", "");
        if (reason === null) return;   // cancelled
        await addFlag(node.address, node.chain, reason.trim(),
                      Number($("case-select").value) || null);
      }
      if (cy) redrawGraph();
      showAddressDetails(node);
    } catch (error) {
      alert(`Flag change failed: ${error.message}`);
    }
  });
  $("btn-toggle-watch").addEventListener("click", async () => {
    try {
      if (watch) {
        await removeWatch(node.address, node.chain);
      } else {
        await addWatch(node.address, node.chain,
                       "Added from the money-flow map",
                       Number($("case-select").value) || null);
      }
      showAddressDetails(node);
    } catch (error) {
      alert(`Watch change failed: ${error.message}`);
    }
  });
  $("btn-save-annotation").addEventListener("click", async () => {
    const caseId = Number($("case-select").value);
    if (!caseId) { alert("Choose a case first (Step 1)."); return; }
    try {
      await api(`/api/cases/${caseId}/annotations`, {
        method: "POST",
        body: JSON.stringify({
          address: node.address, chain: node.chain,
          note: $("annotation-input").value,
        }),
      });
      await refreshAnnotations();
      $("annotation-status").textContent = "Saved ✓";
    } catch (error) {
      $("annotation-status").textContent = `Failed: ${error.message}`;
    }
  });
}

function showDisplayNodeDetails(d) {
  if (!d) return;
  if (d.kind === "address") { showAddressDetails(d.node); return; }
  const panel = $("node-details");
  panel.classList.remove("hidden");

  if (d.kind === "entity") {
    const memberRows = d.members.map((m) => `
      <tr><td><code>${m.address}</code></td><td>${m.depth}</td>
          <td>${freezeLinkHtml(m.address)}</td></tr>`)
      .join("");
    panel.innerHTML = `
      <b>Exchange entity:</b> ${d.entity} —
      ${d.members.length} deposit address(es) received traced funds.
      One legal request to this custodian can cover all of them; see the
      exit points above for amounts and draft records-request language.
      <table class="panel-table">
        <tr><th>Deposit address</th><th>Hops from start</th>
            <th>Freeze/preservation</th></tr>
        ${memberRows}</table>
      <span class="muted">Grouped for readability — uncheck “exchange
      entities” to show each address separately.</span>`;
    return;
  }

  if (d.kind === "stub") {
    const rows = d.folded.map((e) => `
      <tr><td><code>${e.to}</code></td>
          <td>${fmtAmount(e.value)} ${e.asset}</td>
          <td>${e.usdComplete ? fmtUsd(e.valueUsd) : "—"}</td>
          <td>${e.txs.length}</td></tr>`).join("");
    panel.innerHTML = `
      <b>${d.folded.length} minor branches</b> from
      <code>${shortAddress(d.parent)}</code> — small dead-end movements
      folded together so the main flow stays readable. Everything is still
      in the PDF and exports.
      <table class="panel-table">
        <tr><th>Destination</th><th>Amount</th><th>≈ USD then</th>
            <th>Txs</th></tr>${rows}</table>
      <span class="muted">Uncheck “minor branches” (or press Expand all)
      to show them on the map.</span>`;
  }
}

function showDisplayEdgeDetails(e) {
  if (!e) return;
  const panel = $("node-details");
  panel.classList.remove("hidden");

  if (e.kind === "flow") {
    const usdTotal = e.usdComplete ? ` (≈ ${fmtUsd(e.valueUsd)} at the
      transaction dates)` : "";
    panel.innerHTML = `
      <b>Movement:</b> <code>${shortAddress(e.from)}</code> →
      <code>${shortAddress(e.to)}</code>
      <button id="btn-copy-panel" class="secondary small"
        style="float:right">Copy table</button><br>
      <b>Total:</b> ${fmtAmount(e.value)} ${e.asset} across
      ${e.txs.length} transaction(s)${usdTotal}
      ${txTableHtml(e.txs)}`;
    attachCopyButton(txTableText(e.txs));
    return;
  }

  if (e.kind === "chain") {
    const allTxs = e.segments.flatMap((s) => s.txs);
    const hops = e.segments.map((s, i) => `
      <tr><td>${i + 1}</td>
          <td><code>${shortAddress(s.from)}</code> →
              <code>${shortAddress(s.to)}</code></td>
          <td>${fmtAmount(s.value)} ${s.asset}</td>
          <td>${s.usdComplete ? fmtUsd(s.valueUsd) : "—"}</td>
          <td>${s.txs.length}</td></tr>`).join("");
    panel.innerHTML = `
      <b>Collapsed pass-through chain:</b> funds moved through
      ${e.chainNodes.length} intermediate address(es) in sequence — a
      common layering pattern. Each hop below is a real on-chain movement.
      <button id="btn-copy-panel" class="secondary small"
        style="float:right">Copy transactions</button>
      <table class="panel-table">
        <tr><th>Hop</th><th>Movement</th><th>Amount</th>
            <th>≈ USD then</th><th>Txs</th></tr>${hops}</table>
      <span class="muted">Uncheck “pass-through chains” (or press Expand
      all) to show every address on the map.</span>`;
    attachCopyButton(txTableText(allTxs));
    return;
  }

  if (e.kind === "stubEdge") {
    showDisplayNodeDetails({ kind: "stub", parent: e.from,
                             folded: e.folded });
  }
}

/* ===================== AI assistant (optional, glass-box) ============= */

let aiStatus = null;
let aiConversation = [];    // [{role, content}] turns for the current trace

async function refreshAiStatus() {
  try { aiStatus = await api("/api/ai/status"); }
  catch (_) { aiStatus = null; }
}

function updateAiPanel() {
  const panel = $("ai-panel");
  // Trace-scoped feature: hidden for overlay views (no single trace).
  if (currentTraceId === null || !currentResult) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const configured = !!(aiStatus && aiStatus.configured);
  $("ai-unconfigured").classList.toggle("hidden", configured);
  $("ai-controls").classList.toggle("hidden", !configured);
  if (configured) {
    $("ai-status-line").textContent =
      `${aiStatus.provider} · ${aiStatus.model}` +
      (aiStatus.local ? " · local endpoint (data stays on this machine)"
                      : "");
  }
}

function appendAiTurn(role, text) {
  const turn = document.createElement("div");
  turn.className = `ai-turn ${role}`;
  const label = document.createElement("div");
  label.className = "ai-turn-label muted";
  label.textContent = role === "user"
    ? "You"
    : `AI (${aiStatus ? aiStatus.model : "?"}) — assistance, not evidence`;
  const body = document.createElement("pre");
  body.className = "ai-text";
  body.textContent = text;   // textContent: AI output is never HTML
  turn.append(label, body);
  $("ai-conversation").append(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setAiBusy(busy) {
  $("btn-ai-summary").disabled = busy;
  $("btn-ai-ask").disabled = busy;
  if (busy) {
    $("ai-status-line").textContent = "Thinking… (response is logged)";
  } else {
    updateAiPanel();
  }
}

async function aiSummary() {
  if (currentTraceId === null) return;
  setAiBusy(true);
  try {
    const reply = await api(`/api/traces/${currentTraceId}/ai/summary`,
                            { method: "POST" });
    aiConversation.push(
      { role: "user",
        content: "Provide a plain-language summary of this trace." },
      { role: "assistant", content: reply.text });
    appendAiTurn("assistant", reply.text);
  } catch (error) {
    appendAiTurn("assistant", `Request failed: ${error.message}`);
  } finally {
    setAiBusy(false);
  }
}

async function aiAsk() {
  if (currentTraceId === null) return;
  const question = $("ai-question").value.trim();
  if (!question) return;
  appendAiTurn("user", question);
  $("ai-question").value = "";
  setAiBusy(true);
  try {
    const reply = await api(`/api/traces/${currentTraceId}/ai/ask`, {
      method: "POST",
      body: JSON.stringify({ question, history: aiConversation }),
    });
    aiConversation.push({ role: "user", content: question },
                        { role: "assistant", content: reply.text });
    appendAiTurn("assistant", reply.text);
  } catch (error) {
    appendAiTurn("assistant", `Request failed: ${error.message}`);
  } finally {
    setAiBusy(false);
  }
}

async function openAiLog() {
  const rows = await api("/api/ai/log");
  const list = $("ai-log-list");
  $("ai-log-detail").classList.add("hidden");
  if (!rows.length) {
    list.innerHTML = "<p class='muted'>No AI activity yet.</p>";
  } else {
    list.innerHTML = `<table class="panel-table">
      <tr><th>When (UTC)</th><th>Provider · model</th><th>Purpose</th>
          <th>Trace</th><th>Sent</th><th>Received</th><th>ms</th>
          <th>Error</th></tr>` +
      rows.map((r) => `<tr class="ai-log-row" data-log="${r.id}">
        <td>${(r.created_utc || "").replace("+00:00", "Z")}</td>
        <td>${r.provider} · ${r.model}</td>
        <td>${r.purpose}</td>
        <td>${r.trace_id ?? "—"}</td>
        <td>${(r.request_chars || 0).toLocaleString()} ch</td>
        <td>${(r.response_chars || 0).toLocaleString()} ch</td>
        <td>${r.duration_ms ?? "—"}</td>
        <td>${r.error ? "⚠" : ""}</td></tr>`).join("") + "</table>";
    list.querySelectorAll(".ai-log-row").forEach((row) => {
      row.addEventListener("click", async () => {
        const entry = await api(`/api/ai/log/${row.dataset.log}`);
        const detail = $("ai-log-detail");
        detail.classList.remove("hidden");
        detail.innerHTML = "";
        const heading = document.createElement("h3");
        heading.textContent = `Record #${entry.id} — exact exchange`;
        const requestPre = document.createElement("pre");
        requestPre.className = "ai-text";
        requestPre.textContent = "SENT:\n" + (entry.request_json || "");
        const responsePre = document.createElement("pre");
        responsePre.className = "ai-text";
        responsePre.textContent = "RECEIVED:\n" +
          (entry.error ? `ERROR: ${entry.error}\n` : "") +
          (entry.response_text || "");
        detail.append(heading, requestPre, responsePre);
        detail.scrollIntoView({ behavior: "smooth" });
      });
    });
  }
  $("ai-log-dialog").showModal();
}

/* ===================== case overlay map ===================== */

const ROLE_PRIORITY = { victim: 0, exchange: 1, sanctioned: 2, mixer: 3,
                        high_activity_service: 4, contract: 5,
                        intermediary: 6, unexpanded: 7 };

/** Merge every finished trace of the selected case onto one map and
    highlight wallets hit by traces with DIFFERENT victims - the shared
    laundering infrastructure that links victims together. Display-only:
    per-trace downloads are hidden in overlay view. */
async function openCaseOverlay() {
  const caseId = Number($("case-select").value);
  if (!caseId) { alert("Choose a case first (Step 1)."); return; }
  const cases = await api("/api/cases");
  const theCase = cases.find((c) => c.id === caseId);
  const finished = ((theCase || {}).traces || [])
    .filter((t) => t.status === "finished");
  if (!finished.length) {
    alert("No finished traces in this case yet - run a trace first.");
    return;
  }
  $("btn-case-overlay").disabled = true;
  try {
    const group = [];
    for (const t of finished) {
      const full = await api(`/api/traces/${t.id}`);
      if (full.result) group.push({ id: t.id, result: full.result });
    }
    // One chain per map: use the chain with the most traces.
    const byChain = new Map();
    for (const entry of group) {
      const chain = entry.result.chain;
      if (!byChain.has(chain)) byChain.set(chain, []);
      byChain.get(chain).push(entry);
    }
    const [chain, traces] = [...byChain.entries()]
      .sort((a, b) => b[1].length - a[1].length)[0];

    const nodeMap = new Map();
    const hits = new Map();          // address -> Set(trace ids)
    const victimOf = new Map(traces.map(
      ({ id, result }) => [id, result.victim_address]));
    const edges = [], edgeKeys = new Set();
    const exits = [], exitSeen = new Set();
    for (const { id, result } of traces) {
      for (const n of result.nodes) {
        if (!hits.has(n.address)) hits.set(n.address, new Set());
        hits.get(n.address).add(id);
        const existing = nodeMap.get(n.address);
        if (!existing || (ROLE_PRIORITY[n.role] ?? 9) <
            (ROLE_PRIORITY[existing.role] ?? 9)) {
          nodeMap.set(n.address, { ...n });
        }
      }
      for (const e of result.edges) {
        const key = `${e.txid}|${e.from_address}|${e.to_address}|${e.asset}`;
        if (edgeKeys.has(key)) continue;
        edgeKeys.add(key);
        edges.push(e);
      }
      for (const x of result.exits || []) {
        if (exitSeen.has(x.address)) continue;
        exitSeen.add(x.address);
        exits.push(x);
      }
    }
    const victims = new Set(victimOf.values());
    const convergence = [];
    for (const [address, ids] of hits) {
      if (victims.has(address)) continue;
      const distinctVictims = new Set(
        [...ids].map((id) => victimOf.get(id)));
      if (distinctVictims.size >= 2) {
        convergence.push({ address, traces: [...ids] });
        const node = nodeMap.get(address);
        if (node) node.converged = true;
      }
    }
    for (const [address, ids] of hits) {
      const node = nodeMap.get(address);
      if (node) node.overlay_hits = ids.size;
    }

    const warnings = [
      `CASE OVERLAY (display-only): ${traces.length} finished ` +
      `${chain} trace(s) of this case merged onto one map` +
      (byChain.size > 1 ? `; ${byChain.size - 1} trace(s) on other ` +
        `chains not shown` : "") + ". Per-trace downloads are hidden - " +
      "open an individual trace for reports and exports.",
    ];
    if (convergence.length) {
      warnings.push(
        `CONVERGENCE: ${convergence.length} wallet(s) received funds ` +
        `traced from DIFFERENT victims - likely shared scammer ` +
        `infrastructure (orange double ring on the map): ` +
        convergence.map((c) => c.address).join(", "));
    } else if (traces.length > 1) {
      warnings.push("No wallet is shared between traces with different " +
        "victims (yet) - the traces do not converge at this depth.");
    }

    renderResults(null, {
      app_version: (lastMeta || {}).version || "",
      chain,
      direction: "forward",
      victim_address: [...victims].join(", "),
      start_input: [...victims].join(", "),
      focus_txid: "",
      findings: [],
      disposition: [],
      nodes: [...nodeMap.values()],
      edges,
      exits,
      warnings,
      stats: { addresses: nodeMap.size, edges: edges.length,
               exit_points: exits.length },
    });
  } finally {
    $("btn-case-overlay").disabled = false;
  }
}

/* ===================== IC3 complaint helper ===================== */

// Working copies of the repeatable blocks (rendered into the dialog).
let ic3Transactions = [];
let ic3Subjects = [];
let ic3TxTypes = ["Cryptocurrency/Crypto ATM"];
let ic3CaseId = null;

const IC3_TX_FIELDS = [
  ["transaction_type", "Transaction type", "select"],
  ["amount_usd", "Amount (USD)", "text"],
  ["crypto_amount", "Crypto amount (reference)", "text"],
  ["date", "Date (YYYY-MM-DD)", "text"],
  ["sent_or_lost", "Sent or lost?", "sentlost"],
  ["crypto_type", "Type of cryptocurrency", "text"],
  ["tx_hash", "Transaction ID / hash", "text"],
  ["originating_wallet", "Originating wallet (victim)", "text"],
  ["recipient_wallet", "Recipient wallet", "text"],
  ["originating_platform", "Originating platform/exchange", "text"],
  ["recipient_platform", "Recipient platform (if known)", "text"],
  ["kiosk_name", "Crypto ATM/kiosk name (if used)", "text"],
  ["kiosk_address", "Crypto ATM/kiosk address", "text"],
];

const IC3_SUBJECT_FIELDS = [
  ["name", "Name (as given to victim)"],
  ["business", "Business name"],
  ["address", "Address"],
  ["phone", "Phone number"],
  ["email", "Email address"],
  ["website_social", "Website / social media"],
  ["ip", "IP address"],
];

function ic3EmptyTransaction() {
  const tx = {};
  for (const [key] of IC3_TX_FIELDS) tx[key] = "";
  tx.transaction_type = "Cryptocurrency/Crypto ATM";
  tx.sent_or_lost = "Sent";
  tx.contacted_institution = "";
  return tx;
}

function renderIc3Transactions() {
  const container = $("ic3-transactions");
  container.innerHTML = "";
  ic3Transactions.forEach((tx, index) => {
    const block = document.createElement("div");
    block.className = "ic3-block";
    const head = document.createElement("div");
    head.className = "ic3-block-head";
    head.innerHTML = `<b>Payment #${index + 1}</b>`;
    const remove = document.createElement("button");
    remove.className = "secondary small";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      ic3Transactions.splice(index, 1);
      renderIc3Transactions();
    });
    head.append(remove);
    block.append(head);

    const grid = document.createElement("div");
    grid.className = "grid2";
    for (const [key, label, type] of IC3_TX_FIELDS) {
      const wrap = document.createElement("label");
      wrap.className = "field-label";
      wrap.textContent = label;
      let input;
      if (type === "select") {
        input = document.createElement("select");
        for (const option of ic3TxTypes) {
          input.append(new Option(option, option));
        }
      } else if (type === "sentlost") {
        input = document.createElement("select");
        input.append(new Option("Sent", "Sent"), new Option("Lost", "Lost"));
      } else {
        input = document.createElement("input");
      }
      input.value = tx[key] || "";
      input.addEventListener("input", () => { tx[key] = input.value; });
      wrap.append(input);
      grid.append(wrap);
    }
    block.append(grid);
    container.append(block);
  });
}

function renderIc3Subjects() {
  const container = $("ic3-subjects");
  container.innerHTML = "";
  ic3Subjects.forEach((subject, index) => {
    const block = document.createElement("div");
    block.className = "ic3-block";
    const head = document.createElement("div");
    head.className = "ic3-block-head";
    head.innerHTML = `<b>Subject #${index + 1}</b>`;
    const remove = document.createElement("button");
    remove.className = "secondary small";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      ic3Subjects.splice(index, 1);
      renderIc3Subjects();
    });
    head.append(remove);
    block.append(head);

    const grid = document.createElement("div");
    grid.className = "grid2";
    for (const [key, label] of IC3_SUBJECT_FIELDS) {
      const wrap = document.createElement("label");
      wrap.className = "field-label";
      wrap.textContent = label;
      const input = document.createElement("input");
      input.value = subject[key] || "";
      input.addEventListener("input", () => { subject[key] = input.value; });
      wrap.append(input);
      grid.append(wrap);
    }
    block.append(grid);
    container.append(block);
  });
}

function updateIc3Counters() {
  const desc = $("ic3-description");
  $("ic3-desc-count").textContent =
    `${desc.value.length.toLocaleString()} / ` +
    `${Number(desc.maxLength).toLocaleString()} characters`;
  const tech = $("ic3-technical");
  $("ic3-tech-count").textContent =
    `${tech.value.length.toLocaleString()} / ` +
    `${Number(tech.maxLength).toLocaleString()} characters`;
}

async function openIc3() {
  const caseId = Number($("case-select").value);
  if (!caseId) {
    alert("Create or choose a case first (Step 1) — the IC3 worksheet is " +
          "saved with the case.");
    return;
  }
  ic3CaseId = caseId;
  const payload = await api(`/api/cases/${caseId}/ic3`);
  const draft = payload.draft;
  ic3TxTypes = payload.transaction_types ||
    ["Cryptocurrency/Crypto ATM", "Other"];

  // Character limits straight from the real form.
  $("ic3-description").maxLength = payload.limits.description;
  $("ic3-technical").maxLength = payload.limits.technical_details;
  $("ic3-witnesses").maxLength = payload.limits.witnesses;
  $("ic3-agencies").maxLength = payload.limits.other_agencies;

  // Section 1
  $("ic3-filing-self").checked = draft.filer.filing_for_self !== false;
  $("ic3-filer-fields").classList.toggle("hidden",
    $("ic3-filing-self").checked);
  $("ic3-filer-name").value = draft.filer.name || "";
  $("ic3-filer-phone").value = draft.filer.phone || "";
  $("ic3-filer-email").value = draft.filer.email || "";
  $("ic3-filer-business").value = draft.filer.business || "";
  // Section 2
  const person = draft.complainant;
  $("ic3-c-name").value = person.name || "";
  $("ic3-c-phone").value = person.phone || "";
  $("ic3-c-address").value = person.address || "";
  $("ic3-c-email").value = person.email || "";
  $("ic3-c-city").value = person.city || "";
  $("ic3-c-county").value = person.county || "";
  $("ic3-c-state").value = person.state || "";
  $("ic3-c-zip").value = person.zip || "";
  $("ic3-c-country").value = person.country || "United States";
  $("ic3-c-age").value = person.age_range || "";
  $("ic3-c-minor").checked = !!person.is_minor;
  // Section 3
  $("ic3-money-sent").checked = draft.financial.money_sent_or_lost !== false;
  $("ic3-total-loss").value = draft.financial.total_loss_usd || "";
  ic3Transactions = (draft.financial.transactions || []).map(
    (tx) => ({ ...ic3EmptyTransaction(), ...tx }));
  renderIc3Transactions();
  // Section 4
  ic3Subjects = (draft.subjects || []).map((s) => ({ ...s }));
  renderIc3Subjects();
  // Sections 5-6
  $("ic3-description").value = draft.description || "";
  $("ic3-technical").value = draft.other.technical_details || "";
  $("ic3-witnesses").value = draft.other.witnesses || "";
  $("ic3-agencies").value = draft.other.other_agencies || "";
  $("ic3-is-update").checked = !!draft.other.is_update;
  updateIc3Counters();

  $("btn-ic3-prefill").disabled = !payload.prefill_available;
  $("btn-ic3-prefill").title = payload.prefill_available ? "" :
    "Run a trace in this case first, then the payments can be prefilled.";
  $("btn-ic3-ai-draft").classList.toggle(
    "hidden",
    !(aiStatus && aiStatus.configured && payload.prefill_available));
  $("ic3-ai-suggestion").classList.add("hidden");
  $("ic3-worksheet-link").href = `/api/cases/${caseId}/ic3/worksheet.pdf`;
  $("ic3-save-state").textContent = payload.has_saved
    ? `Last saved ${payload.updated_utc}` : "Not saved yet";
  $("ic3-notes").classList.add("hidden");
  $("ic3-dialog").showModal();
}

function collectIc3Draft() {
  return {
    filer: {
      filing_for_self: $("ic3-filing-self").checked,
      name: $("ic3-filer-name").value.trim(),
      phone: $("ic3-filer-phone").value.trim(),
      email: $("ic3-filer-email").value.trim(),
      business: $("ic3-filer-business").value.trim(),
    },
    complainant: {
      name: $("ic3-c-name").value.trim(),
      phone: $("ic3-c-phone").value.trim(),
      address: $("ic3-c-address").value.trim(),
      email: $("ic3-c-email").value.trim(),
      city: $("ic3-c-city").value.trim(),
      county: $("ic3-c-county").value.trim(),
      state: $("ic3-c-state").value.trim(),
      zip: $("ic3-c-zip").value.trim(),
      country: $("ic3-c-country").value.trim(),
      age_range: $("ic3-c-age").value,
      is_minor: $("ic3-c-minor").checked,
    },
    financial: {
      money_sent_or_lost: $("ic3-money-sent").checked,
      total_loss_usd: $("ic3-total-loss").value.trim(),
      transactions: ic3Transactions,
    },
    subjects: ic3Subjects,
    description: $("ic3-description").value,
    other: {
      technical_details: $("ic3-technical").value,
      witnesses: $("ic3-witnesses").value,
      other_agencies: $("ic3-agencies").value,
      is_update: $("ic3-is-update").checked,
    },
  };
}

async function saveIc3() {
  if (!ic3CaseId) return;
  $("btn-ic3-save").disabled = true;
  try {
    await api(`/api/cases/${ic3CaseId}/ic3`, {
      method: "POST",
      body: JSON.stringify({ data: collectIc3Draft() }),
    });
    $("ic3-save-state").textContent = "Saved.";
  } catch (error) {
    $("ic3-save-state").textContent = `Save failed: ${error.message}`;
  } finally {
    $("btn-ic3-save").disabled = false;
  }
}

async function prefillIc3() {
  if (!ic3CaseId) return;
  try {
    const prefill = await api(`/api/cases/${ic3CaseId}/ic3/prefill`);
    // Append only payments not already on the worksheet (by tx hash+wallet).
    const existing = new Set(ic3Transactions.map(
      (tx) => `${tx.tx_hash}|${tx.recipient_wallet}`));
    let added = 0;
    for (const tx of prefill.transactions) {
      const key = `${tx.tx_hash}|${tx.recipient_wallet}`;
      if (existing.has(key)) continue;
      ic3Transactions.push({ ...ic3EmptyTransaction(), ...tx });
      added += 1;
    }
    renderIc3Transactions();
    if (!$("ic3-description").value.trim()) {
      $("ic3-description").value = prefill.description || "";
      updateIc3Counters();
    }
    const notes = [`${added} payment(s) added from the latest trace.`]
      .concat(prefill.notes || []);
    const box = $("ic3-notes");
    box.innerHTML = notes.map((n) => `• ${n}`).join("<br>");
    box.classList.remove("hidden", "blocked");
  } catch (error) {
    const box = $("ic3-notes");
    box.textContent = `Prefill failed: ${error.message}`;
    box.classList.remove("hidden");
    box.classList.add("blocked");
  }
}

/* ===================== settings dialog ===================== */

const AGENCY_FIELDS = [
  ["setting-agency-name", "agency_name"],
  ["setting-agency-unit", "agency_unit"],
  ["setting-agency-address", "agency_address"],
  ["setting-agency-phone", "agency_phone"],
  ["setting-officer-name", "officer_name"],
  ["setting-officer-title", "officer_title"],
  ["setting-officer-badge", "officer_badge"],
  ["setting-officer-email", "officer_email"],
];

async function quitProgram() {
  if (!confirm("Stop Crypto Investigator? Any trace still running will be " +
               "interrupted (it can be re-run later).")) return;
  try {
    await api("/api/shutdown", { method: "POST" });
  } catch (err) {
    alert(`Could not stop the program: ${err.message}`);
    return;
  }
  document.body.innerHTML =
    "<main style='padding:3rem;font-family:system-ui'><h1>Crypto " +
    "Investigator has stopped.</h1><p>You can close this tab. Start the " +
    "program again from the Start menu or desktop shortcut.</p></main>";
}

async function openSettings() {
  const settings = await api("/api/settings");
  $("setting-etherscan-key").value = "";
  $("etherscan-key-state").textContent = settings.etherscan_api_key_masked
    ? `Current key: ${settings.etherscan_api_key_masked}`
    : "No key configured yet.";
  $("setting-coingecko-key").value = "";
  $("coingecko-key-state").textContent = settings.coingecko_api_key_masked
    ? `Current key: ${settings.coingecko_api_key_masked}`
    : "No key yet — the free “demo” key makes USD pricing far more reliable.";
  $("setting-trongrid-key").value = "";
  $("trongrid-key-state").textContent = settings.trongrid_api_key_masked
    ? `Current key: ${settings.trongrid_api_key_masked}`
    : "No key yet — Tron tracing works without one but is rate-limited.";
  $("setting-btc-base").value = settings.bitcoin_api_base;
  $("setting-eth-base").value = settings.etherscan_api_base;
  $("setting-ethereum-mode").value = settings.ethereum_api_mode || "auto";
  $("setting-ai-provider").value = settings.ai_provider || "";
  $("setting-ai-key").value = "";
  $("ai-key-state").textContent = settings.ai_api_key_masked
    ? `Current key: ${settings.ai_api_key_masked}`
    : "No key stored (a local endpoint may not need one).";
  $("setting-ai-model").value = settings.ai_model || "";
  $("setting-ai-base").value = settings.ai_base_url || "";
  $("setting-ai-workspace").value = settings.ai_workspace_id || "";
  updateAiSettingsHints();
  $("setting-watch-interval").value = settings.watch_interval_minutes;
  $("setting-labels-autorefresh").checked =
    settings.labels_autorefresh !== "off";
  for (const [id, key] of AGENCY_FIELDS) {
    $(id).value = settings[key] || "";
  }
  // Label freshness next to each download button.
  const freshness = (lastMeta || {}).labels_freshness || {};
  const freshText = (source) => freshness[source]
    ? `Last updated ${freshness[source].slice(0, 10)}.`
    : "Not downloaded yet.";
  $("ofac-status").textContent = freshText("ofac_sdn");
  $("tagpacks-status").textContent = freshText("graphsense_tagpack");
  $("scamsniffer-status").textContent = freshText("scamsniffer");
  $("setting-data-dir").textContent = (lastMeta || {}).data_dir || "";
  $("settings-dialog").showModal();
}

async function saveSettings() {
  const body = {
    bitcoin_api_base: $("setting-btc-base").value,
    etherscan_api_base: $("setting-eth-base").value,
    ethereum_api_mode: $("setting-ethereum-mode").value,
    watch_interval_minutes: $("setting-watch-interval").value,
    labels_autorefresh: $("setting-labels-autorefresh").checked
      ? "on" : "off",
  };
  for (const [id, key] of AGENCY_FIELDS) {
    body[key] = $(id).value;
  }
  const key = $("setting-etherscan-key").value.trim();
  if (key) body.etherscan_api_key = key;
  const geckoKey = $("setting-coingecko-key").value.trim();
  if (geckoKey) body.coingecko_api_key = geckoKey;
  const tronKey = $("setting-trongrid-key").value.trim();
  if (tronKey) body.trongrid_api_key = tronKey;
  body.ai_provider = $("setting-ai-provider").value;
  body.ai_model = $("setting-ai-model").value;
  body.ai_base_url = $("setting-ai-base").value;
  body.ai_workspace_id = $("setting-ai-workspace").value;
  const aiKey = $("setting-ai-key").value.trim();
  if (aiKey) body.ai_api_key = aiKey;
  await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
  $("settings-dialog").close();
  refreshMeta();
  await refreshAiStatus();
  updateAiPanel();
}

const AI_MODEL_PLACEHOLDERS = {
  anthropic: "e.g. claude-opus-5",
  openai: "e.g. gpt-5",
  custom: "your model's name on the endpoint (e.g. llama3.3)",
};

function updateAiSettingsHints() {
  const provider = $("setting-ai-provider").value;
  $("setting-ai-model").placeholder =
    AI_MODEL_PLACEHOLDERS[provider] || "choose a provider first";
  $("ai-workspace-row").classList.toggle("hidden",
    provider !== "anthropic");
}

/** Shared handler for the label-source download buttons in Settings. */
function wireLabelRefresh(buttonId, statusId, path, describe) {
  $(buttonId).addEventListener("click", async () => {
    const status = $(statusId);
    status.textContent = "Downloading… (may take a minute)";
    $(buttonId).disabled = true;
    try {
      const summary = await api(path, { method: "POST" });
      status.textContent = describe(summary);
    } catch (error) {
      status.textContent = `Failed: ${error.message}`;
    } finally {
      $(buttonId).disabled = false;
      refreshMeta();
    }
  });
}

async function refreshOfac() {
  const status = $("ofac-status");
  status.textContent = "Downloading OFAC SDN list… (about a minute)";
  $("btn-refresh-ofac").disabled = true;
  try {
    const summary = await api("/api/labels/refresh-ofac", { method: "POST" });
    status.textContent = `Loaded ${summary.addresses} sanctioned addresses ` +
      `from ${summary.entities} entities.`;
  } catch (error) {
    status.textContent = `Failed: ${error.message}`;
  } finally {
    $("btn-refresh-ofac").disabled = false;
    refreshMeta();
  }
}

/* ===================== wiring ===================== */

document.addEventListener("DOMContentLoaded", () => {
  refreshMeta();
  refreshCases().then(updateCaseTools);
  refreshFlags();
  refreshWatches();
  refreshAiStatus();
  refreshTicker();

  // AI assistant
  $("btn-ai-summary").addEventListener("click", aiSummary);
  $("btn-ai-ask").addEventListener("click", aiAsk);
  $("ai-question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      aiAsk();
    }
  });
  $("btn-ai-log").addEventListener("click", openAiLog);
  $("btn-ai-log-close").addEventListener("click",
    () => $("ai-log-dialog").close());
  $("setting-ai-provider").addEventListener("change",
    updateAiSettingsHints);
  $("btn-ic3-ai-draft").addEventListener("click", async () => {
    if (!ic3CaseId) return;
    $("btn-ic3-ai-draft").disabled = true;
    $("btn-ic3-ai-draft").textContent = "🤖 Drafting…";
    try {
      const reply = await api(`/api/cases/${ic3CaseId}/ai/ic3-narrative`,
                              { method: "POST" });
      $("ic3-ai-text").textContent = reply.text;
      $("ic3-ai-suggestion").classList.remove("hidden");
    } catch (error) {
      alert(`AI draft failed: ${error.message}`);
    } finally {
      $("btn-ic3-ai-draft").disabled = false;
      $("btn-ic3-ai-draft").textContent =
        "🤖 AI: suggest a draft from the trace";
    }
  });
  $("btn-ic3-ai-use").addEventListener("click", () => {
    const current = $("ic3-description").value.trim();
    if (current && !confirm("Replace the current description with the "
                            + "AI draft? The current text will be lost.")) {
      return;
    }
    $("ic3-description").value = $("ic3-ai-text").textContent;
    updateIc3Counters();
    $("ic3-ai-suggestion").classList.add("hidden");
  });
  $("btn-ic3-ai-dismiss").addEventListener("click",
    () => $("ic3-ai-suggestion").classList.add("hidden"));
  setInterval(refreshTicker, TICKER_REFRESH_MS);
  // Keep the watch-alert badge current without reloading the page.
  setInterval(refreshMeta, 120000);

  function updateCaseTools() {
    const caseId = Number($("case-select").value);
    $("btn-case-export").href =
      caseId ? `/api/cases/${caseId}/export.json` : "#";
    refreshAnnotations();
  }
  $("case-select").addEventListener("change", updateCaseTools);

  // Watched wallets dialog
  $("btn-watches").addEventListener("click", openWatches);
  $("btn-watches-close").addEventListener("click",
    () => $("watches-dialog").close());
  $("btn-watch-add").addEventListener("click", async () => {
    const address = $("watch-add-address").value.trim();
    if (address.length < 8) {
      alert("Paste the wallet address to watch."); return;
    }
    const caseId = $("watch-add-link-case").checked
      ? Number($("case-select").value) || null : null;
    try {
      await addWatch(address, $("watch-add-chain").value,
                     $("watch-add-note").value.trim(), caseId);
      $("watch-add-address").value = "";
      $("watch-add-note").value = "";
      $("watch-add-status").textContent = "Watching ✓";
      renderWatchesList();
    } catch (error) {
      $("watch-add-status").textContent = `Failed: ${error.message}`;
    }
  });
  $("btn-watches-check").addEventListener("click", async () => {
    $("btn-watches-check").disabled = true;
    $("watch-add-status").textContent = "Checking every watch…";
    try {
      const summary = await api("/api/watches/check-now",
                                { method: "POST" });
      $("watch-add-status").textContent =
        `Checked ${summary.checked} wallet(s); ` +
        `${summary.new_alerts} new alert(s).`;
      await refreshWatches();
      refreshMeta();
      renderWatchesList();
    } catch (error) {
      $("watch-add-status").textContent = `Check failed: ${error.message}`;
    } finally {
      $("btn-watches-check").disabled = false;
    }
  });

  // Custodian directory dialog
  $("btn-custodians").addEventListener("click", openCustodians);
  $("btn-custodians-close").addEventListener("click",
    () => $("custodians-dialog").close());
  $("custodian-search").addEventListener("input",
    () => renderCustodians($("custodian-search").value));

  // Case overlay / export / import
  $("btn-case-overlay").addEventListener("click", openCaseOverlay);
  $("btn-case-import").addEventListener("click",
    () => $("case-import-file").click());
  $("case-import-file").addEventListener("change", async () => {
    const file = $("case-import-file").files[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const outcome = await api("/api/cases/import", {
        method: "POST", body: JSON.stringify({ data: payload }),
      });
      await refreshCases(outcome.case_id);
      updateCaseTools();
      alert("Case imported.");
    } catch (error) {
      alert(`Import failed: ${error.message}`);
    } finally {
      $("case-import-file").value = "";
    }
  });

  // Map image export (print-resolution exhibit)
  $("btn-map-image").addEventListener("click", () => {
    if (!cy) { alert("Run a trace first."); return; }
    const link = document.createElement("a");
    link.href = cy.png({ full: true, scale: 3, bg: "#ffffff" });
    link.download = "money_flow_map" +
      (currentTraceId !== null ? `_trace${currentTraceId}` : "_overlay") +
      ".png";
    link.click();
  });

  // Direction / chain interplay with the focus-transaction field.
  function updateFocusAvailability() {
    const backward = document.querySelector(
      "input[name='trace-direction']:checked").value === "backward";
    const tron = $("chain-select").value === "tron";
    $("focus-tx-input").disabled = backward || tron;
    $("focus-tx-input").placeholder = backward
      ? "Not used for backward (source-of-funds) traces"
      : tron ? "Not yet supported on Tron — leave blank"
      : "Transaction hash of the payment to trace (optional)";
  }
  for (const radio of document.querySelectorAll(
      "input[name='trace-direction']")) {
    radio.addEventListener("change", updateFocusAvailability);
  }
  $("chain-select").addEventListener("change", updateFocusAvailability);

  // Flagged wallets dialog
  $("btn-flags").addEventListener("click", openFlags);
  $("btn-flags-close").addEventListener("click",
    () => $("flags-dialog").close());
  $("btn-flag-add").addEventListener("click", async () => {
    const address = $("flag-add-address").value.trim();
    if (address.length < 8) {
      alert("Paste the wallet address to flag."); return;
    }
    const caseId = $("flag-add-link-case").checked
      ? Number($("case-select").value) || null : null;
    try {
      await addFlag(address, $("flag-add-chain").value,
                    $("flag-add-reason").value.trim(), caseId);
      $("flag-add-address").value = "";
      $("flag-add-reason").value = "";
      $("flag-add-status").textContent = "Flagged ✓";
      renderFlagsList();
      if (cy) redrawGraph();
    } catch (error) {
      $("flag-add-status").textContent = `Failed: ${error.message}`;
    }
  });

  // Label-source downloads (Settings)
  wireLabelRefresh("btn-refresh-tagpacks", "tagpacks-status",
    "/api/labels/refresh-tagpacks",
    (s) => `Loaded ${s.labels.toLocaleString()} labels from ${s.packs} ` +
      `pack(s)` + (s.skipped_packs.length
        ? ` (${s.skipped_packs.length} pack(s) skipped)` : "") + ".");
  wireLabelRefresh("btn-refresh-scamsniffer", "scamsniffer-status",
    "/api/labels/refresh-scamsniffer",
    (s) => `Loaded ${s.addresses.toLocaleString()} reported scam addresses.`);
  $("dust-btc").addEventListener("input", updateDustHints);
  $("dust-eth").addEventListener("input", updateDustHints);

  // Money-flow map controls
  $("btn-graph-fit").addEventListener("click",
    () => { if (cy) cy.fit(undefined, 30); });
  const zoomBy = (factor) => {
    if (!cy) return;
    cy.zoom({
      level: cy.zoom() * factor,
      renderedPosition: { x: $("graph").clientWidth / 2,
                          y: $("graph").clientHeight / 2 },
    });
  };
  $("btn-graph-zoom-in").addEventListener("click", () => zoomBy(1.3));
  $("btn-graph-zoom-out").addEventListener("click", () => zoomBy(1 / 1.3));
  $("btn-graph-spread").addEventListener("click", () => {
    graphSpread = Math.min(graphSpread * 1.3, 3.0);
    redrawGraph();
  });
  $("btn-graph-tighten").addEventListener("click", () => {
    graphSpread = Math.max(graphSpread / 1.3, 0.5);
    redrawGraph();
  });
  $("btn-graph-expand-all").addEventListener("click", () => {
    $("collapse-chains").checked = false;
    $("collapse-entities").checked = false;
    $("collapse-minor").checked = false;
    redrawGraph();
  });
  for (const toggleId of ["collapse-chains", "collapse-entities",
                          "collapse-minor"]) {
    $(toggleId).addEventListener("change", () => redrawGraph());
  }

  let detectDebounce = null;
  $("victim-input").addEventListener("input", () => {
    clearTimeout(detectDebounce);
    detectDebounce = setTimeout(detectVictimInput, 350);
  });

  let focusDebounce = null;
  $("focus-tx-input").addEventListener("input", () => {
    clearTimeout(focusDebounce);
    focusDebounce = setTimeout(detectFocusTxInput, 350);
  });

  $("depth-input").addEventListener("input", () => {
    $("depth-value").textContent = $("depth-input").value;
  });

  // Extended mode overrides the depth slider (follow until an exchange).
  $("extended-mode").addEventListener("change", () => {
    $("depth-input").disabled = $("extended-mode").checked;
    $("depth-value").textContent = $("extended-mode").checked
      ? "auto (until exchange)" : $("depth-input").value;
  });

  $("btn-create-case").addEventListener("click", async () => {
    const name = $("new-case-name").value.trim();
    if (!name) { alert("Give the case a name."); return; }
    const { id } = await api("/api/cases", {
      method: "POST",
      body: JSON.stringify({
        name, case_number: $("new-case-number").value.trim(),
      }),
    });
    $("new-case-name").value = "";
    $("new-case-number").value = "";
    $("new-case-details").open = false;
    await refreshCases(id);
    updateCaseTools();
  });

  $("btn-trace").addEventListener("click", startTrace);

  // IC3 complaint helper
  $("btn-ic3").addEventListener("click", openIc3);
  $("btn-ic3-close").addEventListener("click", () => $("ic3-dialog").close());
  $("btn-ic3-save").addEventListener("click", saveIc3);
  $("btn-ic3-prefill").addEventListener("click", prefillIc3);
  $("btn-ic3-add-tx").addEventListener("click", () => {
    ic3Transactions.push(ic3EmptyTransaction());
    renderIc3Transactions();
  });
  $("btn-ic3-add-subject").addEventListener("click", () => {
    ic3Subjects.push({});
    renderIc3Subjects();
  });
  $("ic3-filing-self").addEventListener("change", () => {
    $("ic3-filer-fields").classList.toggle("hidden",
      $("ic3-filing-self").checked);
  });
  $("ic3-description").addEventListener("input", updateIc3Counters);
  $("ic3-technical").addEventListener("input", updateIc3Counters);

  $("btn-settings").addEventListener("click", openSettings);
  $("btn-quit").addEventListener("click", quitProgram);
  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-close-settings").addEventListener("click",
    () => $("settings-dialog").close());
  $("btn-refresh-ofac").addEventListener("click", refreshOfac);
});
