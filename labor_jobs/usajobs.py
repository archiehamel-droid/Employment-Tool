"""Fetch federal job postings from USAJOBS and save them as a postings CSV:

    python -m labor_jobs.usajobs --days 31 --out data/usajobs/2026-10.csv
    python -m labor_jobs --postings data/usajobs/2026-10.csv

Uses the USAJOBS historic announcements API (https://developer.usajobs.gov/),
which needs no key and lists every announcement opened in a date range,
with its pay range, agency, locations and federal job series. Every federal
posting states its pay. It has no job descriptions, so technology counts
don't apply; the keyed Search API adds those.

Announcements often list many locations; each is saved once, at its first
US location, with the number of locations in work_type.
"""

import argparse
import sys
import time
from datetime import date, timedelta

import requests

from .feeds import write_rows
from .states import NAME_TO_ABBR

API = "https://data.usajobs.gov/api/historicjoa"
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.usajobs", description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=31, help="announcements opened in the last N days")
    p.add_argument("--out", required=True, help="CSV file to write")
    p.add_argument("--append", action="store_true", help="add to an existing file")
    args = p.parse_args(argv)
    try:
        added = write_rows(fetch(args.days), args.out, args.append)
    except requests.RequestException as exc:
        sys.exit(f"USAJOBS request failed: {type(exc).__name__}")
    print(f"Wrote {added:,} postings to {args.out}")


if __name__ == "__main__":
    main()
