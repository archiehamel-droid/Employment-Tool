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
        if col not in df:
            return pd.Series(float("nan"), index=df.index)
        values = pd.to_numeric(df[col], errors="coerce")
        return values.where(values > 0)  # Adzuna gives 0 for "not stated"

    # Midpoint of the range, or whichever end is given.
    ends = pd.concat([num("min_salary"), num("max_salary")], axis=1).mean(axis=1)
    midpoint = num("med_salary").fillna(ends)
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
    # All text: a column that is entirely blank would otherwise be read as
    # numbers. Numeric fields are converted where they are used.
    df = pd.read_csv(path, dtype=str, low_memory=False)
    missing = {"title", "location"} - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")

    out = pd.DataFrame(index=df.index)
    out["job_id"] = df["job_id"] if "job_id" in df else _fallback_ids(df)
    inferred = source_of(out["job_id"])
    out["source"] = df["source"].fillna(inferred) if "source" in df else inferred
    out["title"] = df["title"]
    out["company_name"] = df.get("company_name")
    out["location"] = df["location"]
    fips = df["fips"] if "fips" in df else pd.Series(None, index=df.index)
    out["state"] = [parse_state(loc, f) for loc, f in zip(df["location"], fips)]
    # County name, when the export has one (Adzuna does).
    out["county_name"] = df["county"] if "county" in df else None
    county = pd.to_numeric(fips, errors="coerce")
    out["county_fips"] = county.where(county >= 1000).map(
        lambda c: f"{int(c):05d}" if pd.notna(c) else None)
    out["annual_salary"] = annual_salary(df)
    out["experience_level"] = df.get("formatted_experience_level")
    out["work_type"] = df.get("formatted_work_type")
    url = next((c for c in ("source_url", "job_posting_url", "application_url") if c in df), None)
    out["url"] = df[url] if url else None
    if descriptions:
        text = df["description"].fillna("") if "description" in df else pd.Series("", index=df.index)
        if "skills_desc" in df:
            text = text + "\n" + df["skills_desc"].fillna("")
        out["description"] = text
    if "fetched_date" in df:
        out["fetched_date"] = pd.to_datetime(df["fetched_date"], errors="coerce").dt.date
    if "listed_time" in df:
        out["listed_date"] = pd.to_datetime(
            pd.to_numeric(df["listed_time"], errors="coerce"), unit="ms").dt.date
    else:
        col = next((c for c in DATE_COLUMNS if c in df), None)
        if col:
            out["listed_date"] = pd.to_datetime(df[col], errors="coerce").dt.date
    return out


FEED_PREFIXES = ("adzuna", "usajobs", "greenhouse", "lever", "ashby")
# When the same job is in several sources, keep the most direct one.
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "usajobs": 1, "adzuna": 2, "linkedin": 3}
COMPANY_SUFFIXES = re.compile(r"\b(inc|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|plc|lp|"
                              r"holdings|group|the|usa|us)\b")


def source_of(job_ids):
    """Feed name from fetcher job IDs ("adzuna-123"); Kaggle IDs are LinkedIn's."""
    prefix = job_ids.astype(str).str.split("-").str[0]
    return prefix.where(prefix.isin(FEED_PREFIXES), "linkedin")


def _norm(text):
    text = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()) if isinstance(text, str) else ""
    return " ".join(text.split())


def merge_duplicates(postings):
    """Drop a posting when a more direct source has a posting with the same
    title, company, state and city; the kept one lists the others in
    also_listed_on. Postings that match within one source are all kept: a
    company can have several identical openings."""
    city = postings["location"].fillna("").str.split(",").str[0]
    key = (postings["title"].map(_norm) + "|" + postings["company_name"].map(
        lambda c: " ".join(COMPANY_SUFFIXES.sub(" ", _norm(c)).split())) + "|"
        + postings["state"].fillna("") + "|" + city.map(_norm))
    rank = postings["source"].map(SOURCE_PRIORITY).fillna(9)
    best = rank.groupby(key).transform("min")
    keep = rank == best
    others = (pd.DataFrame({"key": key, "source": postings["source"]})[~keep]
              .drop_duplicates().groupby("key")["source"].agg(lambda s: ", ".join(sorted(s))))
    out = postings[keep].copy()
    out["also_listed_on"] = key[keep].map(others)
    return out.reset_index(drop=True), int((~keep).sum())


def load_all(paths, descriptions=False):
    """Load one or more postings files, keeping the first copy of each
    job_id and merging the same job found in different sources."""
    frames = [load_postings(p, descriptions) for p in paths]
    df = pd.concat(frames, ignore_index=True).drop_duplicates("job_id").reset_index(drop=True)
    df, merged = merge_duplicates(df)
    df.attrs["merged_duplicates"] = merged
    return df
