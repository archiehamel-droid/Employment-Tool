"""Fetch current US job postings from the Adzuna API and save them as a CSV
the main program reads like the Kaggle file:

    python -m labor_jobs.adzuna --pages 100 --out data/adzuna/2026-10.csv
    python -m labor_jobs --postings data/adzuna/2026-10.csv

Needs a free key from https://developer.adzuna.com/, given as --app-id and
--app-key or as ADZUNA_APP_ID and ADZUNA_APP_KEY. Each page is one API call
of up to 50 postings; the free plan allows a limited number of calls per day,
so a monthly run of a few hundred pages builds up the postings history.

Adzuna estimates a salary for postings that don't state one; those estimates
are dropped so only advertised salaries are used. Descriptions are short
snippets, so technology counts from Adzuna postings undercount.
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import requests

from .states import NAME_TO_ABBR

API_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"
FIELDS = ["job_id", "title", "company_name", "location", "listed_date", "min_salary",
          "max_salary", "pay_period", "currency", "work_type", "category", "description",
          "source_url"]


def _clean(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def to_row(result):
    """One Adzuna result as a row in the postings format."""
    area = result.get("location", {}).get("area", [])
    state = NAME_TO_ABBR.get(area[1].lower()) if len(area) > 1 else None
    city = area[-1] if len(area) > 2 else None
    if city and state:
        location = f"{city}, {state}"
    else:
        location = result.get("location", {}).get("display_name", "")
    predicted = str(result.get("salary_is_predicted", "0")) == "1"
    salary_min = None if predicted else result.get("salary_min")
    salary_max = None if predicted else result.get("salary_max")
    return {
        "job_id": f"adzuna-{result['id']}",
        "title": _clean(result.get("title")),
        "company_name": (result.get("company") or {}).get("display_name"),
        "location": location,
        "listed_date": (result.get("created") or "")[:10],
        "min_salary": salary_min,
        "max_salary": salary_max,
        "pay_period": "YEARLY" if salary_min or salary_max else None,
        "currency": "USD",
        "work_type": result.get("contract_time"),
        "category": (result.get("category") or {}).get("label"),
        "description": _clean(result.get("description")),
        "source_url": result.get("redirect_url"),
    }


def fetch(app_id, app_key, pages, what=None, where=None, max_days_old=31,
          per_page=50, pause=2.5, session=None):
    """Yield postings rows, newest first, stopping early if results run out."""
    session = session or requests.Session()
    for page in range(1, pages + 1):
        params = {"app_id": app_id, "app_key": app_key, "results_per_page": per_page,
                  "max_days_old": max_days_old, "sort_by": "date",
                  "content-type": "application/json"}
        if what:
            params["what"] = what
        if where:
            params["where"] = where
        resp = session.get(API_URL.format(page=page), params=params, timeout=60)
        if resp.status_code in (401, 403):
            raise RuntimeError("Adzuna rejected the app ID or key (HTTP %d)" % resp.status_code)
        if resp.status_code == 429:
            raise RuntimeError(f"Adzuna rate limit reached after {page - 1} pages; "
                               "run again later with fewer --pages")
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for result in results:
            yield to_row(result)
        if len(results) < per_page:
            return
        time.sleep(pause)  # stay under the per-minute limit


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.adzuna", description=__doc__.split("\n\n")[0])
    p.add_argument("--app-id", default=os.environ.get("ADZUNA_APP_ID"))
    p.add_argument("--app-key", default=os.environ.get("ADZUNA_APP_KEY"))
    p.add_argument("--pages", type=int, default=20,
                   help="API calls to make, 50 postings each (default 20)")
    p.add_argument("--what", help="keywords, e.g. 'data scientist' (default: all jobs)")
    p.add_argument("--where", help="place, e.g. 'Texas' (default: all of the US)")
    p.add_argument("--max-days-old", type=int, default=31)
    p.add_argument("--out", required=True, help="CSV file to write")
    args = p.parse_args(argv)
    if not (args.app_id and args.app_key):
        sys.exit("Set --app-id and --app-key (or ADZUNA_APP_ID / ADZUNA_APP_KEY). "
                 "Register free at https://developer.adzuna.com/")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        try:
            for row in fetch(args.app_id, args.app_key, args.pages, args.what, args.where,
                             args.max_days_old):
                if row["job_id"] not in seen:
                    seen.add(row["job_id"])
                    writer.writerow(row)
        except RuntimeError as exc:  # keep what was fetched before the error
            print(f"Stopped early: {exc}")
    print(f"Wrote {len(seen):,} postings to {out}")


if __name__ == "__main__":
    main()
