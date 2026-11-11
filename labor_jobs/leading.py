"""Leading signals of job openings by industry, and how well each has predicted them.

Two free monthly signals that move before hiring does:

* Indeed Hiring Lab's Job Postings Index (github.com/hiring-lab/job_postings_tracker):
  postings on Indeed by occupational category, seasonally adjusted, February 2020 = 100.
  Categories are occupations, not industries, so each JOLTS industry gets the average
  of the categories that mostly work in it (INDEED_CATEGORIES); some industries have
  none. Total nonfarm and total private use Indeed's all-postings index.
* Census Business Formation Statistics (census.gov/econ/bfs): applications to start a
  business (EIN applications), seasonally adjusted, by NAICS sector.

track_record() checks each signal against what JOLTS openings then did: for every
month from 2021 on (after the pandemic swings), the signal's change over the past 3
months against openings' change over the next 6. It reports how often they moved the
same way, against the share expected by chance, and whether predicting the 6-month
change from the signal (a line fitted only on earlier months) beat assuming no change.
Months overlap, so the counts aren't independent: read them as a guide, not a test
of significance.
"""

import io
from pathlib import Path

import numpy as np
import pandas as pd

from .fetch import download

INDEED_URL = "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master/US/{name}"
BFS_URL = "https://www.census.gov/econ/bfs/csv/bfs_monthly.csv"

# Indeed categories whose jobs sit mostly in each JOLTS industry.
INDEED_CATEGORIES = {
    "Health care and social assistance": ["Nursing", "Physicians & Surgeons", "Medical Technician",
                                          "Medical Information", "Dental", "Pharmacy", "Therapy",
                                          "Personal Care & Home Health", "Childcare", "Community & Social Service"],
    "Retail trade": ["Retail"],
    "Accommodation and food services": ["Food Preparation & Service", "Hospitality & Tourism"],
    "Construction": ["Construction"],
    "Manufacturing": ["Production & Manufacturing", "Industrial Engineering", "Mechanical Engineering"],
    "Transportation, warehousing, and utilities": ["Driving", "Loading & Stocking", "Logistic Support", "Aviation"],
    "Information": ["Media & Communications"],
    "Professional and business services": ["Software Development", "IT Systems & Solutions",
                                           "IT Infrastructure, Operations & Support", "Data & Analytics",
                                           "Accounting", "Legal", "Architecture", "Marketing",
                                           "Scientific Research & Development", "Project Management",
                                           "Administrative Assistance", "Human Resources", "Cleaning & Sanitation"],
    "Financial activities": ["Banking & Finance", "Insurance"],
    "Private educational services": ["Education & Instruction"],
    "Arts, entertainment, and recreation": ["Arts & Entertainment"],
    "Other services": ["Installation & Maintenance"],
    "Government": ["Security & Public Safety"],
}
# Census BFS sectors in each JOLTS industry.
BFS_SECTORS = {
    "Total nonfarm": ["TOTAL"], "Total private": ["TOTAL"],
    "Mining and logging": ["NAICS21"], "Construction": ["NAICS23"], "Manufacturing": ["NAICSMNF"],
    "Wholesale trade": ["NAICS42"], "Retail trade": ["NAICSRET"],
    "Transportation, warehousing, and utilities": ["NAICSTW", "NAICS22"], "Information": ["NAICS51"],
    "Financial activities": ["NAICS52", "NAICS53"],
    "Professional and business services": ["NAICS54", "NAICS55", "NAICS56"],
    "Private educational services": ["NAICS61"], "Health care and social assistance": ["NAICS62"],
    "Arts, entertainment, and recreation": ["NAICS71"], "Accommodation and food services": ["NAICS72"],
    "Other services": ["NAICS81"],
}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                         "nov", "dec"])}
TEST_START = "2021-01"


def indeed_files(cache_dir, user_agent):
    names = ["job_postings_by_sector_US.csv", "aggregate_job_postings_US.csv"]
    return [download(INDEED_URL.format(name=n), Path(cache_dir) / "indeed" / n, user_agent, max_age_days=7)
            for n in names]


def bfs_file(cache_dir, user_agent):
    return download(BFS_URL, Path(cache_dir) / "bfs_monthly.csv", user_agent, max_age_days=7)


def read_indeed(by_sector, aggregate):
    """Monthly average of Indeed's total-postings index per JOLTS industry:
    (industry, month, value, detail) where detail names the categories used."""
    s = pd.read_csv(by_sector)
    s = s[s["variable"] == "total postings"]
    s["month"] = s["date"].str[:7]
    monthly = s.groupby(["display_name", "month"])["indeed_job_postings_index"].mean()
    rows = []
    for industry, cats in INDEED_CATEGORIES.items():
        have = [c for c in cats if c in monthly.index.get_level_values(0)]
        if not have:
            continue
        m = monthly.loc[have].groupby("month").mean()
        rows += [{"industry": industry, "month": k, "value": round(float(v), 2), "detail": ", ".join(have)}
                 for k, v in m.items()]
    a = pd.read_csv(aggregate)
    if "variable" in a:
        a = a[a["variable"] == "total postings"]
    a["month"] = a["date"].str[:7]
    col = "indeed_job_postings_index_SA" if "indeed_job_postings_index_SA" in a else "indeed_job_postings_index"
    m = a.groupby("month")[col].mean()
    for industry in ("Total nonfarm", "Total private"):
        rows += [{"industry": industry, "month": k, "value": round(float(v), 2), "detail": "All postings"}
                 for k, v in m.items()]
    out = pd.DataFrame(rows)
    # The latest month is usually partial: keep it only with at least three weeks of data.
    last = s["date"].max()
    if last[8:10] < "21":
        out = out[out["month"] < last[:7]]
    return out


