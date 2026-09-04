"""
Court-ready PDF report for one trace.

Structure:
  1. Case summary (plain language, one page)
  2. Identified exit points - the legal-service targets, ranked
  3. Draft subpoena/warrant language per custodian (marked DRAFT)
  4. Address table (role + attribution basis for every address)
  5. Transaction table (every followed movement)
  6. Methodology & limitations appendix
  7. Chain-of-custody appendix (every data pull with hash)

Design rule carried through every section: on-chain FACTS (transactions,
amounts, timestamps) are presented separately from INFERENCES (roles,
clusters, attributions), and every inference states its source and
confidence.
"""

from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from app import config

# Layout constants.
PAGE_MARGIN = 0.7 * inch
TABLE_FONT_SIZE = 7
MAX_CUSTODY_ROWS = 400   # keeps the PDF manageable; full log exports as CSV

# Confidence explanations reused in several sections.
CONFIDENCE_LEGEND = (
    "Confidence levels: HIGH = official source (e.g. US Treasury OFAC SDN "
    "list) or multiple corroborating sources. MEDIUM = a single reputable "
    "public/community source (e.g. block-explorer entity tag); unverified. "
    "LOW = heuristic inference produced by this tool's own analysis."
)

DRAFT_LEGAL_DISCLAIMER = (
    "DRAFT - FOR REVIEW BY PROSECUTOR/COUNSEL. This language is a template "
    "produced by an investigative tool. It is not legal advice and must be "
    "reviewed and adapted by the prosecuting authority before service."
)


def _styles():
    """Paragraph styles for the report."""
    base = getSampleStyleSheet()
    return {
        "title": base["Title"],
        "h1": base["Heading1"],
        "h2": base["Heading2"],
        "body": base["BodyText"],
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=8,
                                leading=10),
        "mono": ParagraphStyle("mono", parent=base["BodyText"],
                               fontName="Courier", fontSize=7, leading=9),
        "warn": ParagraphStyle("warn", parent=base["BodyText"],
                               textColor=colors.HexColor("#8a4b00")),
    }


def _fmt_ts(unix_ts) -> str:
    """Unix seconds -> 'YYYY-MM-DD HH:MM:SS UTC' (or a dash)."""
    if not unix_ts:
        return "-"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc
                                  ).strftime("%Y-%m-%d %H:%M:%S UTC")


def _table(data, col_widths, style_extra=None):
    """Uniform small-font table with header row."""
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), TABLE_FONT_SIZE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b7c1cc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f8fa")]),
    ]
    table.setStyle(TableStyle(style + (style_extra or [])))
    return table


def _mono_cell(text, styles):
    """Monospace paragraph so long addresses/txids wrap inside cells."""
    return Paragraph(str(text), styles["mono"])


