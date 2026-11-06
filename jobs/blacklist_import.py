"""
jobs/blacklist_import.py
─────────────────────────
Read a blacklist spreadsheet (Excel .xlsx or .csv) into Blacklist entries.

Columns are found by their header names (any order, case-insensitive):
  name | phone | email | role | reason
(also accepts e.g. "Full name", "Phone number", "Mobile", "Position", "Comment").
If no header row is recognised, the old layout is assumed:
  S/N, Name, Phone Number, Role, Comment

Duplicates (same phone, or same name when there's no phone) are skipped.
"""

import csv
import io
import re

from .models import Blacklist

HEADERS = {
    "name": {"name", "full name", "fullname", "candidate", "candidate name", "applicant", "applicant name"},
    "phone": {"phone", "phone number", "phone no", "mobile", "mobile number", "tel", "telephone", "contact"},
    "email": {"email", "email address", "e-mail"},
    "role": {"role", "position", "job", "job title", "post"},
    "reason": {"reason", "comment", "comments", "remark", "remarks", "note", "notes"},
}
LEGACY = {"name": 1, "phone": 2, "role": 3, "reason": 4}  # S/N, Name, Phone, Role, Comment

MAX_ROWS = 5000


class BlacklistFileError(ValueError):
    pass


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel stores 8031234567 as 8031234567.0
    return str(value).strip()


def _phone_key(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _rows(data: bytes, filename: str) -> list[list[str]]:
    name = filename.lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text))]
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise BlacklistFileError("Could not open the Excel file.") from exc
        return [[_cell(c) for c in row] for row in wb.active.iter_rows(values_only=True)]
    raise BlacklistFileError("Upload an Excel (.xlsx) or CSV file.")


def _find_columns(rows: list[list[str]]) -> tuple[dict, int]:
    """Return ({field: column index}, index of first data row)."""
    for i, row in enumerate(rows[:10]):
        found = {}
        for col, value in enumerate(row):
            key = value.strip().lower().rstrip(":")
            for field, names in HEADERS.items():
                if key in names and field not in found:
                    found[field] = col
        if "name" in found:
            return found, i + 1
    return LEGACY, 1


def import_blacklist(data: bytes, filename: str, added_by=None) -> dict:
    rows = _rows(data, filename)
    if not rows:
        raise BlacklistFileError("The file is empty.")
    if len(rows) > MAX_ROWS:
        raise BlacklistFileError(f"The file has more than {MAX_ROWS} rows.")

    cols, start = _find_columns(rows)
    get = lambda row, field: row[cols[field]] if field in cols and cols[field] < len(row) else ""

    existing_phones = {_phone_key(p) for p in Blacklist.objects.exclude(phone="").values_list("phone", flat=True)}
    existing_names = {n.strip().lower() for n in Blacklist.objects.values_list("name", flat=True)}

    created, skipped = 0, 0
    for row in rows[start:]:
        name = get(row, "name")
        if not name:
            continue
        phone = get(row, "phone")
        key = _phone_key(phone)
        if (key and key in existing_phones) or (not key and name.lower() in existing_names):
            skipped += 1
            continue
        Blacklist.objects.create(
            name=name[:255],
            phone=phone[:50],
            email=get(row, "email")[:254] if "@" in get(row, "email") else "",
            role=get(row, "role")[:255],
            reason=get(row, "reason") or "Imported from list",
            added_by=added_by,
        )
        if key:
            existing_phones.add(key)
        existing_names.add(name.lower())
        created += 1

    return {"created": created, "skipped": skipped}
