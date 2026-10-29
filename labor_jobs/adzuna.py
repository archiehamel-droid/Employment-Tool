"""Fetch current US job postings from the Adzuna API and save them as a CSV
the main program reads like the Kaggle file:

    python -m labor_jobs.adzuna --pages 100 --out data/adzuna/2026-10.csv
    python -m labor_jobs --postings data/adzuna/2026-10.csv

Needs a free key from https://developer.adzuna.com/, given as --app-id and
--app-key or as ADZUNA_APP_ID and ADZUNA_APP_KEY. Each page is one API call
of up to 50 postings; the free plan allows a limited number of calls per day,
so a monthly run of a few hundred pages builds up the postings history.

By default the pages are a sample: split across Adzuna's job categories in
proportion to how many postings each has, then across states in proportion
to their BLS employment, at random positions in each search's results.
Adzuna serves at most 99 pages per search (later pages repeat page 100), so
a date-sorted search only reaches the newest few thousand postings, mostly
from whichever employers posted in the last few hours. Splitting by
category and state, and using Adzuna's default order, spreads the sample
across the whole window of --max-days-old days.

Adzuna estimates a salary for postings that don't state one; those estimates
are dropped so only advertised salaries are used. Descriptions are short
snippets, so technology counts from Adzuna postings undercount.
"""

import argparse
import csv
import math
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

from .states import EMPLOYMENT_THOUSANDS, NAME_TO_ABBR, STATES

API_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"
CATEGORIES_URL = "https://api.adzuna.com/v1/api/jobs/us/categories"
MAX_PAGE = 99  # deeper pages repeat page 100
FIELDS = ["job_id", "title", "company_name", "location", "county", "listed_date", "fetched_date",
          "min_salary",
          "max_salary", "pay_period", "currency", "work_type", "category", "description",
          "source_url"]