def read_bfs(path, start_year=2019):
    """Seasonally adjusted business applications per JOLTS industry by month."""
    b = pd.read_csv(path)
    b = b[(b["sa"] == "A") & (b["series"] == "BA_BA") & (b["geo"] == "US") & (b["year"] >= start_year)]
    long = b.melt(id_vars=["naics_sector", "year"], value_vars=list(MONTHS), var_name="mon", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["value"])
    long["month"] = long["year"].astype(str) + "-" + long["mon"].map(MONTHS).map("{:02d}".format)
    rows = []
    for industry, sectors in BFS_SECTORS.items():
        part = long[long["naics_sector"].isin(sectors)]
        m = part.groupby("month")["value"].sum(min_count=len(sectors))
        rows += [{"industry": industry, "month": k, "value": float(v), "detail": " + ".join(sectors)}
                 for k, v in m.dropna().items()]
    return pd.DataFrame(rows)


def track_record(signal, openings, lag=3, horizon=6, start=TEST_START, min_train=18):
    """How a signal's past `lag`-month change lined up with openings' next `horizon`-month
    change. signal and openings are Series indexed by month (YYYY-MM)."""
    sig, op = signal.sort_index(), openings.sort_index()
    months = [m for m in sig.index if m >= start]
    x, y, used = [], [], []
    for m in months:
        i = sig.index.get_loc(m)
        if i < lag or m not in op.index:
            continue
        j = op.index.get_loc(m)
        if j + horizon >= len(op):
            continue
        x.append(sig.iloc[i] / sig.iloc[i - lag] - 1)
        y.append(op.iloc[j + horizon] / op.iloc[j] - 1)
        used.append(m)
    x, y = np.array(x), np.array(y)
    n = len(x)
    if n < min_train + 6:
        return None
    same = float(np.mean(np.sign(x) == np.sign(y)))
    up = float(np.mean(y > 0))
    chance = up * float(np.mean(x > 0)) + (1 - up) * float(np.mean(x <= 0))
    # Out of sample: predict each month from a line fitted on earlier months only.
    errs_sig, errs_flat = [], []
    for k in range(min_train, n):
        b, a = np.polyfit(x[:k], y[:k], 1)
        errs_sig.append(abs(a + b * x[k] - y[k]))
        errs_flat.append(abs(y[k]))
    err_sig, err_flat = float(np.mean(errs_sig)), float(np.mean(errs_flat))
    # What the signal's latest change points to, from a line fitted on every tested month.
    b, a = np.polyfit(x, y, 1)
    latest = sig.iloc[-1] / sig.iloc[-1 - lag] - 1 if len(sig) > lag else np.nan
    return {"points_to": round(100 * float(a + b * latest), 1) if latest == latest else None,
            "n": n, "first": used[0], "last": used[-1], "same_direction": round(same, 3),
            "chance": round(chance, 3), "corr": round(float(np.corrcoef(x, y)[0, 1]), 3),
            "err_signal": round(100 * err_sig, 1), "err_no_change": round(100 * err_flat, 1),
            "helps": err_sig < err_flat * 0.97 and same > chance}


def build(jolts_series, cache_dir, user_agent):
    """(signals, tests) tables for the dashboard. jolts_series has industry, month,
    kind ('actual'/'forecast') and openings."""
    sector, aggregate = indeed_files(cache_dir, user_agent)
    indeed = read_indeed(sector, aggregate).assign(signal="postings")
    bfs = read_bfs(bfs_file(cache_dir, user_agent)).assign(signal="applications")
    signals = pd.concat([indeed, bfs], ignore_index=True)
    actual = jolts_series[jolts_series["kind"] == "actual"]
    tests = []
    for (industry, kind), g in signals.groupby(["industry", "signal"]):
        s = g.set_index("month")["value"].sort_index()
        op = actual[actual["industry"] == industry].set_index("month")["openings"].astype(float).sort_index()
        row = {"industry": industry, "signal": kind, "detail": g["detail"].iloc[0], "latest_month": s.index[-1],
               "latest": s.iloc[-1],
               "change_3m": round(100 * (s.iloc[-1] / s.iloc[-4] - 1), 1) if len(s) > 3 else None,
               "change_12m": round(100 * (s.iloc[-1] / s.iloc[-13] - 1), 1) if len(s) > 12 else None}
        rec = track_record(s, op) if len(op) else None
        tests.append({**row, **(rec or {"n": 0})})
    keep = signals["month"] >= "2020-01"
    return signals[keep][["industry", "signal", "month", "value"]], pd.DataFrame(tests)


def read_csv_text(text):
    return pd.read_csv(io.StringIO(text))
