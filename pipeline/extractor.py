"""Heuristic structured extraction.

Used as a fallback when the Gemini engine is unavailable (or for the Google
Vision path). Operates on plain OCR text, finds known field labels, and pulls
the neighbouring value. Handles common invoice / CNIC layouts.
"""
from __future__ import annotations

import re

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

CNIC_RE = re.compile(r"\b\d{5}-\d{7}-\d\b")

DATE_RE_ISO = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
DATE_RE_DMY = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
DATE_RE_MONTH_FIRST = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"\s+(\d{1,2}),?\s+(\d{2,4})\b", re.I
)
DATE_RE_MONTH_LAST = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,?"
    r"\s+(\d{2,4})\b", re.I
)

INVOICE_DATE_LABELS = [
    "invoice date", "issue date", "date issued", "bill date", "invoice date:",
    "date:", "date", "tax date", "txn date", "transaction date",
]
CNIC_DATE_LABELS = [
    "date of birth", "dob", "date of birth:", "date of issue",
    "valid till", "valid until", "expiry date", "date of expiry",
]
INVOICE_NAME_LABELS = [
    "customer name", "customer:", "customer", "bill to", "billed to",
    "sold to", "buyer", "consignee", "client", "party name", "party",
    "invoice to", "customer id", "name:",
]
CNIC_NAME_LABELS = [
    "applicant name", "name of applicant", "applicant", "name of holder",
    "holder name", "full name", "name",
]
INVOICE_NUMBER_RE = re.compile(
    r"\b(?:invoice|bill|ref|no)\b[ \t]*[.:#]*[ \t]*"
    r"([A-Za-z0-9][A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]*)",
    re.I,
)
TOTAL_RE = re.compile(
    r"\b(?:grand\s+total|total|amount\s+due|balance\s+due|amount)[:\s$]*"
    r"([0-9][0-9,]*\.?\d*)",
    re.I,
)

GENERIC_NAME_RE = re.compile(
    r"(?:^|\n)\s*([A-Z][a-zA-Z]+(?:[ \.\-][A-Z][a-zA-Z]+){1,4})\s*$"
)


def normalize_date(y: str | int, m: str | int, d: str | int) -> str:
    y, m, d = int(y), int(m), int(d)
    if y < 100:
        y += 2000 if y < 50 else 1900
    if 1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100:
        return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def find_dates(text: str) -> list[str]:
    """Return all dates found in text (normalized ISO when possible)."""
    found: list[str] = []
    for match in DATE_RE_ISO.finditer(text):
        found.append(normalize_date(match.group(1), match.group(2), match.group(3)))
    for match in DATE_RE_DMY.finditer(text):
        found.append(normalize_date(match.group(3), match.group(2), match.group(1)))
    for match in DATE_RE_MONTH_FIRST.finditer(text):
        found.append(normalize_date(match.group(3), MONTHS[match.group(1)[:3].lower()], match.group(2)))
    for match in DATE_RE_MONTH_LAST.finditer(text):
        found.append(normalize_date(match.group(3), MONTHS[match.group(2)[:3].lower()], match.group(1)))
    return [f for f in found if f]


def _split_lines(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _value_after_label(
    lines: list[str], labels: list[str]
) -> str | None:
    any_label = [re.compile(re.escape(lb), re.I) for lb in labels]
    for idx, line in enumerate(lines):
        for label_re in any_label:
            match = label_re.search(line)
            if not match:
                continue
            remainder = line[match.end():].strip(" :\t-")
            if remainder and not re.fullmatch(r"[:,\-–.\s]*", remainder):
                return remainder
            if idx + 1 < len(lines):
                nxt = lines[idx + 1]
                if nxt and not _line_contains_label(nxt, labels):
                    return nxt
            return None
    return None


def _line_contains_label(line: str, labels: list[str]) -> bool:
    return any(
        re.search(re.escape(label), line, re.I) for label in labels
    )


def extract_with_heuristics(text: str, doc_hint: str = "") -> dict:
    text_safe = text or ""
    doc_hint = (doc_hint or "").lower()
    is_cnic = "cnic" in doc_hint or "identity" in text_safe.lower() or bool(CNIC_RE.search(text_safe))
    doc_type = "cnic" if is_cnic else "invoice"

    lines = _split_lines(text_safe)
    name_labels = CNIC_NAME_LABELS if is_cnic else INVOICE_NAME_LABELS
    date_labels = CNIC_DATE_LABELS if is_cnic else INVOICE_DATE_LABELS

    name = _value_after_label(lines, name_labels) or ""
    if name:
        name = name.split("\t")[0].strip(" :,;")
    if not name and is_cnic:
        m = GENERIC_NAME_RE.search(text_safe)
        if m:
            name = m.group(1).strip()

    date_raw = ""
    date_normalized = ""
    labelled_date = _value_after_label(lines, date_labels)
    if labelled_date and not any(ch.isdigit() for ch in labelled_date):
        # label line only -> check next line via full-text
        labelled_date = None
    if labelled_date:
        date_raw = labelled_date
        parsed = find_dates(labelled_date)
        if parsed:
            date_normalized = parsed[0]
    if not date_normalized:
        dates = find_dates(text_safe)
        if dates:
            date_normalized = dates[0]
            date_raw = date_raw or date_normalized

    if not date_raw and date_normalized:
        date_raw = date_normalized

    fields: dict = {}
    if is_cnic:
        cnic = CNIC_RE.search(text_safe)
        if cnic:
            fields["identity_number"] = cnic.group(0)
    else:
        inv_no = INVOICE_NUMBER_RE.search(text_safe)
        if inv_no:
            fields["invoice_number"] = inv_no.group(1)
        total = TOTAL_RE.search(text_safe)
        if total:
            fields["total_amount"] = total.group(1)

    confidence = 0.7 if (name and date_normalized) else (0.4 if (name or date_normalized) else 0.1)
    return {
        "document_type": doc_type,
        "name": name,
        "date": date_raw,
        "date_normalized": date_normalized,
        "confidence": round(confidence, 2),
        "fields": fields,
        "engine": "heuristic",
    }