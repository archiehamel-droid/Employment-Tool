"""Fetch federal job postings from USAJOBS and save them as a postings CSV:

    python -m labor_jobs.usajobs --days 31 --out data/usajobs/2026-10.csv
    python -m labor_jobs --postings data/usajobs/2026-10.csv

Uses the USAJOBS historic announcements API (https://developer.usajobs.gov/),
which needs no key and lists every announcement opened in a date range,
with its pay range, agency, locations and federal job series. Every federal
posting states its pay. With --text, each posting also gets the announcement's
text from the same API: duties, qualifications, education requirements,
conditions of employment and how applicants are evaluated. To add the text to
a file fetched without it:

    python -m labor_jobs.usajobs --add-text data/usajobs/2026-10.csv

Announcements often list many locations; each is saved once, at its first
US location, with the number of locations in work_type.
"""

import argparse
import csv
import html
import re
import sys
import time
from datetime import date, timedelta

import requests

from .feeds import write_rows
from .states import NAME_TO_ABBR

API = "https://data.usajobs.gov/api/historicjoa"
TEXT_API = API + "/announcementtext"
# Announcement sections that say what the job asks for, in reading order.
TEXT_SECTIONS = (("Duties", "duties"), ("Major duties", "majorDutiesList"),
                 ("Qualifications", "requirementsQualifications"), ("Education", "requirementsEducation"),
                 ("Conditions of employment", "requirementsConditionsOfEmployment"),
                 ("Requirements", "requirements"), ("How you will be evaluated", "evaluations"))
PERIODS = {"per year": "YEARLY", "per hour": "HOURLY", "per day": None, "per month": "MONTHLY",
           "per week": "WEEKLY", "bi-weekly": "BIWEEKLY", "per annum": "YEARLY"}


def to_row(item, fetched):
    locations = [loc for loc in item.get("positionlocations") or []
                 if (loc.get("positionLocationCountry") or "").lower() in ("united states", "")]
    location, state = "", None
    if locations:
        first = locations[0]
        state = NAME_TO_ABBR.get((first.get("positionLocationState") or "").lower())
        city = first.get("positionLocationCity") or ""
        location = f"{city}, {state}" if city and state else (state or city)
    period = PERIODS.get((item.get("salaryType") or "").lower())
    series = [c.get("series") for c in item.get("jobcategories") or [] if c.get("series")]
    control = item["usajobsControlNumber"]
    return {
        "job_id": f"usajobs-{control}",
        "source": "usajobs",
        "title": (item.get("positionTitle") or "").strip(),
        "company_name": item.get("hiringAgencyName"),
        "location": location,
        "listed_date": item.get("positionOpenDate"),
        "fetched_date": fetched,
        "min_salary": item.get("minimumSalary") if period else None,
        "max_salary": item.get("maximumSalary") if period else None,
        "pay_period": period,
        "currency": "USD",
        "work_type": f"{item.get('workSchedule') or ''}; {len(locations)} location(s)",
        "category": "OPM series " + ", ".join(series) if series else None,
        "description": "",
        "source_url": f"https://www.usajobs.gov/job/{control}",
    }, bool(locations)


def fetch(days=31, end=None, session=None, pause=1.0):
    """Yield rows for announcements opened in the last `days` days, US only."""
    session = session or requests.Session()
    end = end or date.today()
    start = end - timedelta(days=days)
    params = {"StartPositionOpenDate": start.isoformat(), "EndPositionOpenDate": end.isoformat()}
    url, fetched = API, date.today().isoformat()
    while url:
        resp = session.get(url, params=params, timeout=90,
                           headers={"User-Agent": "labor-jobs/1.0"})
        resp.raise_for_status()
        body = resp.json()
        for item in body.get("data") or []:
            row, in_us = to_row(item, fetched)
            if in_us:
                yield row
        nxt = (body.get("paging") or {}).get("next")
        url, params = (f"https://data.usajobs.gov{nxt}", None) if nxt else (None, None)
        time.sleep(pause)


def plain_text(markup):
    """HTML from an announcement as plain text, one paragraph or list item per line."""
    if not markup or markup == "None":
        return ""
    text = re.sub(r"(?i)<\s*(br|/p|/li|/div|/h\d|/tr)\s*/?>", "\n", str(markup))
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def announcement_text(item):
    """The sections of an announcement that state what the job asks for, as one text."""
    parts = []
    for heading, key in TEXT_SECTIONS:
        body = plain_text(item.get(key))
        if body:
            parts.append(f"{heading}\n{body}")
    return "\n\n".join(parts)


def fetch_text(start, end, session=None, pause=1.0):
    """{control number: announcement text} for announcements opened from start to end (dates)."""
    session = session or requests.Session()
    params = {"StartPositionOpenDate": start.isoformat(), "EndPositionOpenDate": end.isoformat()}
    url, out = TEXT_API, {}
    while url:
        for attempt in range(4):
            try:
                resp = session.get(url, params=params, timeout=120, headers={"User-Agent": "labor-jobs/1.0"})
                resp.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(5 * 2 ** attempt)
        body = resp.json()
        for item in body.get("data") or []:
            out[str(item.get("usajobsControlNumber"))] = announcement_text(item)
        nxt = (body.get("paging") or {}).get("next")
        url, params = (f"https://data.usajobs.gov{nxt}", None) if nxt else (None, None)
        time.sleep(pause)
    return out


def add_text(path, session=None):
    """Fill the description column of a USAJOBS postings file with announcement text."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0
    dates = sorted(r["listed_date"][:10] for r in rows if r.get("listed_date"))
    start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    texts = fetch_text(start, end, session)
    filled = 0
    for r in rows:
        text = texts.get(r["job_id"].removeprefix("usajobs-"))
        if text:
            r["description"] = text
            filled += 1
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return filled


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.usajobs", description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=31, help="announcements opened in the last N days")
    p.add_argument("--out", help="CSV file to write")
    p.add_argument("--append", action="store_true", help="add to an existing file")
    p.add_argument("--text", action="store_true", help="also fetch each announcement's text")
    p.add_argument("--add-text", metavar="FILE", help="fill in announcement text in an existing postings file")
    args = p.parse_args(argv)
    if args.add_text:
        try:
            filled = add_text(args.add_text)
        except requests.RequestException as exc:
            sys.exit(f"USAJOBS request failed: {type(exc).__name__}")
        print(f"Added announcement text to {filled:,} postings in {args.add_text}")
        return
    if not args.out:
        p.error("give --out, or --add-text FILE")
    try:
        added = write_rows(fetch(args.days), args.out, args.append)
        if args.text:
            add_text(args.out)
    except requests.RequestException as exc:
        sys.exit(f"USAJOBS request failed: {type(exc).__name__}")
    print(f"Wrote {added:,} postings to {args.out}")


if __name__ == "__main__":
    main()
