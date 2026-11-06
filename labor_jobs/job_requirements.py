"""What employers require for each occupation: minimum education, prior work
experience and licenses, from two federal sources.

* BLS Occupational Requirements Survey (ORS): employers report, per job, the
  minimum education, whether prior experience is required and how much, and
  whether a license, certification or apprenticeship is required. About 250
  occupations, some as broader groups. https://www.bls.gov/ors/
* O*NET Education, Training, and Experience: the share of workers in each
  occupation who say each education level and amount of related experience
  is required to get hired. Every O*NET occupation.

ORS is used where it publishes enough of an occupation's figures (BLS
suppresses estimates from too few responses); O*NET fills in the rest.
Education uses the same 0-5 scale as majors.education_level.
"""

from pathlib import Path

import pandas as pd

from .fetch import download
from .soc_mapping import onet_file, read_onet

ORS_URL = "https://download.bls.gov/pub/time.series/or/{name}"
ORS_FILES = ("or.series", "or.data.1.AllData")
# ORS estimate codes -> what they measure.
ORS_EDUCATION = {"00075": 0, "00076": 0, "01252": 2, "01029": 3, "00079": 4, "00080": 5, "00081": 5}
ORS_FIELDS = {"00107": "exp_pct", "00111": "exp_median_days", "01290": "license_pct",
              "01293": "apprenticeship_pct", "01299": "certificate_pct"}
# O*NET Required Level of Education categories (RL 1-12) -> the 0-5 scale.
ONET_EDUCATION = {1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 3, 7: 3, 8: 4, 9: 4, 10: 5, 11: 5, 12: 5}
# O*NET Related Work Experience categories (RW 1-11) -> years, at the middle of each range.
ONET_EXPERIENCE_YEARS = {1: 0, 2: 0.04, 3: 0.17, 4: 0.375, 5: 0.75, 6: 1.5, 7: 3, 8: 5, 9: 7, 10: 9, 11: 12}
LEVEL_NAMES = {0: "high school or less", 1: "a certificate or some college", 2: "an associate's degree",
               3: "a bachelor's degree", 4: "a master's degree", 5: "a doctoral or professional degree"}
# ORS education shares count when at least this much of the total is published.
MIN_PUBLISHED_SHARE = 90


def ors_files(cache_dir, user_agent):
    paths = []
    for name in ORS_FILES:
        path = Path(cache_dir) / "ors" / name
        if not path.exists():
            download(ORS_URL.format(name=name), path, user_agent)
        paths.append(path)
    return paths


