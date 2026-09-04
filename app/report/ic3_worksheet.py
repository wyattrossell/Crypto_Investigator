"""
Printable IC3 complaint worksheet (PDF).

Mirrors the section order of the real complaint form at
https://complaint.ic3.gov so answers can be transcribed top to bottom.
IC3 has no API - filing is always done by a person on the web form - so
this worksheet is the deliverable: everything organised, nothing forgotten,
narrative already inside the form's character limits.
"""

from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from app import config, ic3

PAGE_MARGIN = 0.7 * inch

FILING_NOTES = [
    "File at https://complaint.ic3.gov (there is no other way to submit; "
    "IC3 has no email or phone intake and no API).",
    "A complaint CANNOT be edited or cancelled after it is submitted. If "
    "new information surfaces later, file a new complaint and answer YES "
    "to 'Is this an update to a previously filed complaint?'.",
    "Do NOT include Social Security numbers or dates of birth anywhere in "
    "the complaint - IC3 says not to provide them.",
    "Keep every original record: chat logs, emails, receipts, exchange "
    "statements, screenshots. IC3 may not contact the complainant, but "
    "investigators assigned later will need the originals.",
    "Beware of 'fund recovery' services that contact victims after a scam "
    "- the FBI warns these are usually a second scam, especially any that "
    "charge an up-front fee.",
    "Victims aged 60 or over can get free help filing from the National "
    "Elder Fraud Hotline: 833-372-8311.",
]


def _styles():
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


def _kv_table(rows, styles, key_width=2.1):
    """Two-column field/value table; blank values render as a fill-in line."""
    data = [[Paragraph(f"<b>{k}</b>", styles["small"]),
             Paragraph(str(v) if str(v).strip() else
                       "<font color='#999999'>________________</font>",
                       styles["small"])]
            for k, v in rows]
    table = Table(data, colWidths=[key_width * inch,
                                   (7.0 - key_width) * inch])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b7c1cc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1),
         [colors.white, colors.HexColor("#f5f8fa")]),
    ]))
    return table


def _yesno(value) -> str:
    if value in (True, "true", "yes", "Yes"):
        return "Yes"
    if value in (False, "false", "no", "No"):
        return "No"
    return str(value or "")


