"""Bureau of Labor Statistics data.

* OEWS (Occupational Employment and Wage Statistics): employment and wages
  for every occupation (SOC code), nationally and by state. Downloaded as the
  annual flat files from https://www.bls.gov/oes/tables.htm.
* LAUS (Local Area Unemployment Statistics): the latest monthly state
  unemployment rate, fetched from the BLS public API v2.
"""

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .fetch import download
from .states import STATES

OEWS_URL = "https://www.bls.gov/oes/special-requests/oesm{yy}{scope}.zip"
API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# OEWS column -> name used in this program.
OEWS_COLUMNS = {
    "PRIM_STATE": "state",
    "AREA_TITLE": "area_title",
    "OCC_CODE": "soc_code",
    "OCC_TITLE": "occ_title",
    "O_GROUP": "o_group",
    "TOT_EMP": "tot_emp",
    "JOBS_1000": "jobs_1000",
    "LOC_QUOTIENT": "loc_quotient",
    "A_MEAN": "a_mean",
    "A_MEDIAN": "a_median",
}
NUMERIC = ["tot_emp", "jobs_1000", "loc_quotient", "a_mean", "a_median"]


def oews_file(cache_dir, year, scope, user_agent):
    """Fetch the OEWS flat file. scope is "st" (states) or "nat" (national)."""
    yy = f"{year % 100:02d}"
    url = OEWS_URL.format(yy=yy, scope=scope)
    return download(url, Path(cache_dir) / f"oesm{yy}{scope}.zip", user_agent)


def read_oews(path):
    """Read an OEWS file (.zip as published, .xlsx, or .csv) into a tidy frame.

    Keeps cross-industry rows for the all-occupations total ("total") and
    individual occupations ("detailed"). BLS marks suppressed or top-coded
    values with *, ** or #; these become NaN.
    """
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            sheets = [n for n in zf.namelist()
                      if n.lower().endswith((".xlsx", ".xls"))
                      and "desc" not in Path(n).name.lower()]
            if not sheets:
                raise ValueError(f"No spreadsheet found in {path}")
            name = next((n for n in sheets if "_dl" in n.lower()), sheets[0])
            df = pd.read_excel(io.BytesIO(zf.read(name)), dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_excel(path, dtype=str)

    df.columns = [c.strip().upper() for c in df.columns]
    if "I_GROUP" in df:
        df = df[df["I_GROUP"].str.lower() == "cross-industry"]
    if "PRIM_STATE" not in df:
        df["PRIM_STATE"] = "US"
    df = df[list(OEWS_COLUMNS)].rename(columns=OEWS_COLUMNS)
    df["o_group"] = df["o_group"].str.lower()
    df = df[df["o_group"].isin(["total", "detailed"])].copy()
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col].str.replace(",", ""), errors="coerce")
    return df.reset_index(drop=True)


def laus_series_id(fips):
    """Seasonally adjusted unemployment rate series for a state."""
    return f"LASST{fips:02d}{'0' * 11}03"


def fetch_series(series_ids, start_year, end_year, user_agent, api_key=None):
    """Monthly values for BLS time series as a frame of (series_id, month
    "YYYY-MM", value). Splits the request to fit the API's limits: with a
    registration key, 50 series and 20 years per request; without, 25 and 10.
    Register for free at https://data.bls.gov/registrationEngine/.
    """
    max_series, max_years = (50, 20) if api_key else (25, 10)
    rows = []
    for first in range(start_year, end_year + 1, max_years):
        last = min(first + max_years - 1, end_year)
        for i in range(0, len(series_ids), max_series):
            payload = {"seriesid": list(series_ids[i:i + max_series]),
                       "startyear": str(first), "endyear": str(last)}
            if api_key:
                payload["registrationkey"] = api_key
            resp = requests.post(API_URL, json=payload,
                                 headers={"User-Agent": user_agent}, timeout=60)
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError("BLS API error: " + "; ".join(body.get("message", [])))
            for series in body["Results"]["series"]:
                for p in series["data"]:
                    # M13 is the annual average; "-" marks a missing value.
                    if p["period"].startswith("M") and p["period"] != "M13" and _is_number(p["value"]):
                        rows.append({"series_id": series["seriesID"],
                                     "month": f"{p['year']}-{p['period'][1:]}",
                                     "value": float(p["value"])})
    df = pd.DataFrame(rows, columns=["series_id", "month", "value"])
    return df.drop_duplicates(["series_id", "month"]).sort_values(["series_id", "month"])


def fetch_unemployment(user_agent, api_key=None, start_year=None, end_year=None):
    """Monthly unemployment rate for each state, as a frame of (state, month
    "YYYY-MM", unemployment_rate). Defaults to last year through this year."""
    end_year = end_year or date.today().year
    start_year = start_year or end_year - 1
    ids = {laus_series_id(fips): abbr for abbr, _, fips in STATES}
    df = fetch_series(list(ids), start_year, end_year, user_agent, api_key)
    return pd.DataFrame({"state": df["series_id"].map(ids), "month": df["month"],
                         "unemployment_rate": df["value"]}).reset_index(drop=True)


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