def _read_bls(path):
    df = pd.read_csv(path, sep="\t", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df.apply(lambda c: c.str.strip())


def read_ors(series_path, data_path):
    """One row per ORS occupation (soc_code like "29-1141", or a broader group
    like "15-1250"): education shares by level (edu_0 .. edu_5, percent),
    exp_pct, exp_years (median among jobs requiring experience), license_pct,
    apprenticeship_pct and certificate_pct. Suppressed figures are blank."""
    series = _read_bls(series_path)
    series = series[(series["requirement_code"] == "V") & (series["industry_code"] == "0000")
                    & (series["job_characteristic_code"] == "000") & (series["occupation_code"] != "000")
                    & series["estimate_code"].isin(set(ORS_EDUCATION) | set(ORS_FIELDS))]
    data = _read_bls(data_path)
    data = data[data["series_id"].isin(series["series_id"])]
    data = data.sort_values("year").drop_duplicates("series_id", keep="last")
    df = series.merge(data[["series_id", "value"]], on="series_id")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # "-" (suppressed) -> blank
    df["soc_code"] = df["soc_code"].str[:2] + "-" + df["soc_code"].str[2:]
    rows = {}
    for soc, group in df.groupby("soc_code"):
        values = dict(zip(group["estimate_code"], group["value"]))
        row = {f"edu_{level}": None for level in range(6)}
        published = 0.0
        for code, level in ORS_EDUCATION.items():
            v = values.get(code)
            if v is not None and not pd.isna(v):
                row[f"edu_{level}"] = (row[f"edu_{level}"] or 0) + v
                published += v
        row["edu_published"] = published
        for code, name in ORS_FIELDS.items():
            v = values.get(code)
            row[name] = None if v is None or pd.isna(v) else v
        row["exp_years"] = row.pop("exp_median_days") / 365 if row["exp_median_days"] is not None else None
        rows[soc] = row
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("soc_code").reset_index()


def read_onet_requirements(version, cache_dir, user_agent, onet_dir=None):
    """One row per O*NET occupation: education shares by level (edu_0 .. edu_5,
    percent), exp_pct (share requiring any related experience) and exp_years
    (median years among those who need some)."""
    path = onet_file("Education, Training, and Experience.txt", version, cache_dir, user_agent, onet_dir)
    if path is None:  # a local O*NET folder without this file
        return None
    df = read_onet(path)
    df = df[df["Scale ID"].isin(["RL", "RW"])].copy()
    df["Category"] = pd.to_numeric(df["Category"], errors="coerce")
    df["Data Value"] = pd.to_numeric(df["Data Value"], errors="coerce")
    rows = {}
    for code, group in df.groupby("O*NET-SOC Code"):
        row = {f"edu_{level}": 0.0 for level in range(6)}
        rl = group[group["Scale ID"] == "RL"]
        for cat, share in zip(rl["Category"], rl["Data Value"]):
            if cat in ONET_EDUCATION and not pd.isna(share):
                row[f"edu_{ONET_EDUCATION[cat]}"] += share
        rw = group[group["Scale ID"] == "RW"].dropna(subset=["Category", "Data Value"]).sort_values("Category")
        none = rw.loc[rw["Category"] == 1, "Data Value"].sum()
        some = rw[rw["Category"] > 1]
        row["exp_pct"] = 100 - none if len(rw) else None
        row["exp_years"] = None
        total = some["Data Value"].sum()
        if total > 0:
            running = 0.0
            for cat, share in zip(some["Category"], some["Data Value"]):
                running += share
                if running >= total / 2:
                    row["exp_years"] = ONET_EXPERIENCE_YEARS[int(cat)]
                    break
        rows[code] = row
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("onet_code").reset_index()


def _ok(value):
    return value is not None and not pd.isna(value)


def _ors_for(soc, ors):
    """The most specific ORS row for a SOC code: the occupation itself, else
    the broad or minor group it belongs to ("15-1252" -> "15-1250")."""
    if soc in ors:
        return ors[soc]
    digits = soc.replace("-", "")
    for keep in (5, 4, 3):
        group = digits[:keep] + "0" * (6 - keep)
        key = f"{group[:2]}-{group[2:]}"
        if key in ors:
            return ors[key]
    return None


def requirements_table(onet_codes, ors, onet):
    """Per O*NET code: education shares (edu_0..edu_5), exp_pct, exp_years,
    license_pct and which source each came from ("ORS" or "O*NET")."""
    ors_by_soc = {r["soc_code"]: r for r in ors.to_dict("records")} if ors is not None else {}
    onet_by_code = {r["onet_code"]: r for r in onet.to_dict("records")} if onet is not None else {}
    out = []
    for code in onet_codes:
        o = onet_by_code.get(code) or onet_by_code.get(code[:7] + ".00") or {}
        b = _ors_for(code[:7], ors_by_soc) or {}
        row = {"onet_code": code}
        if _ok(b.get("edu_published")) and b["edu_published"] >= MIN_PUBLISHED_SHARE:
            row.update({f"edu_{lv}": b[f"edu_{lv}"] if _ok(b.get(f"edu_{lv}")) else 0.0 for lv in range(6)})
            row["edu_source"] = "ORS"
        elif o:
            row.update({f"edu_{lv}": o[f"edu_{lv}"] for lv in range(6)})
            row["edu_source"] = "O*NET"
        if _ok(b.get("exp_pct")) and _ok(b.get("exp_years")):
            row.update(exp_pct=b["exp_pct"], exp_years=b["exp_years"], exp_source="ORS")
        elif _ok(o.get("exp_pct")):
            row.update(exp_pct=o["exp_pct"], exp_years=o["exp_years"], exp_source="O*NET")
        row["license_pct"] = b["license_pct"] if _ok(b.get("license_pct")) else None
        row["apprenticeship_pct"] = b["apprenticeship_pct"] if _ok(b.get("apprenticeship_pct")) else None
        out.append(row)
    return pd.DataFrame(out)


def load(onet_codes, version, cache_dir, user_agent, onet_dir=None):
    """requirements_table for these occupations, downloading what's needed."""
    try:
        ors = read_ors(*ors_files(cache_dir, user_agent))
    except Exception as exc:  # ORS is a refinement; O*NET alone still works
        print(f"  BLS ORS unavailable ({type(exc).__name__}); using O*NET only")
        ors = None
    onet = read_onet_requirements(version, cache_dir, user_agent, onet_dir)
    return requirements_table(onet_codes, ors, onet)


def share_requiring_more(row, level):
    """Percent of an occupation's jobs whose minimum education is above `level`,
    or None when unknown."""
    shares = [row.get(f"edu_{lv}") for lv in range(6)]
    if not any(_ok(s) for s in shares):
        return None
    total = sum(s for s in shares if _ok(s)) or 1.0
    above = sum(s for lv, s in enumerate(shares) if lv > level and _ok(s))
    return 100 * above / total


def typical_education(row):
    """The education level most of an occupation's jobs require at minimum
    (the median), or None."""
    shares = [row.get(f"edu_{lv}") if _ok(row.get(f"edu_{lv}")) else 0.0 for lv in range(6)]
    total = sum(shares)
    if not total:
        return None
    running = 0.0
    for level, share in enumerate(shares):
        running += share
        if running >= total / 2:
            return level
    return 5