def build_worksheet(pdf_path: str, case: dict, draft: dict) -> None:
    """Render the worksheet PDF for one case's saved IC3 draft."""
    styles = _styles()
    story = []
    doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                            leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
                            topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
                            title=f"IC3 Complaint Worksheet - {case['name']}")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    story.append(Paragraph("IC3 Internet Crime Complaint - Filing Worksheet",
                           styles["title"]))
    story.append(Paragraph(
        f"Case: {case['name']}"
        + (f" ({case['case_number']})" if case.get("case_number") else "")
        + f" &nbsp;|&nbsp; Prepared {generated} by {config.APP_NAME} "
          f"v{config.APP_VERSION}", styles["small"]))
    story.append(Paragraph(
        "This worksheet organises the complaint in the same order as the "
        "official form at <b>https://complaint.ic3.gov</b>. It is NOT a "
        "submission - a person must type these answers into the form.",
        styles["warn"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Before you file", styles["h2"]))
    for note in FILING_NOTES:
        story.append(Paragraph(f"• {note}", styles["small"]))
    story.append(Spacer(1, 10))

    # ---- Section 1: who is filing
    filer = draft.get("filer", {})
    story.append(Paragraph("Section 1 - Who Is Filing This Complaint?",
                           styles["h1"]))
    rows = [("Were you the one affected in this incident?",
             _yesno(filer.get("filing_for_self", True)))]
    if not filer.get("filing_for_self", True):
        rows += [("Filer name", filer.get("name", "")),
                 ("Filer phone", filer.get("phone", "")),
                 ("Filer email", filer.get("email", "")),
                 ("Filer business name (optional)", filer.get("business", ""))]
    story.append(_kv_table(rows, styles, key_width=3.1))
    story.append(Spacer(1, 8))

    # ---- Section 2: complainant
    person = draft.get("complainant", {})
    story.append(Paragraph("Section 2 - Complainant Information "
                           "(the person affected)", styles["h1"]))
    story.append(_kv_table([
        ("Name", person.get("name", "")),
        ("Address", person.get("address", "")),
        ("City", person.get("city", "")),
        ("County (optional)", person.get("county", "")),
        ("Country", person.get("country", "")),
        ("State", person.get("state", "")),
        ("Zip code", person.get("zip", "")),
        ("Phone number", person.get("phone", "")),
        ("Email address", person.get("email", "")),
        ("Age range (optional)", person.get("age_range", "")),
        ("Is the complainant 17 or younger?",
         _yesno(person.get("is_minor", False))),
    ], styles))
    story.append(Spacer(1, 8))

    # ---- Section 3: financial transactions
    financial = draft.get("financial", {})
    story.append(Paragraph("Section 3 - Financial Transaction(s)",
                           styles["h1"]))
    story.append(_kv_table([
        ("Did you send or lose money?",
         _yesno(financial.get("money_sent_or_lost", True))),
        ("Total loss amount (USD)", financial.get("total_loss_usd", "")),
    ], styles, key_width=3.1))
    story.append(Spacer(1, 4))
    transactions = financial.get("transactions", [])
    if not transactions:
        story.append(Paragraph(
            "No transactions recorded yet. On the form, add one entry per "
            "payment.", styles["small"]))
    for i, tx in enumerate(transactions, start=1):
        story.append(Paragraph(f"Transaction #{i}", styles["h2"]))
        rows = [
            ("Transaction type",
             tx.get("transaction_type", "Cryptocurrency/Crypto ATM")),
            ("Transaction amount (USD)", tx.get("amount_usd", "")),
            ("Crypto amount (for your reference; the form asks for USD)",
             tx.get("crypto_amount", "")),
            ("Transaction date", tx.get("date", "")),
            ("Was the money sent or lost?", tx.get("sent_or_lost", "")),
            ("Did you contact your bank/exchange?",
             _yesno(tx.get("contacted_institution", ""))),
            ("Type of cryptocurrency", tx.get("crypto_type", "")),
            ("Transaction ID/Hash", tx.get("tx_hash", "")),
            ("Originating wallet address", tx.get("originating_wallet", "")),
            ("Recipient wallet address", tx.get("recipient_wallet", "")),
            ("Originating platform/exchange",
             tx.get("originating_platform", "")),
            ("Recipient platform/exchange (if known)",
             tx.get("recipient_platform", "")),
        ]
        if tx.get("kiosk_name") or tx.get("kiosk_address"):
            rows += [("Crypto ATM/kiosk name", tx.get("kiosk_name", "")),
                     ("Crypto ATM/kiosk address", tx.get("kiosk_address", ""))]
        story.append(_kv_table(rows, styles, key_width=3.1))
        story.append(Spacer(1, 6))

    # ---- Section 4: subjects
    story.append(Paragraph("Section 4 - Information About The Subject(s) "
                           "(all optional; give what you have)", styles["h1"]))
    subjects = draft.get("subjects", [])
    if not subjects:
        story.append(Paragraph(
            "No subject information recorded. Even partial details help: a "
            "username, phone number, email, website or IP address.",
            styles["small"]))
    for i, subject in enumerate(subjects, start=1):
        story.append(Paragraph(f"Subject #{i}", styles["h2"]))
        story.append(_kv_table([
            ("Name (as given to you)", subject.get("name", "")),
            ("Business name", subject.get("business", "")),
            ("Address", subject.get("address", "")),
            ("Phone number", subject.get("phone", "")),
            ("Email address", subject.get("email", "")),
            ("Website / social media", subject.get("website_social", "")),
            ("IP address", subject.get("ip", "")),
        ], styles))
        story.append(Spacer(1, 6))

    # ---- Section 5: description
    story.append(Paragraph("Section 5 - Description Of Incident "
                           f"(maximum {ic3.LIMIT_DESCRIPTION:,} characters)",
                           styles["h1"]))
    description = draft.get("description", "")
    story.append(Paragraph(
        f"Current length: {len(description):,} / {ic3.LIMIT_DESCRIPTION:,} "
        f"characters.", styles["small"]))
    for para in (description or "(not written yet)").split("\n"):
        if para.strip():
            story.append(Paragraph(para, styles["body"]))
    story.append(Spacer(1, 8))

    # ---- Section 6: other information
    other = draft.get("other", {})
    story.append(Paragraph("Section 6 - Other Information", styles["h1"]))
    story.append(_kv_table([
        (f"Technical details (max {ic3.LIMIT_TECHNICAL:,} chars)",
         other.get("technical_details", "")),
        (f"Other witnesses/affected persons (max "
         f"{ic3.LIMIT_WITNESSES:,} chars)", other.get("witnesses", "")),
        (f"Reports to other agencies (max {ic3.LIMIT_OTHER_AGENCIES:,} "
         f"chars)", other.get("other_agencies", "")),
        ("Is this an update to a previously filed complaint?",
         _yesno(other.get("is_update", False))),
    ], styles, key_width=3.1))
    story.append(Spacer(1, 8))

    # ---- Section 7: signature (never pre-filled)
    story.append(Paragraph("Section 7 - Privacy & Signature", styles["h1"]))
    story.append(Paragraph(
        "The form ends with a digital signature: the complainant types "
        "their full name and accepts the terms. This must be done by the "
        "complainant personally on the form.", styles["small"]))

    doc.build(story)
