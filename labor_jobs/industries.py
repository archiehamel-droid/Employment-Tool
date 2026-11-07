"""Which industries employ each occupation, from BLS OEWS industry-specific
estimates (May 2024, 3-digit NAICS industries: "Hospitals", "Nursing and
Residential Care Facilities"...), with each industry's median wage for that
occupation and the JOLTS industry it belongs to, so a job's industries can be
linked to their job-openings trend.

OEWS covers wage and salary jobs; self-employed workers and most farm jobs
aren't in it.
"""

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from .fetch import download

URL = "https://www.bls.gov/oes/special-requests/oesm{yy}in4.zip"
# NAICS sector (first two digits) -> JOLTS industry name, as in jolts_forecast.csv.
JOLTS_INDUSTRY = {
    "21": "Mining and logging", "22": "Transportation, warehousing, and utilities", "23": "Construction",
    "31": "Manufacturing", "32": "Manufacturing", "33": "Manufacturing", "42": "Wholesale trade",
    "44": "Retail trade", "45": "Retail trade", "48": "Transportation, warehousing, and utilities",
    "49": "Transportation, warehousing, and utilities", "51": "Information", "52": "Financial activities",
    "53": "Financial activities", "54": "Professional and business services",
    "55": "Professional and business services", "56": "Professional and business services",
    "61": "Private educational services", "62": "Health care and social assistance",
    "71": "Arts, entertainment, and recreation", "72": "Accommodation and food services", "81": "Other services",
    "99": "Government",
}


def oews_industry_file(cache_dir, year, user_agent):
    yy = f"{year % 100:02d}"
    return download(URL.format(yy=yy), Path(cache_dir) / f"oesm{yy}in4.zip", user_agent)


def jolts_industry(naics):
    """The JOLTS industry a NAICS code falls in ("1133" logging is mining and logging), or None."""
    naics = str(naics)
    if naics.startswith("113"):
        return "Mining and logging"
    return JOLTS_INDUSTRY.get(naics[:2])


def short_title(title):
    """Industry titles without OEWS's notes ("..., excluding ... (OEWS Designation)")."""
    title = re.sub(r"\s*\(OEWS Designation\)", "", str(title))
    if title.startswith("Federal, State, and Local Government"):
        return "Government (federal, state and local)"
    return title.strip()


def read_industry_employment(path):
    """Detailed occupations by 3-digit industry: soc_code, naics, industry,
    employment and median annual wage. Suppressed values are blank."""
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if Path(n).name.lower().startswith("nat3d_m") and n.endswith(".xlsx"))
        df = pd.read_excel(io.BytesIO(zf.read(name)), dtype=str)
    df.columns = [c.strip().upper() for c in df.columns]
    df = df[df["O_GROUP"] == "detailed"]
    out = pd.DataFrame({"soc_code": df["OCC_CODE"], "naics": df["NAICS"].str[:3],
                        "industry": df["NAICS_TITLE"].map(short_title),
                        "employment": pd.to_numeric(df["TOT_EMP"], errors="coerce"),
                        "median_wage": pd.to_numeric(df["A_MEDIAN"], errors="coerce")})
    return out.dropna(subset=["employment"])


def top_industries(table, n=8):
    """The n industries employing the most of each occupation, with each one's
    share of the occupation's wage and salary jobs (percent) and its JOLTS industry."""
    table = table.copy()
    table["share_pct"] = 100 * table["employment"] / table.groupby("soc_code")["employment"].transform("sum")
    table["jolts"] = table["naics"].map(jolts_industry)
    table = table.sort_values(["soc_code", "employment"], ascending=[True, False])
    top = table.groupby("soc_code").head(n)
    return top.assign(share_pct=top["share_pct"].round(1))[
        ["soc_code", "naics", "industry", "employment", "share_pct", "median_wage", "jolts"]]
