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
    """The JOLTS industry a NAICS code falls in ("1133" logging is mining and logging), or None.
    Codes ending in "G" are the state and local government part of an industry (public
    schools, public hospitals), and the Postal Service (491) is federal: JOLTS counts
    all of these as government."""
    naics = str(naics)
    if naics.endswith("G") or naics.startswith("491"):
        return "Government"
    if naics.startswith("113"):
        return "Mining and logging"
    return JOLTS_INDUSTRY.get(naics[:2])


def short_title(title):
    """Industry titles without OEWS's notes ("..., excluding ... (OEWS Designation)")."""
    title = re.sub(r"\s*\(OEWS Designation\)", "", str(title))
    if title.startswith("Federal, State, and Local Government"):
        return "Government (federal, state and local)"
    return title.strip()


# Industries OEWS splits by ownership: their state and local government part
# (OWN_CODE 2 and 3) is government in JOLTS, the private part (5) is not.
OWNED = {"611": ("Educational Services (private)", "State and local government schools"),
         "622": ("Hospitals (private)", "State and local government hospitals")}


def _sheet(zf, prefix):
    name = next(n for n in zf.namelist() if Path(n).name.lower().startswith(prefix) and n.endswith(".xlsx"))
    df = pd.read_excel(io.BytesIO(zf.read(name)), dtype=str)
    df.columns = [c.strip().upper() for c in df.columns]
    return df[df["O_GROUP"] == "detailed"]


def read_industry_employment(path):
    """Detailed occupations by 3-digit industry: soc_code, naics, industry,
    employment and median annual wage. Suppressed values are blank.

    OEWS puts public schools in Educational Services and public hospitals in
    Hospitals; JOLTS counts them as government. Where the ownership file has the
    split, each of those rows becomes a private row (same code) and a state and
    local government row (code + "G")."""
    with zipfile.ZipFile(path) as zf:
        df = _sheet(zf, "nat3d_m")
        try:
            owner = _sheet(zf, "nat3d_owner")
        except StopIteration:
            owner = None
    out = pd.DataFrame({"soc_code": df["OCC_CODE"], "naics": df["NAICS"].str[:3],
                        "industry": df["NAICS_TITLE"].map(short_title),
                        "employment": pd.to_numeric(df["TOT_EMP"], errors="coerce"),
                        "median_wage": pd.to_numeric(df["A_MEDIAN"], errors="coerce")})
    if owner is not None:
        owner = pd.DataFrame({"soc_code": owner["OCC_CODE"], "naics": owner["NAICS"].str[:3], "own": owner["OWN_CODE"],
                              "employment": pd.to_numeric(owner["TOT_EMP"], errors="coerce"),
                              "median_wage": pd.to_numeric(owner["A_MEDIAN"], errors="coerce")})
        owner = owner[owner["naics"].isin(OWNED) & owner["employment"].notna()]
        totals = out.groupby(["soc_code", "naics"])["employment"].sum(min_count=1)
        rows = []
        for (soc, naics), g in owner.groupby(["soc_code", "naics"]):
            private, public = g[g["own"] == "5"], g[g["own"].isin(["2", "3"])]
            names = OWNED[naics]
            priv_emp, pub_emp = private["employment"].sum(), public["employment"].sum()
            # Employment in a suppressed ownership part: the industry total less the parts shown.
            # It goes to the private row if that one is missing, else to the government row.
            rest = totals.get((soc, naics), float("nan")) - priv_emp - pub_emp
            if rest > 0:
                if not len(private):
                    priv_emp += rest
                else:
                    pub_emp += rest
            if priv_emp > 0:
                rows.append({"soc_code": soc, "naics": naics, "industry": names[0], "employment": priv_emp,
                             "median_wage": private["median_wage"].iloc[0] if len(private) else float("nan")})
            if pub_emp > 0:
                # No combined median for state plus local: take the larger one's.
                big = public.sort_values("employment").iloc[-1] if len(public) else None
                rows.append({"soc_code": soc, "naics": naics + "G", "industry": names[1], "employment": pub_emp,
                             "median_wage": big["median_wage"] if big is not None else float("nan")})
        if rows:
            split = pd.DataFrame(rows)
            done = set(zip(split["soc_code"], split["naics"].str[:3]))
            keep = [(s, n) not in done for s, n in zip(out["soc_code"], out["naics"])]
            out = pd.concat([out[keep], split], ignore_index=True)
    return out.dropna(subset=["employment"])


def top_industries(table, n=8, national=None):
    """The n industries employing the most of each occupation, with each one's
    share of the occupation's wage and salary jobs (percent) and its JOLTS industry.

    `national` maps occupation codes to their cross-industry OEWS employment, the
    share's denominator. Without it, the industries' own total is used, which runs
    short where BLS suppresses some industries' figures and so overstates shares."""
    table = table.copy()
    within = table.groupby("soc_code")["employment"].transform("sum")
    total = table["soc_code"].map(national) if national is not None else within
    # A national total below the industries' own (rare rounding) falls back to theirs.
    total = total.where(total.notna() & (total >= within * 0.999), within)
    table["share_pct"] = 100 * table["employment"] / total
    table["jolts"] = table["naics"].map(jolts_industry)
    table = table.sort_values(["soc_code", "employment"], ascending=[True, False])
    top = table.groupby("soc_code").head(n)
    return top.assign(share_pct=top["share_pct"].round(1))[
        ["soc_code", "naics", "industry", "employment", "share_pct", "median_wage", "jolts"]]
