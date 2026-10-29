"""H-1B and related visa filings (Labor Condition Applications) from the
Department of Labor: the wage an employer offers for a specific job, with
the job title the employer used and the O*NET occupation code its lawyers
assigned. Published quarterly at
https://www.dol.gov/agencies/eta/foreign-labor/performance, one file per
quarter of the federal fiscal year (Q1 is October to December).

Two uses here:
* Offered salaries per occupation, including specialized O*NET occupations
  that BLS doesn't report wages for.
* Employer job titles paired with occupation codes, as extra reference titles
  for matching postings.

Filings cover only jobs sponsored for these visas, so they lean toward tech,
research, finance and medicine, and employers must pay at least the local
prevailing wage. Only job, employer, worksite and wage columns are read; the
file's contact names, emails and phone numbers are never loaded.
"""

from pathlib import Path

import pandas as pd

from .fetch import download
from .soc_mapping import normalize_title

H1B_URL = ("https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/"
           "LCA_Disclosure_Data_FY{year}_Q{quarter}.xlsx")
COLUMNS = [
    "CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE", "SOC_CODE",
    "FULL_TIME_POSITION", "TOTAL_WORKER_POSITIONS", "EMPLOYER_NAME", "NAICS_CODE",
    "WORKSITE_CITY", "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY",
    "PW_WAGE_LEVEL",
]
PER_YEAR = {"YEAR": 1, "MONTH": 12, "BI-WEEKLY": 26, "WEEK": 52, "HOUR": 2080}
VALID_STATUS = {"CERTIFIED", "CERTIFIED - WITHDRAWN"}


def h1b_files(cache_dir, fiscal_year, user_agent, quarters=(1, 2, 3, 4)):
    """The quarterly files for a fiscal year (about 50-130 MB each)."""
    paths = []
    for quarter in quarters:
        url = H1B_URL.format(year=fiscal_year, quarter=quarter)
        paths.append(download(url, Path(cache_dir) / Path(url).name, user_agent))
    return paths


def _slim(path):
    """Copy of the needed columns, cached beside the spreadsheet: reading
    the full 97-column file takes minutes."""
    path = Path(path)
    slim = path.with_suffix(".slim.csv.gz")
    if slim.exists() and slim.stat().st_mtime >= path.stat().st_mtime:
        return slim
    import openpyxl

    print(f"  extracting wage columns from {path.name} (one-time, a few minutes)...")
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = wb[wb.sheetnames[0]].iter_rows(values_only=True)
    header = [str(h).strip().upper() if h else "" for h in next(rows)]
    missing = set(COLUMNS) - set(header)
    if missing:
        raise ValueError(f"{path.name} is missing columns {sorted(missing)}")
    idx = [header.index(c) for c in COLUMNS]
    # The sheet has blank rows after the data.
    df = pd.DataFrame(([r[i] for i in idx] for r in rows if r[idx[0]]), columns=COLUMNS)
    wb.close()
    df.to_csv(slim, index=False, compression="gzip")
    return slim


def read_h1b(paths):
    """Certified full-time filings with an annual offered wage, from one or
    more disclosure files (.xlsx as published, or .csv)."""
    frames = []
    for path in [paths] if isinstance(paths, (str, Path)) else paths:
        path = Path(path)
        src = path if path.suffix in (".csv", ".gz") else _slim(path)
        frame = pd.read_csv(src, dtype=str, low_memory=False)
        frame.columns = [c.upper() for c in frame.columns]
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True).drop_duplicates("CASE_NUMBER", keep="last")

    df = df[df["CASE_STATUS"].str.upper().isin(VALID_STATUS)
            & (df["FULL_TIME_POSITION"].str.upper() == "Y")]
    code = df["SOC_CODE"].str.strip().str.extract(r"^(\d{2}-\d{4})(\.\d{2})?$")
    out = pd.DataFrame({
        "case_number": df["CASE_NUMBER"],
        "job_title": df["JOB_TITLE"],
        "onet_code": code[0] + code[1].fillna(".00"),
        "employer": df["EMPLOYER_NAME"].str.strip().str.upper(),
        "state": df["WORKSITE_STATE"].str.strip().str.upper(),
        "city": df["WORKSITE_CITY"].str.strip(),
        "wage_level": df["PW_WAGE_LEVEL"],
        "positions": pd.to_numeric(df["TOTAL_WORKER_POSITIONS"], errors="coerce").fillna(1),
        "decision_date": pd.to_datetime(df["DECISION_DATE"], errors="coerce"),
    })
    wage = pd.to_numeric(df["WAGE_RATE_OF_PAY_FROM"].str.replace(r"[$,]", "", regex=True),
                         errors="coerce")
    out["annual_wage"] = wage * df["WAGE_UNIT_OF_PAY"].str.upper().str.strip().map(PER_YEAR)
    out["annual_wage"] = out["annual_wage"].where(out["annual_wage"].between(20_000, 2_000_000))
    out = out.dropna(subset=["onet_code", "annual_wage"])
    out["soc_code"] = out["onet_code"].str[:7]
    return out.reset_index(drop=True)


def summarize(h1b, keys):
    """Offered wages per group: filings, median and middle-50% range, share
    at the senior (Level IV) prevailing-wage level, and top employers."""
    g = h1b.groupby(keys)
    df = g.agg(h1b_filings=("case_number", "size"),
               h1b_median_wage=("annual_wage", "median"),
               h1b_wage_p25=("annual_wage", lambda s: s.quantile(0.25)),
               h1b_wage_p75=("annual_wage", lambda s: s.quantile(0.75)),
               h1b_level_iv_pct=("wage_level", lambda s: (s == "IV").mean() * 100))
    df["h1b_top_employers"] = g["employer"].agg(
        lambda s: ", ".join(s.value_counts().head(3).index.str.title()))
    for col in ("h1b_median_wage", "h1b_wage_p25", "h1b_wage_p75"):
        df[col] = df[col].round()
    df["h1b_level_iv_pct"] = df["h1b_level_iv_pct"].round(1)
    return df.reset_index()


def reference_titles(h1b, min_employers=3, min_share=0.6):
    """{normalized job title: O*NET code} for titles that at least
    `min_employers` employers used, when one code accounts for at least
    `min_share` of their filings. Employers' internal codes like "JC60" or
    "- 2 - US" are stripped first."""
    titles = h1b["job_title"].fillna("").str.replace(r"\b[A-Z]{1,3}\d{1,4}\b|-\s*\d+\s*-.*$", " ",
                                                     regex=True)
    df = pd.DataFrame({"key": titles.map(normalize_title), "onet_code": h1b["onet_code"],
                       "employer": h1b["employer"]})
    df = df[df["key"].str.len() > 0]
    stats = df.groupby("key").agg(employers=("employer", "nunique"), filings=("onet_code", "size"))
    top = df.groupby(["key", "onet_code"]).size().rename("n").reset_index() \
            .sort_values("n", ascending=False).drop_duplicates("key").set_index("key")
    top = top.join(stats)
    keep = (top["employers"] >= min_employers) & (top["n"] / top["filings"] >= min_share)
    return dict(zip(top.index[keep], top.loc[keep, "onet_code"]))