def _clean(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _clean_url(url):
    """Posting link without Adzuna's utm_ tracking tags, which carry the app ID."""
    if not url:
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def to_row(result):
    """One Adzuna result as a row in the postings format."""
    area = result.get("location", {}).get("area", [])
    state = NAME_TO_ABBR.get(area[1].lower()) if len(area) > 1 else None
    city = area[-1] if len(area) > 2 else None
    county = area[2] if len(area) > 3 else None
    if city and state:
        location = f"{city}, {state}"
    else:
        location = result.get("location", {}).get("display_name", "")
    predicted = str(result.get("salary_is_predicted", "0")) == "1"
    # Adzuna gives 0 for an unstated end of a range.
    salary_min = None if predicted else (result.get("salary_min") or None)
    salary_max = None if predicted else (result.get("salary_max") or None)
    return {
        "job_id": f"adzuna-{result['id']}",
        "title": _clean(result.get("title")),
        "company_name": (result.get("company") or {}).get("display_name"),
        "location": location,
        "county": county,
        "listed_date": (result.get("created") or "")[:10],
        # A fetch is a snapshot of live postings; history files it under this date.
        "fetched_date": date.today().isoformat(),
        "min_salary": salary_min,
        "max_salary": salary_max,
        "pay_period": "YEARLY" if salary_min or salary_max else None,
        "currency": "USD",
        "work_type": result.get("contract_time"),
        "category": (result.get("category") or {}).get("label"),
        "description": _clean(result.get("description")),
        "source_url": _clean_url(result.get("redirect_url")),
    }


class PageFailed(Exception):
    """One request kept failing; the caller can skip it and carry on."""


def _get(session, url, params, retries=3, backoff=5):
    """GET with retries for Adzuna's occasional 5xx errors and timeouts.
    Errors never include the request URL, which carries the API key."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            problem = type(exc).__name__
        else:
            if resp.status_code in (401, 403):
                raise RuntimeError("Adzuna rejected the app ID or key (HTTP %d)" % resp.status_code)
            if resp.status_code == 429:
                raise RuntimeError("Adzuna rate limit reached; run again later or with fewer --pages")
            if resp.status_code < 500:
                if resp.status_code >= 400:
                    raise PageFailed(f"HTTP {resp.status_code}")
                return resp.json()
            problem = f"HTTP {resp.status_code}"
        if attempt < retries:
            time.sleep(backoff * 2 ** attempt)
    raise PageFailed(f"{problem} after {retries + 1} tries")


def search(session, app_id, app_key, page, per_page=50, **filters):
    params = {"app_id": app_id, "app_key": app_key, "results_per_page": per_page,
              "content-type": "application/json"}
    params.update({k: v for k, v in filters.items() if v})
    return _get(session, API_URL.format(page=page), params)


def fetch(app_id, app_key, pages, what=None, where=None, max_days_old=31,
          per_page=50, pause=2.5, session=None):
    """Yield postings rows from the first `pages` pages, newest first,
    stopping early if results run out."""
    session = session or requests.Session()
    for page in range(1, pages + 1):
        body = search(session, app_id, app_key, page, per_page, what=what, where=where,
                      max_days_old=max_days_old, sort_by="date")
        results = body.get("results", [])
        for result in results:
            yield to_row(result)
        if len(results) < per_page or page >= MAX_PAGE:
            return
        time.sleep(pause)  # stay under the per-minute limit


def allocate(counts, pages):
    """Pages per category, proportional to its postings, at least one for
    any category with postings (largest remainders get the leftovers)."""
    total = sum(counts.values())
    if not total:
        return {}
    shares = {c: pages * n / total for c, n in counts.items() if n}
    alloc = {c: max(1, math.floor(s)) for c, s in shares.items()}
    leftover = pages - sum(alloc.values())
    for c in sorted(shares, key=lambda c: shares[c] - math.floor(shares[c]), reverse=True):
        if leftover <= 0:
            break
        alloc[c] += 1
        leftover -= 1
    return alloc


def sample(app_id, app_key, pages, what=None, where=None, max_days_old=31,
           per_page=50, pause=2.5, session=None, seed=None):
    """Yield postings rows from `pages` pages spread across categories in
    proportion to their postings and across states in proportion to their
    employment (unless `where` fixes the place), at random positions."""
    session = session or requests.Session()
    rng = random.Random(seed)
    auth = {"app_id": app_id, "app_key": app_key}
    tags = [c["tag"] for c in _get(session, CATEGORIES_URL, auth).get("results", [])]
    counts = {}
    for tag in tags:  # one small call each to size the categories
        try:
            body = search(session, app_id, app_key, 1, 1, what=what, where=where,
                          max_days_old=max_days_old, category=tag)
        except PageFailed as exc:
            print(f"  couldn't size {tag}, leaving it out: {exc}")
            body = {}
        counts[tag] = int(body.get("count") or 0)
        time.sleep(pause)

    names = {abbr: name for abbr, name, _ in STATES}
    weights = {names[a]: n for a, n in EMPLOYMENT_THOUSANDS.items()}
    total_weight = sum(weights.values())
    seen = set()
    for tag, n_pages in allocate(counts, pages).items():
        for _ in range(n_pages):
            if where:
                place, share = where, 1.0
            else:
                place = rng.choices(list(weights), list(weights.values()))[0]
                share = weights[place] / total_weight
            # Estimated size of this search; pages past its end repeat the last one.
            last = min(MAX_PAGE, max(1, math.ceil(counts[tag] * share / per_page)))
            page = rng.randint(1, last)
            if (tag, place, page) in seen:
                continue
            seen.add((tag, place, page))
            try:
                body = search(session, app_id, app_key, page, per_page, what=what, where=place,
                              max_days_old=max_days_old, category=tag)
            except PageFailed as exc:
                print(f"  skipped {tag} / {place} page {page}: {exc}")
                continue
            for result in body.get("results", []):
                yield to_row(result)
            time.sleep(pause)


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.adzuna", description=__doc__.split("\n\n")[0])
    p.add_argument("--app-id", default=os.environ.get("ADZUNA_APP_ID"))
    p.add_argument("--app-key", default=os.environ.get("ADZUNA_APP_KEY"))
    p.add_argument("--pages", type=int, default=20,
                   help="API calls to make, 50 postings each (default 20)")
    p.add_argument("--what", help="keywords, e.g. 'data scientist' (default: all jobs)")
    p.add_argument("--where", help="place, e.g. 'Texas' (default: all of the US)")
    p.add_argument("--max-days-old", type=int, default=31)
    p.add_argument("--newest", action="store_true",
                   help="take the newest pages instead of a sample across categories")
    p.add_argument("--seed", type=int, help="random seed for the sample")
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
            if args.newest:
                rows = fetch(args.app_id, args.app_key, args.pages, args.what, args.where,
                             args.max_days_old)
            else:
                rows = sample(args.app_id, args.app_key, args.pages, args.what, args.where,
                              args.max_days_old, seed=args.seed)
            for row in rows:
                if row["job_id"] not in seen:
                    seen.add(row["job_id"])
                    writer.writerow(row)
        except (RuntimeError, PageFailed) as exc:  # keep what was fetched before the error
            print(f"Stopped early: {exc}")
    print(f"Wrote {len(seen):,} postings to {out}")


if __name__ == "__main__":
    main()
