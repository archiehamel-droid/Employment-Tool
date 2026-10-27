"""BLS Employment Projections: ten-year outlook for each occupation.

Table 1.2 of https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm
gives, per SOC occupation, employment now and in ten years and the expected
number of openings each year (from growth plus workers retiring or leaving
the occupation). Employment and openings are published in thousands.
"""

import re
from pathlib import Path

import pandas as pd

from .fetch import download

PROJECTIONS_URL = "https://www.bls.gov/emp/ind-occ-matrix/occupation.xlsx"

# Column prefix in Table 1.2 -> name used here. The years in the headers
# change with each release, so columns are matched by prefix.
COLUMNS = {
    "Employment change, percent": "proj_growth_pct",
    "Employment change, numeric": "proj_change",
    "Occupational openings": "proj_annual_openings",
    "Typical education needed for entry": "typical_education",
}


def projections_file(cache_dir, user_agent):
    return download(PROJECTIONS_URL, Path(cache_dir) / "occupation.xlsx", user_agent)


def read_projections(path):
    """Returns (frame, period) where period is e.g. "2025-35"."""
    df = pd.read_excel(path, sheet_name="Table 1.2", header=1)
    code_col = next(c for c in df.columns if c.endswith("code"))
    type_col = next(c for c in df.columns if c.startswith("Occupation type"))
    df = df[df[type_col] == "Line item"]

    out = pd.DataFrame({"soc_code": df[code_col].astype(str).str.strip()})
    for prefix, name in COLUMNS.items():
        col = next(c for c in df.columns if c.startswith(prefix))
        out[name] = df[col]
        if prefix == "Employment change, percent":
            match = re.search(r"(\d{4})\D+(\d{2})", col)
            period = f"{match.group(1)}-{match.group(2)}" if match else "ten-year"
    for col in ("proj_growth_pct", "proj_change", "proj_annual_openings"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    # Published in thousands.
    out["proj_change"] = (out["proj_change"] * 1000).round()
    out["proj_annual_openings"] = (out["proj_annual_openings"] * 1000).round()
    return out.reset_index(drop=True), period
