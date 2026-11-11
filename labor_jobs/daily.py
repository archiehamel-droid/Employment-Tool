"""Daily Adzuna collection, for a scheduled job, and folding the days into a month:

    python -m labor_jobs.daily fetch --fill-inputs fill_inputs.json --out data/adzuna/daily/2026-11-12.csv
    python -m labor_jobs.daily combine --out data/adzuna/2026-11.csv day1.csv day2.csv ...
    python -m labor_jobs.daily export --results output/combined-2026-10 --out fill_inputs.json

fetch takes postings listed in the last --max-days-old days (2 by default, so each
day adds mostly new ones): a sample of --sample-pages pages spread across
categories and states, then --fill-pages searches for under-covered occupations
(one each for thin occupations first, then by shortfall), planned from a
fill-inputs file. It stays well inside Adzuna's free limits: about 30 calls to
size the categories plus the pages asked for (and a retry for a page whose
results are off-topic). If Adzuna refuses for the day, what was fetched is kept.

combine merges day files into one month file, dropping repeats of the same
posting (job_id), keeping the latest copy; an existing month file is merged in.

export saves the fill-inputs file from a results folder (see
adzuna.export_fill_inputs); the monthly refresh does this after each run.
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from . import adzuna
from .feeds import FIELDS, write_rows


def fetch(out, fill_inputs=None, sample_pages=150, fill_pages=100, max_days_old=2, thin=10, seed=None):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise SystemExit("ADZUNA_APP_ID and ADZUNA_APP_KEY are not set.")
    out = Path(out)
    report = {"file": str(out), "max_days_old": max_days_old}
    rows = adzuna.sample(app_id, app_key, sample_pages, max_days_old=max_days_old, seed=seed)
    report["sample_postings"] = write_rows(adzuna._until_error(rows), out)
    if fill_inputs and fill_pages:
        employment, postings, titles = adzuna.load_fill_inputs(fill_inputs)
        # Up to 40% of the searches for thin occupations, a different set each day.
        plan = adzuna.fill_plan(employment, postings, titles, fill_pages, thin=thin,
                                thin_calls=int(fill_pages * 0.4), rotate=out.stem)
        rows = adzuna.fill(app_id, app_key, plan, max_days_old=max_days_old)
        report["fill_searches"] = len(plan)
        report["fill_postings"] = write_rows(adzuna._until_error(rows), out, append=True)
    report["postings"] = report["sample_postings"] + report.get("fill_postings", 0)
    print(json.dumps(report))
    return report


def combine(out, day_files):
    """Merge day files (and an existing month file) into `out`, one row per job_id."""
    csv.field_size_limit(sys.maxsize)
    rows = {}
    for path in [p for p in [Path(out)] if p.exists()] + [Path(f) for f in sorted(day_files)]:
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                rows[row.get("job_id") or f"{path.name}:{len(rows)}"] = row
    tmp = Path(str(out) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows.values())
    tmp.replace(out)
    print(f"{len(rows):,} postings in {out} from {len(day_files)} day files")
    return len(rows)


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.daily", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="one day's Adzuna postings")
    f.add_argument("--out", required=True)
    f.add_argument("--fill-inputs", help="file saved by 'export' (fill searches are skipped without it)")
    f.add_argument("--sample-pages", type=int, default=150)
    f.add_argument("--fill-pages", type=int, default=100)
    f.add_argument("--max-days-old", type=int, default=2)
    f.add_argument("--thin", type=int, default=10)
    f.add_argument("--seed", type=int)
    c = sub.add_parser("combine", help="merge day files into a month file")
    c.add_argument("--out", required=True)
    c.add_argument("files", nargs="*")
    e = sub.add_parser("export", help="save the fill-inputs file from a results folder")
    e.add_argument("--results", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--oews", default="data/cache/oesm24nat.zip")
    e.add_argument("--onet-dir", default="data/cache/onet_29_3")
    e.add_argument("--title-counts", nargs="*", default=[])
    args = p.parse_args(argv)
    if args.command == "fetch":
        fetch(args.out, args.fill_inputs, args.sample_pages, args.fill_pages, args.max_days_old, args.thin, args.seed)
    elif args.command == "combine":
        combine(args.out, args.files)
    else:
        data = adzuna.export_fill_inputs(args.results, args.out, args.oews, args.onet_dir, args.title_counts)
        print(f"Saved fill inputs for {len(data['titles'])} occupations to {args.out}")


if __name__ == "__main__":
    main()
