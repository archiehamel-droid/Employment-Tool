"""Load LinkedIn job postings from the Kaggle dataset.

Dataset: https://www.kaggle.com/datasets/arshkon/linkedin-job-postings
(file postings.csv). Only the columns used here are required to exist:
title and location; salary, FIPS and date columns are used when present.
Other postings exports work too if they have title and location columns,
plus a listing date as listed_time (milliseconds since 1970) or as a date in
listed_date, posted_date or date.
"""

import hashlib
import re

import pandas as pd

from .states import ABBRS, FIPS_TO_ABBR, NAME_TO_ABBR

PAY_PERIOD_HOURS = {"HOURLY": 2080, "WEEKLY": 52, "BIWEEKLY": 26,
                    "MONTHLY": 12, "YEARLY": 1, "ANNUALLY": 1}
STATE_CODE = re.compile(r",\s*([A-Z]{2})\b")


def parse_state(location, fips=None):
    """Two-letter state from 'Austin, TX', 'Texas, United States' or a FIPS code."""
    if isinstance(location, str):
        m = STATE_CODE.search(location)
        if m and m.group(1) in ABBRS:
            return m.group(1)
        first = location.split(",")[0].strip().lower()
        if first in NAME_TO_ABBR:
            return NAME_TO_ABBR[first]
    if pd.notna(fips):
        try:
            code = int(float(fips))
        except ValueError:
            return None
        # County FIPS codes are SSCCC; state codes are SS.
        return FIPS_TO_ABBR.get(code // 1000 if code >= 1000 else code)
    return None


def annual_salary(df):
    """Annualized salary in USD, NaN when unknown or implausible."""
    def num(col):
        return pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(float("nan"), index=df.index)

    midpoint = num("med_salary").fillna((num("min_salary") + num("max_salary")) / 2)
    if "pay_period" in df:
        midpoint = midpoint * df["pay_period"].str.upper().map(PAY_PERIOD_HOURS)
    else:
        midpoint[:] = float("nan")
    salary = num("normalized_salary").fillna(midpoint)
    if "currency" in df:
        salary = salary.where(df["currency"].fillna("USD").str.upper() == "USD")
    return salary.where(salary.between(10_000, 1_000_000))


DATE_COLUMNS = ("listed_date", "posted_date", "date")


def _fallback_ids(df):
    """Stable IDs for exports without job_id, so history can de-duplicate."""
    cols = [c for c in ("title", "company_name", "location", *DATE_COLUMNS, "listed_time") if c in df]
    keys = df[cols].astype(str).agg("|".join, axis=1)
    return keys.map(lambda k: hashlib.sha1(k.encode()).hexdigest()[:16])


def load_postings(path, descriptions=False):
    df = pd.read_csv(path, dtype={"job_id": str, "zip_code": str, "fips": str},
                     low_memory=False)
    missing = {"title", "location"} - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")

    out = pd.DataFrame(index=df.index)
    out["job_id"] = df["job_id"] if "job_id" in df else _fallback_ids(df)
    out["title"] = df["title"]
    out["company_name"] = df.get("company_name")
    out["location"] = df["location"]
    fips = df["fips"] if "fips" in df else pd.Series(None, index=df.index)
    out["state"] = [parse_state(loc, f) for loc, f in zip(df["location"], fips)]
    county = pd.to_numeric(fips, errors="coerce")
    out["county_fips"] = county.where(county >= 1000).map(
        lambda c: f"{int(c):05d}" if pd.notna(c) else None)
    out["annual_salary"] = annual_salary(df)
    out["experience_level"] = df.get("formatted_experience_level")
    out["work_type"] = df.get("formatted_work_type")
    if descriptions:
        text = df["description"].fillna("") if "description" in df else pd.Series("", index=df.index)
        if "skills_desc" in df:
            text = text + "\n" + df["skills_desc"].fillna("")
        out["description"] = text
    if "listed_time" in df:
        out["listed_date"] = pd.to_datetime(
            pd.to_numeric(df["listed_time"], errors="coerce"), unit="ms").dt.date
    else:
        col = next((c for c in DATE_COLUMNS if c in df), None)
        if col:
            out["listed_date"] = pd.to_datetime(df[col], errors="coerce").dt.date
    return out


def load_all(paths, descriptions=False):
    """Load one or more postings files, keeping the first copy of each job_id."""
    frames = [load_postings(p, descriptions) for p in paths]
    return pd.concat(frames, ignore_index=True).drop_duplicates("job_id").reset_index(drop=True)