def build_report(pdf_path: str, case: dict, trace_row: dict, result: dict,
                 custody_entries: list, annotations: dict = None) -> None:
    """Render the full PDF to `pdf_path`. `annotations` maps address ->
    investigator note (from the case's annotations) and is shown in the
    address table, clearly marked as the investigator's own note."""
    styles = _styles()
    annotations = annotations or {}
    backward = result.get("direction") == "backward"
    story = []
    doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                            leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
                            topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
                            title=f"Fund Tracing Report - {case['name']}")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ------------------------------------------------ 1. Case summary
    story.append(Paragraph("Cryptocurrency Fund-Tracing Report",
                           styles["title"]))
    story.append(Spacer(1, 8))
    summary_rows = [
        ["Case name", case["name"]],
        ["Case number", case.get("case_number") or "-"],
        ["Report generated", generated],
        ["Tool", f"{config.APP_NAME} v{config.APP_VERSION}"],
        ["Blockchain", result["chain"].capitalize()],
        ["Trace direction",
         ("BACKWARD - source of funds (where did this wallet's money "
          "come from?)") if backward else
         "Forward - follow the money out of the victim's wallet"],
        [("Target wallet (starting point)" if backward
          else "Victim wallet (starting point)"), result["start_input"]],
        ["Focus transaction",
         result.get("focus_txid") or "(none - all outgoing movements "
                                     "from the wallet were followed)"],
        ["Trace started", trace_row["started_utc"]],
        ["Trace finished", trace_row.get("finished_utc") or "-"],
        ["Accounting method", result["accounting_method"]],
        ["Search pattern", result.get("search_pattern", "thorough")],
        ["Addresses examined", str(result["stats"]["addresses"])],
        ["Movements followed", str(result["stats"]["edges"])],
        ["Exit points found", str(result["stats"]["exit_points"])],
    ]
    story.append(_table(
        [[Paragraph(f"<b>{k}</b>", styles["small"]),
          Paragraph(str(v), styles["small"])] for k, v in summary_rows],
        [1.7 * inch, 5.3 * inch]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "This report follows funds on a public blockchain from the starting "
        "point supplied by the investigator to the point(s) where they reach "
        "a custodial service (an exchange or similar business) on which "
        "legal process can be served. This tool does not identify natural "
        "persons; account-holder identity must be obtained from the "
        "custodian's KYC records via subpoena or warrant.", styles["body"]))
    story.append(Paragraph(CONFIDENCE_LEGEND, styles["small"]))

    if result.get("warnings"):
        story.append(Spacer(1, 8))
        story.append(Paragraph("Notices and trace limits encountered:",
                               styles["h2"]))
        for warning in result["warnings"]:
            story.append(Paragraph(f"• {warning}", styles["warn"]))

    # ------------------------------ 2. Investigative summary (findings)
    findings = result.get("findings") or []
    disposition = result.get("disposition") or []
    if findings or disposition:
        story.append(PageBreak())
        story.append(Paragraph("Investigative Summary", styles["h1"]))
        if disposition:
            story.append(Paragraph(
                ("Origin of traced funds - every branch ending is "
                 "accounted for. Amounts are traced value sent from each "
                 "terminal source (backward taint-by-touch; co-mingled "
                 "funds included).") if backward else
                ("Disposition of traced funds - every branch ending is "
                 "accounted for. Amounts are traced value arriving at "
                 "each terminal point (taint-by-touch; co-mingled funds "
                 "included)."), styles["small"]))
            dispo_rows = [["Outcome", "Addresses", "Traced value received",
                           "≈USD", "Share"]]
            for row in disposition:
                totals = ", ".join(f"{v:,.6f} {a}"
                                   for a, v in row["totals"].items()) or "-"
                dispo_rows.append([
                    Paragraph(row["bucket"], styles["small"]),
                    str(row["count"]),
                    Paragraph(totals, styles["small"]),
                    (f"${row['usd']:,.2f}"
                     if row.get("usd_complete") and row.get("usd") else "-"),
                    (f"{row['share']}%"
                     if row.get("share") is not None else "-")])
            story.append(_table(dispo_rows,
                                [1.9 * inch, 0.7 * inch, 2.2 * inch,
                                 1.0 * inch, 0.5 * inch]))
            story.append(Spacer(1, 10))
        for finding in findings:
            story.append(Paragraph(finding["title"], styles["h2"]))
            story.append(Paragraph(finding["detail"], styles["small"]))
            story.append(Paragraph(f"<b>Recommended next step:</b> "
                                   f"{finding['action']}", styles["small"]))
            if finding.get("addresses"):
                story.append(Paragraph(
                    "Address(es): " + ", ".join(
                        f"<font face='Courier'>{a}</font>"
                        for a in finding["addresses"]), styles["small"]))
            story.append(Spacer(1, 6))

    # ------------------------------------------------ 3. Exit points
    story.append(PageBreak())
    story.append(Paragraph(
        "Identified Fund Sources (Legal-Service Targets)" if backward
        else "Identified Exit Points (Legal-Service Targets)",
        styles["h1"]))
    exits = result.get("exits", [])
    if not exits:
        story.append(Paragraph(
            ("No traced funds were followed back to a labelled custodial "
             "service within the configured depth. This does NOT mean the "
             "funds have no custodial origin: the attribution lists used "
             "are incomplete, and the origin may lie beyond the traced "
             "depth. Consider increasing depth or re-tracing from the "
             "unexpanded edge addresses listed in the address table.")
            if backward else
            ("No traced funds reached a labelled custodial service within "
             "the configured depth. This does NOT mean no off-ramp "
             "exists: the attribution lists used are incomplete, and "
             "funds may exit beyond the traced depth. Consider increasing "
             "depth, lowering the dust threshold, or re-tracing from the "
             "unexpanded edge addresses listed in the address table."),
            styles["body"]))
    for rank, exit_point in enumerate(exits, start=1):
        totals = ", ".join(f"{value:,.6f} {asset}"
                           for asset, value in exit_point["totals"].items())
        if exit_point.get("totals_usd") is not None:
            totals += (f" (approx. ${exit_point['totals_usd']:,.2f} at the "
                       f"transaction dates)")
        compliance = exit_point.get("compliance")
        compliance_title = ""
        if compliance:
            compliance_title = (" — designated "
                                + ("COMPLIANT"
                                   if compliance["status"] == "compliant"
                                   else "NON-COMPLIANT"))
        story.append(Paragraph(
            f"#{rank} — {exit_point['entity']} "
            f"({exit_point['confidence'].upper()} confidence, "
            f"source: {exit_point['source']}){compliance_title}",
            styles["h2"]))
        if compliance:
            guidance = (
                "Expect this custodian to respond to subpoenas/warrants "
                "through normal channels."
                if compliance["status"] == "compliant" else
                "This custodian may not respond to US legal process; "
                "consider MLAT or alternative strategies before funds "
                "move onward.")
            note = f" ({compliance['note']})" if compliance.get("note") \
                else ""
            story.append(Paragraph(
                f"<b>Compliance designation:</b> {guidance}{note} "
                f"<i>{compliance['source_note']}</i>", styles["small"]))
        story.append(Paragraph(
            (f"Sending address: " if backward else f"Receiving address: ")
            + f"<font face='Courier'>{exit_point['address']}</font><br/>"
            f"Hops from starting point: {exit_point['depth']} &nbsp;|&nbsp; "
            + ("Total traced value sent: " if backward
               else "Total traced value received: ") + totals,
            styles["small"]))
        counterparty_header = "Paid to address" if backward \
            else "From address"
        funding_rows = [["Txid", "Date/time", counterparty_header,
                         "Amount", "Asset", "≈USD then"]]
        for f in exit_point["funding"]:
            usd = f.get("value_usd")
            counterparty = (f.get("to_address") or "-") if backward \
                else f["from_address"]
            funding_rows.append([
                _mono_cell(f["txid"], styles), _fmt_ts(f["timestamp"]),
                _mono_cell(counterparty, styles),
                f"{f['value']:,.6f}", f["asset"],
                f"${usd:,.2f}" if usd is not None else "-"])
        story.append(_table(funding_rows,
                            [2.3 * inch, 1.05 * inch, 1.85 * inch,
                             0.7 * inch, 0.45 * inch, 0.65 * inch]))

        # Hop-by-hop path (the "traceroute" that a warrant can quote).
        # Shortest path; ties favour larger movements.
        path = exit_point.get("path") or []
        if path:
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                (f"Fund-flow path from this source to the target wallet "
                 if backward else
                 f"Fund-flow path from the victim wallet to this exit ")
                + f"({len(path)} hop(s)). Each row is one on-chain "
                f"movement; this path is also exportable as plain text "
                f"from the case screen for inclusion in legal process.",
                styles["small"]))
            path_rows = [["Hop", "From", "To", "Amount", "≈USD then",
                          "Txid", "Date/time"]]
            for hop in path:
                usd = hop.get("value_usd")
                path_rows.append([
                    str(hop["hop"]),
                    _mono_cell(hop["from_address"], styles),
                    _mono_cell(hop["to_address"], styles),
                    f"{hop['value']:,.6f} {hop['asset']}",
                    f"${usd:,.2f}" if usd is not None else "-",
                    _mono_cell(hop["txid"], styles),
                    _fmt_ts(hop["timestamp"])])
            story.append(_table(path_rows,
                                [0.35 * inch, 1.45 * inch, 1.45 * inch,
                                 0.85 * inch, 0.6 * inch, 1.5 * inch,
                                 0.9 * inch]))
        story.append(Spacer(1, 10))

    # ------------------------------------------------ 3. Draft legal language
    if exits:
        story.append(PageBreak())
        story.append(Paragraph("Draft Records Request Language", styles["h1"]))
        story.append(Paragraph(DRAFT_LEGAL_DISCLAIMER, styles["warn"]))
        story.append(Spacer(1, 6))
        for exit_point in exits:
            asset_list = ", ".join(sorted(exit_point["totals"].keys()))
            txid_list = ", ".join(f["txid"] for f in exit_point["funding"])
            date_list = ", ".join(sorted({_fmt_ts(f["timestamp"])[:10]
                                          for f in exit_point["funding"]
                                          if f["timestamp"]}))
            date_phrase = date_list or "the dates shown in the funding table"
            story.append(Paragraph(f"Target custodian: "
                                   f"<b>{exit_point['entity']}</b>",
                                   styles["h2"]))
            story.append(Paragraph(
                f"You are requested to produce, for the account associated "
                f"with " + ("sending" if backward else "deposit")
                + f" address <font face='Courier'>"
                f"{exit_point['address']}</font> on the "
                f"{result['chain'].capitalize()} blockchain, which "
                + ("sent" if backward else "received")
                + f" {asset_list} on or about {date_phrase} via "
                f"transaction(s) "
                f"<font face='Courier'>{txid_list}</font>: "
                f"(1) all customer identification and KYC records, including "
                f"name, date of birth, addresses, government identifiers and "
                f"documents; (2) all account-opening records, IP-address and "
                f"device logs; (3) complete transaction history including "
                f"deposits, withdrawals, trades, and linked bank or card "
                f"accounts; (4) all communications with the account holder; "
                f"and (5) records of any other accounts controlled by the "
                f"same person or sharing the same identifiers.",
                styles["small"]))
            story.append(Spacer(1, 8))

    # ------------------------------------------------ 4. Address table
    story.append(PageBreak())
    story.append(Paragraph("Address Table (roles are analytical inferences)",
                           styles["h1"]))
    address_rows = [["Address", "Hop", "Role", "Basis for role/attribution"]]
    for node in sorted(result["nodes"], key=lambda n: (n["depth"],
                                                       n["address"])):
        role_text = node["role"].replace("_", " ")
        if "agency_flagged" in (node.get("flags") or []):
            role_text += " [AGENCY-FLAGGED]"
        basis_text = node["basis"] or "-"
        note = annotations.get(node["address"])
        if note:
            basis_text += (f" <b>Investigator note (the investigator's "
                           f"own annotation):</b> {note}")
        address_rows.append([
            _mono_cell(node["address"], styles), str(node["depth"]),
            role_text,
            Paragraph(basis_text, styles["small"])])
    story.append(_table(address_rows,
                        [2.5 * inch, 0.4 * inch, 1.0 * inch, 3.1 * inch]))

    # ------------------------------------------------ 5. Transaction table
    story.append(PageBreak())
    story.append(Paragraph("Transaction Table (on-chain facts; USD values "
                           "are estimates at the transaction date)",
                           styles["h1"]))
    tx_rows = [["Txid", "Date/time", "From", "To", "Amount", "Asset",
                "≈USD then"]]
    for edge in result["edges"]:
        usd = edge.get("value_usd")
        tx_rows.append([
            _mono_cell(edge["txid"], styles), _fmt_ts(edge["timestamp"]),
            _mono_cell(edge["from_address"], styles),
            _mono_cell(edge["to_address"], styles),
            f"{edge['value']:,.6f}", edge["asset"],
            f"${usd:,.2f}" if usd is not None else "-"])
    story.append(_table(tx_rows, [1.7 * inch, 0.9 * inch, 1.5 * inch,
                                  1.5 * inch, 0.6 * inch, 0.35 * inch,
                                  0.55 * inch]))

    # ------------------------------------------------ 6. Methodology
    story.append(PageBreak())
    story.append(Paragraph("Methodology and Limitations", styles["h1"]))
    for paragraph in [
        f"<b>Data sources.</b> All blockchain data was obtained from public "
        f"block-explorer APIs; the chain-of-custody appendix records the "
        f"exact source and URL of every pull. Bitcoin: Esplora-compatible "
        f"APIs (mempool.space and compatible hosts). Ethereum and ERC-20 "
        f"tokens: the Etherscan V2, Blockscout, or Routescan API "
        f"(per settings). Tron and TRC-20 tokens: TronGrid API. Sanctions "
        f"attribution: the official US Treasury OFAC SDN list. Exchange/"
        f"mixer attribution: publicly documented wallet addresses from "
        f"community sources (bundled seed list and, when downloaded, "
        f"GraphSense TagPacks with per-label provenance; unverified, "
        f"MEDIUM confidence).",
        f"<b>Tracing method.</b> Starting from the supplied "
        f"{result.get('start_kind', 'starting point')}"
        + (f" (direction set by focus transaction "
           f"<font face='Courier'>{result['focus_txid']}</font>: only that "
           f"payment's outputs leaving the victim wallet were followed)"
           if result.get("focus_txid") else "")
        + f", outgoing movements were followed "
        f"breadth-first up to {result['params'].get('max_depth')} hop(s). "
        f"Movements below the dust threshold "
        f"({result['params'].get('dust_btc')} BTC / "
        f"{result['params'].get('dust_eth')} ETH / "
        f"{result['params'].get('dust_token')} token units) were not "
        f"followed. Value accounting: {result['accounting_method']}",
        "<b>Search pattern.</b> " + {
            "rapid": (
                "RAPID ('follow the bulk'): at each address, only the "
                "largest outgoing movements covering approximately 80% of "
                "the outgoing value (maximum 3 branches per address, per "
                "asset) were followed. This is a deliberate speed/coverage "
                "trade-off for identifying the primary destination "
                "quickly; smaller branches were NOT examined and are "
                "counted in the notices above and itemised in the JSON "
                "export. A Thorough re-trace should support any final "
                "charging or forfeiture accounting."),
            "balanced": (
                "BALANCED: at each address, outgoing movements carrying at "
                "least 5% of that address's outgoing value were followed "
                "(maximum 8 branches per address, per asset). Smaller "
                "branches were NOT examined and are counted in the notices "
                "above and itemised in the JSON export. A Thorough "
                "re-trace should support any final charging or forfeiture "
                "accounting."),
            "thorough": (
                "THOROUGH: every outgoing movement above the dust "
                "threshold was followed. No pattern-based branch "
                "selection was applied."),
        }.get(result.get("search_pattern", "thorough")),
        "<b>USD valuation.</b> " + result.get(
            "usd_valuation_note",
            "USD values were not computed for this trace."),
        "<b>Heuristics used.</b> (1) High-activity flag: addresses with "
        f"more than {config.HIGH_ACTIVITY_TX_THRESHOLD:,} transactions are "
        "treated as probable services and not expanded — this is an "
        "inference, not a fact. (2) Same-address change: outputs paying the "
        "spending address itself are treated as change and not followed. "
        "No other change-detection or wallet-clustering heuristic is applied "
        "in this version.",
        "<b>Known limitations of this version.</b> (a) Only the most recent "
        f"{config.MAX_OUTGOING_TXS_PER_ADDRESS} outgoing transactions per "
        "address are examined. (b) Bitcoin change outputs to fresh addresses "
        "are followed like any other output, which can include the "
        "suspect's own change in the flow (wallet clustering arrives in a "
        "later version). (c) Funds entering smart contracts (swaps, bridges, "
        "mixers) are flagged and not followed. (d) The exchange label list "
        "is a small seed set: absence of an exit finding is not evidence of "
        "absence. (e) Attribution of an address to an exchange does not "
        "identify the account holder; only the custodian's records can.",
        CONFIDENCE_LEGEND,
    ]:
        story.append(Paragraph(paragraph, styles["body"]))
        story.append(Spacer(1, 4))

    # ------------------------------------------------ 7. Chain of custody
    story.append(PageBreak())
    story.append(Paragraph("Chain-of-Custody Appendix", styles["h1"]))
    story.append(Paragraph(
        "Every data acquisition performed for this trace is listed below "
        "with its exact request URL, UTC timestamp, and the SHA-256 hash of "
        "the raw response body as received. 'cache' indicates the response "
        "was served from this tool's local evidence cache, captured earlier "
        "at the timestamp shown in the original entry. Any analyst can "
        "re-issue the same URL and compare hashes (live blockchain data "
        "appends over time; confirmed historical records are immutable).",
        styles["small"]))
    custody_rows = [["#", "UTC time", "Source", "Request URL", "SHA-256",
                     "Cache"]]
    for i, entry in enumerate(custody_entries[:MAX_CUSTODY_ROWS], start=1):
        custody_rows.append([
            str(i), entry["fetched_utc"].replace("+00:00", "Z"),
            entry["source"], _mono_cell(entry["url"], styles),
            _mono_cell((entry["sha256"] or "-")[:20] + "...", styles),
            "yes" if entry["from_cache"] else "no"])
    story.append(_table(custody_rows, [0.3 * inch, 1.0 * inch, 0.8 * inch,
                                       3.2 * inch, 1.2 * inch, 0.4 * inch]))
    if len(custody_entries) > MAX_CUSTODY_ROWS:
        story.append(Paragraph(
            f"({len(custody_entries) - MAX_CUSTODY_ROWS} further entries "
            f"omitted from the PDF; the complete log is available as a CSV "
            f"export from the case screen.)", styles["small"]))

    doc.build(story)
