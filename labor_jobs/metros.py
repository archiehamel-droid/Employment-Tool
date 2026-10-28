"""Metropolitan areas: place postings in a metro area and compare them with
BLS OEWS wages and employment for that metro.

Postings are placed by county (FIPS code, or county name and state) using
the Census Bureau's metro area delineation file, or failing that by matching
"City, ST" to a metro's principal cities. OEWS publishes about 390 metropolitan statistical areas,
identified by the same CBSA codes.
"""

from pathlib import Path

import pandas as pd

from .fetch import download
from .states import STATES

DELINEATION_URL = ("https://www2.census.gov/programs-surveys/metro-micro/geographies/"
                   "reference-files/{year}/delineation-files/{name}")
METRO = "Metropolitan Statistical Area"
ABBR_TO_FIPS = {abbr: f"{fips:02d}" for abbr, _, fips in STATES}


def delineation_files(cache_dir, user_agent, year=2023):
    """list1: counties in each metro area; list2: principal cities."""
    return [download(DELINEATION_URL.format(year=year, name=f"list{n}_{year}.xlsx"),
                     Path(cache_dir) / f"list{n}_{year}.xlsx", user_agent) for n in (1, 2)]


def read_delineations(list1, list2):
    """Returns ({county FIPS or (county name, state FIPS): cbsa},
    {(city, state FIPS): cbsa}, {cbsa: title}) for metropolitan (not
    micropolitan) areas."""
    counties = pd.read_excel(list1, header=2, dtype=str)
    counties = counties[counties["Metropolitan/Micropolitan Statistical Area"] == METRO]
    cities = pd.read_excel(list2, header=2, dtype=str)
    cities = cities[cities["Metropolitan/Micropolitan Statistical Area"] == METRO]
    by_county = dict(zip(counties["FIPS State Code"] + counties["FIPS County Code"],
                         counties["CBSA Code"]))
    by_county.update(zip(zip(counties["County/County Equivalent"].str.lower(),
                             counties["FIPS State Code"]), counties["CBSA Code"]))
    by_city = dict(zip(zip(cities["Principal City Name"].str.lower(), cities["FIPS State Code"]),
                       cities["CBSA Code"]))
    titles = dict(zip(counties["CBSA Code"], counties["CBSA Title"]))
    return by_county, by_city, titles


def assign_metros(postings, by_county, by_city):
    """CBSA code per posting, NaN when it can't be placed."""
    none = pd.Series(None, index=postings.index, dtype=object)
    from_county = postings["county_fips"].map(by_county) if "county_fips" in postings else none
    city = postings["location"].str.extract(r"^\s*([^,]+?)\s*,\s*([A-Z]{2})\b")
    state_fips = city[1].map(ABBR_TO_FIPS)
    if "county_name" in postings:
        names = postings["county_name"].where(postings["county_name"].notna(), "").astype(str)
        from_name = pd.Series([by_county.get((n.lower(), s)) if n else None
                               for n, s in zip(names, state_fips)], index=postings.index)
    else:
        from_name = none
    from_city = pd.Series([by_city.get(k) for k in zip(city[0].str.lower(), state_fips)],
                          index=postings.index)
    return from_county.fillna(from_name).fillna(from_city)


def by_metro(postings, oews_metro, titles):
    placed = postings.dropna(subset=["metro"])
    df = (placed.groupby("metro")
          .agg(postings=("job_id", "size"), matched_postings=("soc_code", "count"),
               median_posted_salary=("annual_salary", "median"))
          .reset_index().rename(columns={"metro": "cbsa"}))
    df["metro_name"] = df["cbsa"].map(titles)
    totals = oews_metro[oews_metro["o_group"] == "total"][["area", "tot_emp", "a_median"]].rename(
        columns={"area": "cbsa", "tot_emp": "total_employment",
                 "a_median": "oews_median_wage_all_occupations"})
    df = df.merge(totals, on="cbsa", how="left")
    df["postings_per_1k_employed"] = (df["postings"] / df["total_employment"] * 1000).round(3)
    return df.sort_values("postings", ascending=False)


def by_metro_occupation(postings, oews_metro, titles, min_salary_samples=20):
    matched = postings.dropna(subset=["metro", "soc_code"])
    df = (matched.groupby(["metro", "soc_code"])
          .agg(postings=("job_id", "size"), salary_samples=("annual_salary", "count"),
               median_posted_salary=("annual_salary", "median"))
          .reset_index().rename(columns={"metro": "cbsa"}))
    df["metro_name"] = df["cbsa"].map(titles)
    detailed = oews_metro[oews_metro["o_group"] == "detailed"][
        ["area", "soc_code", "occ_title", "tot_emp", "a_median"]].rename(columns={"area": "cbsa"})
    df = df.merge(detailed, on=["cbsa", "soc_code"], how="left")
    gap = (df["median_posted_salary"] / df["a_median"] - 1) * 100
    df["posted_vs_oews_median_pct"] = gap.where(df["salary_samples"] >= min_salary_samples).round(1)
    return df.sort_values(["cbsa", "postings"], ascending=[True, False])
