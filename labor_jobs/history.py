"""Postings history: every run adds its postings to a SQLite file, so posting
counts can be compared month to month as new postings files are loaded.

A posting is stored once per job_id, under the month it was listed; loading
the same file twice changes nothing. Occupations are compared by their share
of each month's postings rather than raw counts, so months with bigger or
smaller postings files stay comparable. Each posting also records its feed
("linkedin", "adzuna", ...), and trends only compare months from one feed:
different job sites carry different mixes of jobs, so mixing them would show
the difference between sites rather than change over time.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    job_id TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    soc_code TEXT,
    state TEXT,
    source TEXT,
    feed TEXT
)
"""


def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.execute(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(postings)")}
    if "feed" not in columns:  # files made before feeds were tracked
        con.execute("ALTER TABLE postings ADD COLUMN feed TEXT")
        con.execute("UPDATE postings SET feed = CASE WHEN job_id LIKE 'adzuna-%' "
                    "THEN 'adzuna' ELSE 'linkedin' END")
    return con


def detect_feed(postings):
    """"adzuna" for files from labor_jobs.adzuna, otherwise "linkedin"."""
    ids = postings["job_id"].astype(str)
    return "adzuna" if len(ids) and ids.str.startswith("adzuna-").all() else "linkedin"


def record(postings, months, db_path, source, feed):
    """Add postings with a known month; returns how many were new."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rows = (postings.assign(month=months).dropna(subset=["month"])
            [["job_id", "month", "soc_code", "state"]])
    rows = rows.astype(object).where(rows.notna(), None)
    with _connect(db_path) as con:
        before = con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        # Re-running with better title matching updates the occupation.
        con.executemany(
            "INSERT INTO postings (job_id, month, soc_code, state, source, feed) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE "
            "SET soc_code = excluded.soc_code, state = excluded.state",
            [(*r, str(source), feed) for r in rows.itertuples(index=False)])
        after = con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    return after - before


def monthly_counts(db_path, feed=None):
    with _connect(db_path) as con:
        where, params = ("WHERE feed = ?", (feed,)) if feed else ("", ())
        return pd.read_sql_query(
            f"SELECT month, soc_code, COUNT(*) AS postings FROM postings {where} "
            "GROUP BY month, soc_code", con, params=params)


def covered_months(counts, min_month_postings=500):
    """Months with enough postings to compare (stray early listings in a
    file don't count as a month of data)."""
    totals = counts.groupby("month")["postings"].sum()
    return sorted(totals[totals >= min_month_postings].index)


def trends(counts, min_months=3, min_month_postings=500, min_postings=30):
    """Per occupation: share of postings in each covered month, and the
    trend in that share (percent change per month, from a least-squares
    line). Returns (frame, months); the frame is empty until there are at
    least `min_months` covered months."""
    months = covered_months(counts, min_month_postings)
    if len(months) < min_months:
        return pd.DataFrame(), months

    counts = counts[counts["month"].isin(months)]
    totals = counts.groupby("month")["postings"].sum()
    matched = counts.dropna(subset=["soc_code"])
    shares = (matched.pivot_table(index="soc_code", columns="month", values="postings",
                                  aggfunc="sum", fill_value=0)
              .reindex(columns=months, fill_value=0) / totals[months])
    totals_by_soc = matched.groupby("soc_code")["postings"].sum()
    shares = shares[totals_by_soc.reindex(shares.index) >= min_postings]

    x = np.arange(len(months))
    slope = np.polyfit(x, shares.to_numpy().T, 1)[0]
    df = pd.DataFrame({
        "soc_code": shares.index,
        "postings": totals_by_soc.reindex(shares.index).to_numpy(),
        "first_month_share_pct": (shares[months[0]] * 100).round(3).to_numpy(),
        "last_month_share_pct": (shares[months[-1]] * 100).round(3).to_numpy(),
        "trend_pct_per_month": (slope / shares.mean(axis=1).to_numpy() * 100).round(1),
    })
    return df.sort_values("trend_pct_per_month", ascending=False), months
