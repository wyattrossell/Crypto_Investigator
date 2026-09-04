"""
DRAFT preservation / asset-freeze request letter for one wallet.

Generated for a wallet that received traced funds - normally an identified
exchange deposit address. The letter asks the custodian to (1) preserve
records, (2) place a voluntary hold on the assets pending legal process,
and (3) not notify the account holder; Attachment A is the transaction
schedule tying the wallet to the traced funds (also usable as an exhibit
to a later seizure warrant).

Honesty rules, carried through the document itself:
- Every page is watermarked DRAFT; the letter states it must be reviewed
  by the prosecuting authority / agency counsel before service.
- A freeze only works when someone actually CONTROLS the wallet's assets
  (a custodian). When the wallet is not attributed to a custodian, the
  letter says so prominently instead of pretending there is an addressee.
- Legal citations are offered as the customary starting point (18 U.S.C.
  § 2703(f) preservation; § 2705(b) non-disclosure) with an explicit note
  that counsel must confirm applicability to this custodian.
- Custodian contact details come from the agency-editable directory and
  carry its "verify before service" source note.
"""

from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from app import config

PAGE_MARGIN = 0.8 * inch

DRAFT_BANNER = (
    "DRAFT - FOR REVIEW BY PROSECUTOR / AGENCY COUNSEL BEFORE SERVICE. "
    "This document was produced by an investigative tool from trace data. "
    "It is not legal advice; authority citations must be confirmed for this "
    "custodian and jurisdiction, and the service channel must be verified "
    "on the custodian's own site before anything is sent.")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": base["Title"],
        "h2": base["Heading2"],
        "body": base["BodyText"],
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=8,
                                leading=10),
        "mono": ParagraphStyle("mono", parent=base["BodyText"],
                               fontName="Courier", fontSize=7, leading=9),
        "warn": ParagraphStyle("warn", parent=base["BodyText"],
                               textColor=colors.HexColor("#8a4b00")),
        "banner": ParagraphStyle("banner", parent=base["BodyText"],
                                 textColor=colors.HexColor("#a11212"),
                                 fontName="Helvetica-Bold"),
        "letterhead": ParagraphStyle("letterhead", parent=base["BodyText"],
                                     fontSize=11, leading=14),
    }


def _fmt_ts(unix_ts) -> str:
    if not unix_ts:
        return "-"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc
                                  ).strftime("%Y-%m-%d %H:%M:%S UTC")


def _draft_watermark(canvas, doc) -> None:
    """Light diagonal DRAFT across every page."""
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 80)
    canvas.setFillColor(colors.Color(0.85, 0.85, 0.85, alpha=0.45))
    canvas.translate(4.25 * inch, 5.5 * inch)
    canvas.rotate(45)
    canvas.drawCentredString(0, 0, "DRAFT")
    canvas.restoreState()


def _agency_line(agency: dict, key: str, placeholder: str) -> str:
    return agency.get(key) or placeholder


