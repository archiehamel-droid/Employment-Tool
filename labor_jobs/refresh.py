"""Fetch a month's postings from every source and rebuild the results and the
dashboard data in one go, for a scheduled job:

    python -m labor_jobs.refresh                 # last month (run early in the month)
    python -m labor_jobs.refresh --month 2026-11

Steps, each skipped when its output is already there (so a run that stopped
part way can be restarted), unless --force:

1. USAJOBS: federal announcements opened in the last 31 days, with their text.
2. Company job boards (Greenhouse, Lever, Ashby) in ats_companies.csv.
3. Adzuna: a sample of --sample-pages pages across categories and states.
4. The main run, to see which occupations and states are under-covered.
5. Adzuna --fill: --fill-pages searches aimed at under-covered occupations,
   one each for thin occupations first (see the README).
6. Adzuna: the newest --state-pages pages for each state with fewer than
   --thin-state postings.
7. The main run again, then the dashboard data (labor_jobs.dashboard).

Adzuna steps need ADZUNA_APP_ID and ADZUNA_APP_KEY; without them they are
skipped and the rest still runs. BLS_API_KEY and BLS_CONTACT_EMAIL are used
when set. Writes output/refresh-<month>.json with what each step did.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from .states import STATES


def last_month(today=None):
    """YYYY-MM for the month before `today`: a run early in December collects
    postings from the 31 days before, which are mostly November's."""
    today = today or date.today()
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{year}-{month:02d}"


def thin_states(results, below):
    """Full names of states with fewer than `below` postings in a results folder."""
    names = {abbr: name for abbr, name, _ in STATES}
    with (Path(results) / "states.csv").open() as f:
        return [names[r["state"]] for r in csv.DictReader(f)
                if r["state"] in names and float(r.get("postings") or 0) < below]


class Refresh:
    def __init__(self, args):
        self.args = args
        self.month = args.month
        self.log = {"month": self.month, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": []}
        self.adzuna = Path(f"data/adzuna/{self.month}.csv")
        self.usajobs = Path(f"data/usajobs/{self.month}.csv")
        self.ats = Path(f"data/ats/{self.month}.csv")
        self.results = Path(f"output/combined-{self.month}")
        self.has_adzuna = bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))

    def run(self, name, cmd, output=None):
        """Run one step as its own process, unless its output is already there."""
        if output is not None and Path(output).exists() and not self.args.force:
            self.log["steps"].append({"step": name, "status": "skipped (already done)"})
            print(f"== {name}: already done ({output})")
            return True
        print(f"== {name}", flush=True)
        start = time.time()
        code = subprocess.call([sys.executable, "-m", *cmd])
        status = "ok" if code == 0 else f"failed (exit {code})"
        self.log["steps"].append({"step": name, "status": status, "minutes": round((time.time() - start) / 60, 1)})
        return code == 0

    def skip(self, name, why):
        self.log["steps"].append({"step": name, "status": f"skipped ({why})"})
        print(f"== {name}: skipped ({why})")

    def main_run(self, name):
        postings = [str(p) for p in (self.adzuna, self.usajobs, self.ats) if p.exists()]
        if not postings:
            raise SystemExit("No postings were fetched; nothing to run.")
        # Only a finished run counts as done: a failed one can leave an empty folder.
        if (self.results / "coverage.json").exists() and not self.args.force and name == "main run (first)":
            self.log["steps"].append({"step": name, "status": "skipped (already done)"})
            return True
        return self.run(name, ["labor_jobs", "--postings", *postings, "--out", str(self.results), "--no-history"])

    def go(self):
        a = self.args
        if not os.environ.get("BLS_CONTACT_EMAIL"):
            # bls.gov refuses downloads (HTTP 403) from clients that don't give a contact address.
            raise SystemExit("BLS_CONTACT_EMAIL is not set; bls.gov refuses the BLS downloads without it.")
        usajobs = ["labor_jobs.usajobs", "--days", "31", "--out", str(self.usajobs)] + ([] if a.no_text else ["--text"])
        self.run("USAJOBS", usajobs, self.usajobs)
        self.run("company job boards", ["labor_jobs.ats", "--out", str(self.ats)], self.ats)
        if self.has_adzuna:
            self.run("Adzuna sample", ["labor_jobs.adzuna", "--pages", str(a.sample_pages), "--out", str(self.adzuna)],
                     self.adzuna)
        else:
            self.skip("Adzuna", "ADZUNA_APP_ID / ADZUNA_APP_KEY not set")

        self.main_run("main run (first)")
        if self.has_adzuna and a.fill_pages:
            fill = ["labor_jobs.adzuna", "--fill", str(self.results), "--pages", str(a.fill_pages),
                    "--thin", str(a.thin), "--out", str(self.adzuna), "--append"]
            if a.title_counts and Path(a.title_counts).exists():
                fill += ["--title-counts", a.title_counts]
            self.run("Adzuna fill", fill)
            for state in thin_states(self.results, a.thin_state):
                self.run(f"Adzuna newest in {state}", ["labor_jobs.adzuna", "--newest", "--where", state,
                                                       "--pages", str(a.state_pages), "--out", str(self.adzuna),
                                                       "--append"])
            self.main_run("main run (with fill)")
        self.run("dashboard data", ["labor_jobs.dashboard", "--results", str(self.results), "--out", a.dashboard])
        # The plan the daily Adzuna runs use until next month: shortfalls from this month's results.
        if (self.results / "coverage.json").exists():
            export = ["labor_jobs.daily", "export", "--results", str(self.results), "--out", a.fill_inputs]
            if a.title_counts and Path(a.title_counts).exists():
                export += ["--title-counts", a.title_counts]
            self.run("fill inputs for daily runs", export)

        coverage = self.results / "coverage.json"
        if coverage.exists():
            cov = json.loads(coverage.read_text())
            self.log["postings"] = cov.get("postings")
            self.log["matched"] = cov.get("matched_soc")
            self.log["occupations"] = cov.get("occupations_found")
        self.log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out = Path(f"output/refresh-{self.month}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.log, indent=1))
        print(json.dumps(self.log, indent=1))
        failed = [s for s in self.log["steps"] if s["status"].startswith("failed")]
        return 1 if failed else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.refresh", description=__doc__.split("\n\n")[0])
    p.add_argument("--month", default=last_month(), help="YYYY-MM the files are named for (default: last month)")
    p.add_argument("--sample-pages", type=int, default=300, help="Adzuna sample pages (default 300)")
    p.add_argument("--fill-pages", type=int, default=400, help="Adzuna fill searches (default 400; 0 skips)")
    p.add_argument("--thin", type=int, default=10, help="fill: occupations under this many postings get one search")
    p.add_argument("--thin-state", type=int, default=300, help="states under this many postings get newest pages")
    p.add_argument("--state-pages", type=int, default=5)
    p.add_argument("--title-counts", default="data/linkedin/postings.csv",
                   help="postings file whose titles rank fill searches, used when present")
    p.add_argument("--no-text", action="store_true", help="skip fetching USAJOBS announcement text (faster)")
    p.add_argument("--dashboard", default="output/dashboard")
    p.add_argument("--fill-inputs", default="output/fill_inputs.json",
                   help="where to save the search plan the daily Adzuna runs use")
    p.add_argument("--force", action="store_true", help="redo steps whose output already exists")
    sys.exit(Refresh(p.parse_args(argv)).go())


if __name__ == "__main__":
    main()
