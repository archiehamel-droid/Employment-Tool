"""Fetch postings straight from companies' job boards on Greenhouse, Lever
and Ashby, the hiring systems many tech and growth companies use. Each has
a free public API for embedding a company's job board on its own site:

    python -m labor_jobs.ats --out data/ats/2026-10.csv
    python -m labor_jobs --postings data/ats/2026-10.csv

Companies come from labor_jobs/ats_companies.csv (platform,board,name),
where board is the name in the company's board address, e.g. "stripe" in
boards.greenhouse.io/stripe, and name is how the company is shown. Add rows
there for more companies.

Only US (and unspecified remote) postings are kept. Salaries come from the
board's pay fields where it has them (Lever, Ashby) or from the pay range
in the description, which pay-transparency laws in several states require.
Descriptions are full text, so technology counts work well.
"""

import argparse
import concurrent.futures
import csv
import html
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from .feeds import parse_pay, us_location, write_rows

COMPANIES = Path(__file__).with_name("ats_companies.csv")
URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
}
INTERVALS = {"1 YEAR": "YEARLY", "YEAR": "YEARLY", "YEARLY": "YEARLY", "PER-YEAR-SALARY": "YEARLY",
             "1 HOUR": "HOURLY", "HOUR": "HOURLY", "HOURLY": "HOURLY", "PER-HOUR-WAGE": "HOURLY",
             "1 MONTH": "MONTHLY", "PER-MONTH-SALARY": "MONTHLY", "1 WEEK": "WEEKLY"}


def text_of(markup):
    """Plain text from (possibly HTML-escaped) HTML."""
    markup = html.unescape(markup or "")
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", markup)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _row(source, board, job_id, title, company, location_text, listed, url, description,
         pay=(None, None, None), work_type=None, category=None):
    place = us_location(location_text)
    if place is None:
        return None
    low, high, period = pay if pay[0] else parse_pay(description)
    return {"job_id": f"{source}-{board}-{job_id}", "source": source, "title": (title or "").strip(),
            "company_name": company, "location": place[0], "listed_date": listed,
            "fetched_date": date.today().isoformat(), "min_salary": low, "max_salary": high,
            "pay_period": period, "currency": "USD", "work_type": work_type, "category": category,
            "description": description, "source_url": url}


def greenhouse(board, body, name=None):
    for job in body.get("jobs", []):
        yield _row("greenhouse", board, job["id"], job.get("title"), name or board.title(),
                   (job.get("location") or {}).get("name"), (job.get("updated_at") or "")[:10],
                   job.get("absolute_url"), text_of(job.get("content")))


def lever(board, body, name=None):
    for job in body if isinstance(body, list) else []:
        cats = job.get("categories") or {}
        locations = "; ".join(filter(None, [cats.get("location")] + (cats.get("allLocations") or [])))
        created = job.get("createdAt")
        listed = datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat() if created else None
        salary = job.get("salaryRange") or {}
        pay = (salary.get("min"), salary.get("max"), INTERVALS.get(str(salary.get("interval", "")).upper())) \
            if salary.get("min") and (salary.get("currency") or "USD") == "USD" else (None, None, None)
        description = "\n".join(filter(None, [job.get("descriptionPlain"), job.get("additionalPlain")] +
                                       [text_of(lst.get("content")) for lst in job.get("lists") or []]))
        yield _row("lever", board, job["id"], job.get("text"), name or board.title(), locations, listed,
                   job.get("hostedUrl"), description, pay, cats.get("commitment"), cats.get("team"))


def ashby(board, body, name=None):
    for job in body.get("jobs", []):
        address = ((job.get("address") or {}).get("postalAddress") or {})
        location = ", ".join(filter(None, [address.get("addressLocality"), address.get("addressRegion"),
                                           address.get("addressCountry")])) or job.get("location")
        if job.get("isRemote") and not address:
            location = f"Remote {job.get('location') or ''}"
        pay = (None, None, None)
        for comp in ((job.get("compensation") or {}).get("summaryComponents") or []):
            if comp.get("compensationType") == "Salary" and comp.get("currencyCode", "USD") == "USD" \
                    and comp.get("minValue"):
                pay = (comp["minValue"], comp.get("maxValue") or comp["minValue"],
                       INTERVALS.get(str(comp.get("interval", "")).upper(), "YEARLY"))
                break
        yield _row("ashby", board, job["id"], job.get("title"), name or board.title(), location,
                   (job.get("publishedAt") or "")[:10], job.get("jobUrl"),
                   job.get("descriptionPlain") or text_of(job.get("descriptionHtml")), pay,
                   job.get("employmentType"), job.get("department"))


PARSERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch_board(platform, board, name=None, session=None):
    """Rows for one company's US postings; [] if the board can't be read."""
    session = session or requests
    try:
        resp = session.get(URLS[platform].format(board=board), timeout=60,
                           headers={"User-Agent": "labor-jobs/1.0"})
        if resp.status_code != 200:
            return []
        return [r for r in PARSERS[platform](board, resp.json(), name) if r]
    except (requests.RequestException, ValueError):
        return []


def read_companies(path=COMPANIES):
    with open(path, newline="") as f:
        return [(r["platform"].strip().lower(), r["board"].strip(), (r.get("name") or "").strip() or None)
                for r in csv.DictReader(f) if r.get("platform") and r.get("board")]


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.ats", description=__doc__.split("\n\n")[0])
    p.add_argument("--companies", default=str(COMPANIES), help="CSV of platform,board,name")
    p.add_argument("--out", required=True)
    p.add_argument("--append", action="store_true", help="add to an existing file")
    p.add_argument("--workers", type=int, default=6, help="boards fetched at once (default 6)")
    args = p.parse_args(argv)
    companies = read_companies(args.companies)
    rows, empty = [], []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for (platform, board, _), found in zip(companies, pool.map(lambda c: fetch_board(*c), companies)):
            rows += found
            if not found:
                empty.append(f"{platform}:{board}")
    added = write_rows(rows, args.out, args.append)
    print(f"Wrote {added:,} US postings from {len(companies) - len(empty)} of {len(companies)} boards "
          f"to {args.out}")
    if empty:
        print("No US postings (or board not found): " + ", ".join(empty))


if __name__ == "__main__":
    main()
