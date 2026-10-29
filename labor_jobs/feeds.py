"""Shared pieces for the postings fetchers (Adzuna, USAJOBS, company job
boards): the CSV columns they write, appending without duplicates, and
reading salary ranges and US locations out of free text."""

import csv
import re
from pathlib import Path

from .states import ABBRS, NAME_TO_ABBR

FIELDS = ["job_id", "source", "title", "company_name", "location", "county", "listed_date",
          "fetched_date", "min_salary", "max_salary", "pay_period", "currency", "work_type",
          "category", "description", "source_url"]


def write_rows(rows, out, append=False):
    """Write postings rows to a CSV, skipping job_ids already written (and,
    with append, already in the file). Returns how many rows were added."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if append and out.exists():
        with out.open(newline="") as f:
            reader = csv.DictReader(f)
            existing = list(reader)
            header = reader.fieldnames
        seen = {row["job_id"] for row in existing}
        if header != FIELDS:
            # Written by an older version with other columns: rewrite it in
            # the current layout first, so appended rows line up.
            with out.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(existing)
    mode = "a" if append and out.exists() else "w"
    added = 0
    with out.open(mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if mode == "w":
            writer.writeheader()
        for row in rows:
            if row["job_id"] in seen:
                continue
            seen.add(row["job_id"])
            writer.writerow(row)
            added += 1
    return added


DASH = "-–—"
# "$120,000", "$120,000.00", "$45.50", "$120K"; not "$100M" or "$2 billion".
NUMBER = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?([kK])?(?![\d.,]*\s?(?:[mMbB]\b|million|billion))"
PAY_RANGE = re.compile(
    r"\$\s?" + NUMBER
    + r"(?:\s*(?:[" + DASH + r"]|to)\s*\$?\s?" + NUMBER + r")?"
    + r"(?:\s*(?:USD|usd))?\s*(?:/|per|an|a)?\s*(hour|hr|year|yr|annum|annually|week|month)?")
PERIODS = {"hour": "HOURLY", "hr": "HOURLY", "year": "YEARLY", "yr": "YEARLY",
           "annum": "YEARLY", "annually": "YEARLY", "week": "WEEKLY", "month": "MONTHLY"}


def _amount(number, k):
    value = float(number.replace(",", ""))
    return value * 1000 if k else value


def parse_pay(text):
    """First plausible pay range in text, as (min, max, pay_period), or
    (None, None, None). "$120,000 - $150,000", "$120K-$150K", "$45/hr"."""
    if not isinstance(text, str):
        return None, None, None
    for m in PAY_RANGE.finditer(text):
        low = _amount(m.group(1), m.group(2))
        high = _amount(m.group(3), m.group(4)) if m.group(3) else low
        if m.group(3) and m.group(4) and not m.group(2) and low < 1000:
            low *= 1000  # "$120-150K"
        period = PERIODS.get((m.group(5) or "").lower())
        if period is None and low >= 15000:
            period = "YEARLY"  # a bare "$95,000" is a salary; a bare "$100" could be anything
        if period is None or high < low:
            continue
        if period == "YEARLY" and not 15000 <= low <= 1_500_000:
            continue
        if period == "HOURLY" and not 7 <= low <= 500:
            continue
        if period == "MONTHLY" and not 1000 <= low <= 150000:
            continue  # "$75/month phone stipend"
        if period == "WEEKLY" and not 300 <= low <= 30000:
            continue
        return low, high, period
    return None, None, None


NON_US = re.compile(r"(?i)\b(canada|uk|united kingdom|england|london|ireland|dublin|germany|berlin|"
                    r"france|paris|india|bangalore|bengaluru|hyderabad|singapore|japan|tokyo|"
                    r"australia|sydney|brazil|mexico|netherlands|amsterdam|spain|poland|israel|"
                    r"tel aviv|toronto|vancouver|emea|apac|latam|europe)\b")
DC = re.compile(r"(?i)\bwashington,?\s*d\.?\s?c\b")


def us_location(text):
    """("City, ST", "ST") for a US location string, ("Remote, US", None) for
    US-remote, ("Remote", None) for remote with no country, or None when the
    location is outside the US."""
    if not isinstance(text, str) or not text.strip():
        return None
    for part in re.split(r"\s*[;|/]\s*|\s+or\s+", text):
        if DC.search(part):
            return "Washington, DC", "DC"
        m = re.search(r"([A-Za-z .'-]+?),\s*([A-Z]{2})\b", part)
        if m and m.group(2) in ABBRS:
            return f"{m.group(1).strip()}, {m.group(2)}", m.group(2)
        for name, abbr in NAME_TO_ABBR.items():
            if re.search(rf"(?i)\b{re.escape(name)}\b", part):
                city = part.split(",")[0].strip()
                return (f"{city}, {abbr}" if city.lower() != name else abbr), abbr
    if NON_US.search(text):
        return None
    if re.search(r"(?i)\b(united states|usa|u\.s\.?|us)\b", text):
        return "Remote, US", None
    if re.search(r"(?i)\bremote\b", text):
        return "Remote", None  # possibly outside the US; kept, with no state
    return None