def build_freeze_request(pdf_path: str, case: dict, result: dict,
                         address: str, node: dict, funding_edges: list,
                         entity: str, custodian: dict, compliance: dict,
                         agency: dict, issuer_asset: str = "") -> None:
    """Render the DRAFT preservation/freeze request PDF.

    Two modes: CUSTODIAN (default - the wallet is an account at an
    exchange/service; ask the custodian to preserve records and hold the
    assets) and TOKEN ISSUER (`issuer_asset` set - the traced funds are a
    freezable stablecoin like USDT/USDC; ask the issuer to blacklist the
    address at the token-contract level, which works even for
    self-custodied wallets)."""
    styles = _styles()
    story = []
    doc = SimpleDocTemplate(
        pdf_path, pagesize=letter,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        title=f"DRAFT Preservation-Freeze Request - {case['name']}")

    chain_name = result["chain"].capitalize()
    totals = {}
    usd_total, usd_complete = 0.0, True
    timestamps = []
    for edge in funding_edges:
        totals[edge["asset"]] = totals.get(edge["asset"], 0.0) + edge["value"]
        if edge.get("value_usd") is None:
            usd_complete = False
        else:
            usd_total += edge["value_usd"]
        if edge.get("timestamp"):
            timestamps.append(edge["timestamp"])
    totals_text = ", ".join(f"{v:,.6f} {a}" for a, v in sorted(totals.items()))
    if usd_complete and usd_total:
        totals_text += (f" (approximately ${usd_total:,.2f} at the "
                        f"transaction dates)")
    date_span = "-"
    if timestamps:
        first = _fmt_ts(min(timestamps))[:10]
        last = _fmt_ts(max(timestamps))[:10]
        date_span = first if first == last else f"{first} to {last}"

    # ---------------------------------------------------------------- banner
    story.append(Paragraph(DRAFT_BANNER, styles["banner"]))
    story.append(Spacer(1, 10))

    # ------------------------------------------------------------ letterhead
    story.append(Paragraph(
        f"<b>{_agency_line(agency, 'agency_name', '[AGENCY NAME]')}</b><br/>"
        f"{_agency_line(agency, 'agency_unit', '[UNIT / DIVISION]')}<br/>"
        f"{_agency_line(agency, 'agency_address', '[AGENCY ADDRESS]')}<br/>"
        f"Tel: {_agency_line(agency, 'agency_phone', '[PHONE]')} &nbsp; "
        f"Email: {_agency_line(agency, 'officer_email', '[EMAIL]')}",
        styles["letterhead"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        datetime.now(timezone.utc).strftime("%B %d, %Y"), styles["body"]))
    story.append(Spacer(1, 8))

    # -------------------------------------------------------------- addressee
    if entity:
        addressee = (f"<b>{custodian.get('legal_name') if custodian else entity}"
                     f"</b><br/>Attn: Legal / Law-Enforcement Response Team")
        if custodian and custodian.get("portal"):
            addressee += (f"<br/>Via law-enforcement portal: "
                          f"{custodian['portal']}")
    else:
        addressee = ("<b>[CUSTODIAN NOT YET IDENTIFIED - DO NOT SERVE]</b>"
                     "<br/>Attn: Legal / Law-Enforcement Response Team")
    story.append(Paragraph(addressee, styles["body"]))
    story.append(Spacer(1, 8))

    subject = ("Token freeze / blacklisting request (token issuer)"
               if issuer_asset else "Preservation and asset-freeze request")
    story.append(Paragraph(
        f"<b>Re: {subject} - "
        f"{case.get('case_number') or case['name']} - "
        f"{chain_name} address "
        f"<font face='Courier'>{address}</font></b>", styles["body"]))
    story.append(Spacer(1, 8))

    if issuer_asset:
        story.append(Paragraph(
            f"NOTE FOR THE INVESTIGATOR (remove before service): this is "
            f"the TOKEN-ISSUER route. {entity} controls the {issuer_asset} "
            f"token contract and can freeze/blacklist the token at ANY "
            f"address - including a self-custodied wallet no exchange "
            f"controls. Issuer freezes are discretionary: send early, "
            f"attach the transaction schedule, and follow up with legal "
            f"process. Non-{issuer_asset} assets at this address are NOT "
            f"covered by this request.", styles["warn"]))
        story.append(Spacer(1, 8))
    if not entity:
        story.append(Paragraph(
            "WARNING: this wallet is NOT attributed to a known custodian in "
            "the trace data. A freeze request can only be actioned by "
            "whoever controls the wallet's assets. Identify the service "
            "behind this address first (block-explorer and Chainabuse "
            "lookups from the case screen; large service wallets are "
            "usually publicly tagged), then complete the addressee block. "
            "If the address is self-custodied (controlled by the suspect "
            "personally), no third party can freeze it - the remedies are "
            "monitoring, seizure of keys/devices under warrant, or waiting "
            "for the funds to reach a custodian.", styles["warn"]))
        story.append(Spacer(1, 8))
    if compliance and compliance.get("status") == "non_compliant":
        story.append(Paragraph(
            "NOTE FOR THE INVESTIGATOR (remove before service): this "
            "custodian is designated NON-COMPLIANT on the agency's "
            "compliance list - it may not respond to US legal process. "
            "Consult counsel on MLAT or alternative strategies, and send "
            "any voluntary-freeze outreach as early as possible; funds "
            "often move before compelled process can land. "
            + (compliance.get("note") or ""), styles["warn"]))
        story.append(Spacer(1, 8))

    # ------------------------------------------------------------------ body
    officer = _agency_line(agency, "officer_name", "[OFFICER NAME]")
    officer_title = _agency_line(agency, "officer_title", "[TITLE / RANK]")
    officer_badge = _agency_line(agency, "officer_badge", "[BADGE / ID]")
    agency_name = _agency_line(agency, "agency_name", "[AGENCY NAME]")

    if issuer_asset:
        identification = (
            f"Blockchain analysis conducted in this investigation shows "
            f"that between {date_span}, the {chain_name} address "
            f"<font face='Courier'>{address}</font> received {totals_text} "
            f"traceable to the victim's funds, via the transaction(s) "
            f"itemised in Attachment A, including {issuer_asset} issued "
            f"by your institution. Transaction identifiers, amounts and "
            f"timestamps are public blockchain records and independently "
            f"verifiable on any block explorer.")
        hold_request = (
            f"<b>2. Token freeze.</b> I request that, to the extent your "
            f"terms of service and applicable law permit, you freeze or "
            f"blacklist the above address at the {issuer_asset} token "
            f"contract level, preventing further transfers of "
            f"{issuer_asset} from it, pending service of a court order, "
            f"seizure warrant or equivalent legal process, which is being "
            f"pursued. The itemised schedule in Attachment A is suitable "
            f"for incorporation as an exhibit to that process.")
    else:
        identification = (
            f"Blockchain analysis conducted in this investigation shows "
            f"that between {date_span}, the {chain_name} address "
            f"<font face='Courier'>{address}</font> - which the "
            f"investigation attributes to "
            + (f"an account at <b>{entity}</b>" if entity
               else "an account at your institution")
            + f" - received {totals_text} traceable to the victim's "
            f"funds, via the transaction(s) itemised in Attachment A. "
            f"Transaction identifiers, amounts and timestamps are public "
            f"blockchain records and independently verifiable on any "
            f"block explorer.")
        hold_request = (
            "<b>2. Asset hold.</b> I further request that, to the extent "
            "your terms of service and applicable law permit, you place "
            "an immediate hold on the assets in the identified account "
            "attributable to the transactions in Attachment A, pending "
            "service of a court order, seizure warrant or equivalent "
            "legal process, which is being pursued. The itemised "
            "schedule in Attachment A is suitable for incorporation as "
            "an exhibit to that process.")

    paragraphs = [
        f"I am {officer_title} {officer} ({officer_badge}) of "
        f"{agency_name}. I am conducting an active criminal investigation "
        f"(reference: {case.get('case_number') or case['name']}) involving "
        f"the theft or fraudulent acquisition of cryptocurrency from a "
        f"victim.",

        identification,

        "<b>1. Preservation.</b> I request that you preserve, for 90 days "
        "pending the issuance of legal process, all records associated "
        + ("with the above address and the listed transactions, "
           "including any records identifying persons who acquired, "
           "redeemed or transferred the tokens involved"
           if issuer_asset else
           "with the account that controls the above deposit address, "
           "including: customer identification and KYC records; "
           "account-opening records; IP-address, device and session "
           "logs; complete transaction, deposit, withdrawal and trade "
           "history; linked bank, card and payment accounts; "
           "communications with the account holder; and records of any "
           "other accounts held by the same person or sharing "
           "identifiers with this account")
        + ". This request is made under 18 U.S.C. § 2703(f) to the "
        "extent that statute applies to your institution and, in the "
        "alternative, as a request for voluntary cooperation pending "
        "legal process. A renewal request may follow at the end of the "
        "90-day period. (Counsel to confirm the citation's applicability "
        "to this custodian and jurisdiction.)",

        hold_request,

        "<b>3. Non-disclosure.</b> I request that you not disclose the "
        "existence of this request to the account holder or any third "
        "party, as disclosure may result in flight, destruction of "
        "evidence, or dissipation of the assets. (An order under "
        "18 U.S.C. § 2705(b) may follow; counsel to advise.)",

        f"Please direct your response, and any questions, to: {officer} "
        f"({officer_title}, {officer_badge}), "
        f"{_agency_line(agency, 'agency_unit', '[UNIT / DIVISION]')}, "
        f"{agency_name}, "
        f"tel {_agency_line(agency, 'agency_phone', '[PHONE]')}, "
        f"email {_agency_line(agency, 'officer_email', '[EMAIL]')}. "
        f"A full fund-tracing report (methodology, confidence basis for "
        f"every attribution, and a per-acquisition chain-of-custody log "
        f"with SHA-256 hashes) is available on request.",
    ]
    for text in paragraphs:
        story.append(Paragraph(text, styles["body"]))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        f"Respectfully,<br/><br/><br/>"
        f"____________________________<br/>"
        f"{officer}<br/>{officer_title}, {officer_badge}<br/>{agency_name}",
        styles["body"]))

    # -------------------------------------------- service-channel information
    if custodian:
        story.append(Spacer(1, 14))
        rows = [["Field", "Value"]]
        for label, key in [("Entity", "legal_name"),
                           ("Jurisdiction", "jurisdiction"),
                           ("LE portal", "portal"),
                           ("Published guidelines", "guidelines"),
                           ("Email", "email"),
                           ("Process notes", "process_notes")]:
            value = custodian.get(key)
            if value:
                rows.append([label, Paragraph(str(value), styles["small"])])
        table = Table(rows, colWidths=[1.3 * inch, 5.5 * inch])
        table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b7c1cc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(Paragraph(
            "Service-channel information (for the investigator; remove "
            "before service):", styles["h2"]))
        story.append(table)
        if custodian.get("source_note"):
            story.append(Paragraph(custodian["source_note"], styles["warn"]))

    # -------------------------------------------------- Attachment A schedule
    story.append(PageBreak())
    story.append(Paragraph("Attachment A - Transaction Schedule",
                           styles["title"]))
    story.append(Paragraph(
        f"On-chain movements of traced funds into {chain_name} address "
        f"<font face='Courier'>{address}</font>. Transaction identifiers, "
        f"timestamps and amounts are public blockchain records. Approximate "
        f"USD figures use the daily market price of each transaction's UTC "
        f"date and are context, not appraisals.", styles["small"]))
    story.append(Spacer(1, 6))
    schedule = [["#", "Transaction ID", "Date/time (UTC)", "From address",
                 "Asset", "Amount", "≈USD then"]]
    for i, edge in enumerate(sorted(funding_edges,
                                    key=lambda e: e.get("timestamp") or 0),
                             start=1):
        usd = edge.get("value_usd")
        schedule.append([
            str(i),
            Paragraph(edge["txid"], styles["mono"]),
            _fmt_ts(edge.get("timestamp")),
            Paragraph(edge["from_address"], styles["mono"]),
            edge["asset"],
            f"{edge['value']:,.6f}",
            f"${usd:,.2f}" if usd is not None else "-"])
    schedule.append(["", "TOTAL", "", "", "",
                     Paragraph(", ".join(
                         f"{v:,.6f} {a}" for a, v in sorted(totals.items())),
                         styles["small"]),
                     f"${usd_total:,.2f}" if usd_complete and usd_total
                     else "-"])
    table = Table(schedule, colWidths=[0.3 * inch, 2.1 * inch, 1.0 * inch,
                                       1.7 * inch, 0.45 * inch, 0.7 * inch,
                                       0.65 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b7c1cc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, colors.HexColor("#f5f8fa")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    attribution_basis = node.get("basis") or "-"
    story.append(Paragraph(
        f"Attribution basis for the deposit address: {attribution_basis} "
        f"Generated by {config.APP_NAME} v{config.APP_VERSION} from trace "
        f"data in case '{case['name']}'.", styles["small"]))

    doc.build(story, onFirstPage=_draft_watermark,
              onLaterPages=_draft_watermark)
