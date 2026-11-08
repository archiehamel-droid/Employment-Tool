"""Build the data files behind the Job Market Monitor dashboard from a
main run's output:

    python -m labor_jobs.dashboard --results output/combined-2026-10 --out output/dashboard

Each file is a JSON array of rows. Summary tables feed the Overview,
Occupations and Outlook tabs; the Resume check tab, which matches a
resume in the reader's browser, gets the postings split by occupation group
(so a page downloads only the groups that fit), occupation profiles, each
occupation's task wording (for comparing a resume's duties) and its
interest, work-style and reasoning profile (for test results), the job-title
index, technology names and college programs.
"""

import argparse
import json
import math
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd

from . import (bls, h1b, industries, jolts, majors, occupation_text, personality, posting_requirements, projections,
               skills)
from .matching import build_matcher
from .soc_mapping import load_onet_titles, load_title_overrides, normalize_title


def _clean(df):
    df = df.astype(object).where(pd.notna(df), None)
    rows = df.to_dict("records")
    for row in rows:
        for key, value in row.items():
            if isinstance(value, float):
                row[key] = None if math.isnan(value) else (round(value, 3) if abs(value) < 1000 else round(value))
    return rows


def _save(out, name, rows):
    path = Path(out) / f"{name}.json"
    path.write_text(json.dumps(rows, separators=(",", ":")))
    print(f"  {name}: {len(rows):,} rows, {path.stat().st_size:,} bytes")
    return path


def results_month(results):
    """The month a results folder covers, from its name (output/combined-2026-10),
    else the current month, as YYYY-MM."""
    m = re.search(r"(\d{4}-\d{2})(?!.*\d{4}-\d{2})", Path(results).name)
    return m.group(1) if m else date.today().strftime("%Y-%m")


def summary_tables(results, out, user_agent, api_key, cache_dir="data/cache"):
    """The Overview, Occupations and Outlook tables, and the states list."""
    results = Path(results)
    cov = json.loads((results / "coverage.json").read_text())
    methods = cov["matched_by_method"]
    _save(out, "summary", [{"metric": k, "value": v} for k, v in [
        ("postings", cov["postings"]), ("with_salary", cov["with_salary"]), ("with_state", cov["with_state"]),
        ("with_metro", cov.get("with_metro")), ("matched", cov["matched_soc"]),
        ("occupations", cov["occupations_found"]), ("merged_duplicates", cov.get("merged_duplicates", 0)),
        ("gig_ads", methods.get("gig", 0)), ("salary_share", round(cov["with_salary"] / cov["postings"], 4)),
        ("matched_share", round(cov["matched_soc"] / cov["postings"], 4)),
        ("month", results_month(results))]])

    o = pd.read_csv(results / "occupations.csv", dtype={"soc_code": str})
    cols = ["soc_code", "occ_title", "postings", "tot_emp", "demand_index", "median_posted_salary", "salary_samples",
            "a_median", "h1b_median_wage", "perm_median_wage", "proj_growth_pct", "proj_annual_openings",
            "typical_education", "job_zone"]
    o = o.dropna(subset=["occ_title"]).sort_values("postings", ascending=False)
    o["median_posted_salary"] = o["median_posted_salary"].where(o["salary_samples"] >= 20)
    _save(out, "occupations", _clean(o[[c for c in cols if c in o]]))

    st = pd.read_csv(results / "states.csv")
    st = st[st.state.str.len() == 2]
    st = st.rename(columns={"area_title": "state_name", "postings_per_1k_employed": "per_1k",
                            "jolts_openings_last_month": "openings_month", "jolts_openings_last_value": "openings",
                            "jolts_openings_forecast_month": "forecast_month",
                            "jolts_openings_forecast": "openings_forecast"})
    keep = ["state", "state_name", "postings", "total_employment", "per_1k", "median_posted_salary",
            "unemployment_rate", "openings_month", "openings", "forecast_month", "openings_forecast"]
    # Feeds the Resume check tab's state filter.
    _save(out, "states", _clean(st[[c for c in keep if c in st]].sort_values("postings", ascending=False)))

    if (results / "jolts_forecast.csv").exists():
        fc = pd.read_csv(results / "jolts_forecast.csv")
        bt = pd.read_csv(results / "jolts_backtest.csv")
        ind = fc[(fc["group"] == "industry") & (fc.horizon == fc.horizon.max())].merge(
            bt[bt.horizon == bt.horizon.max()][["series_id", "model_mape", "naive_mape"]], on="series_id")
        ind["change_pct"] = ((ind.forecast / ind.last_value - 1) * 100).round(1)
        for c in ("last_value", "forecast", "lower_80", "upper_80"):
            ind[c] = (ind[c] * 1000).round()
        _save(out, "jolts_industries", _clean(
            ind[["name", "last_month", "last_value", "month", "forecast", "lower_80", "upper_80", "change_pct",
                 "model_mape", "naive_mape"]].rename(columns={"month": "forecast_month"})
            .sort_values("last_value", ascending=False)))
        sid = jolts.series_id()
        last_year = int(fc["last_month"].max()[:4])
        hist = bls.fetch_series([sid], last_year - 4, last_year, user_agent, api_key)
        hist = hist[hist.month >= f"{last_year - 3}-01"]
        nat = fc[fc.series_id == sid]
        rows = [{"month": r.month, "kind": "actual", "openings": round(r.value * 1000), "lower": None, "upper": None}
                for r in hist.itertuples()]
        rows += [{"month": r.month, "kind": "forecast", "openings": round(r.forecast * 1000),
                  "lower": round(r.lower_80 * 1000), "upper": round(r.upper_80 * 1000)} for r in nat.itertuples()]
        _save(out, "jolts_national", rows)

        # Every industry's openings history and forecast, for the Outlook tab's picker.
        industry = fc[fc["group"] == "industry"]
        ids = industry["series_id"].unique().tolist()
        history = bls.fetch_series(ids, last_year - 4, last_year, user_agent, api_key)
        history = history[history.month >= f"{last_year - 3}-01"]
        names = dict(zip(industry["series_id"], industry["name"]))
        series_rows = [{"industry": names[r.series_id], "month": r.month, "kind": "actual",
                        "openings": round(r.value * 1000), "lower": None, "upper": None}
                       for r in history.itertuples() if r.series_id in names]
        series_rows += [{"industry": r.name, "month": r.month, "kind": "forecast", "openings": round(r.forecast * 1000),
                         "lower": round(r.lower_80 * 1000), "upper": round(r.upper_80 * 1000)}
                        for r in industry.itertuples()]
        _save(out, "jolts_series", series_rows)

    # Every occupation's ten-year outlook (BLS Employment Projections), with this month's postings.
    outlook = projections.read_outlook(projections.projections_file(cache_dir, user_agent))
    every = pd.read_csv(results / "occupations.csv", dtype={"soc_code": str})
    every["median_posted_salary"] = every["median_posted_salary"].where(every["salary_samples"] >= 20)
    posted = every.set_index("soc_code")[["postings", "demand_index", "median_posted_salary"]]
    outlook = outlook.join(posted, on="soc_code")
    _save(out, "occupation_outlook", _clean(outlook))

    # Which industries employ each job (OEWS industry-specific estimates), linked to JOLTS industries.
    table = industries.read_industry_employment(industries.oews_industry_file(cache_dir, 2024, user_agent))
    _save(out, "occupation_industries", _clean(industries.top_industries(table)))

    d = pd.read_csv(results / "detailed_occupations.csv", dtype={"onet_code": str, "soc_code": str})
    sp = d[d.specialized & (d.postings >= 5)].sort_values("postings", ascending=False).head(40)
    _save(out, "specialized", _clean(sp[[c for c in ["onet_code", "onet_title", "bls_occupation", "postings", "job_zone",
                                                     "median_posted_salary", "h1b_median_wage", "perm_median_wage",
                                                     "posting_technologies"] if c in sp]]))
    _save(out, "occupation_titles", _clean(occupation_titles(results / "postings_with_soc.csv")))
    return d


PAY_IN_TITLE = re.compile(r"\$\s?[\d,.]+[kK]?(\s*(-|to)\s*\$?\s?[\d,.]+[kK]?)?(\s*(per|/|an?)\s*(hour|hr|week|wk|year|yr|month|mo))?",
                          re.I)
ID_IN_TITLE = re.compile(r"\s*(\(\s*[A-Z]*-?\d[\w-]*\s*\)|#\s?\d[\w-]*|\b(req|job|id)\s*[:#]?\s*\d[\w-]*)", re.I)


def display_title(title):
    """A posting title for display: pay amounts ("$2,084 per week") and ID numbers
    taken out, with the separators they leave behind."""
    t = ID_IN_TITLE.sub("", PAY_IN_TITLE.sub("", str(title)))
    t = re.sub(r"\s*[-–|:,/]+\s*$", "", re.sub(r"\s*[-–|]\s*[-–|]\s*", " - ", t))
    return re.sub(r"\s{2,}", " ", re.sub(r"\(\s*\)", "", t)).strip(" -–|,:")


def occupation_titles(postings_csv, per_occupation=10, min_postings=2):
    """The job titles employers post most for each occupation: titles are grouped
    once pay, IDs and locations are stripped (normalize_title), shown as their most
    common spelling, with how many postings use them and their median posted salary
    (when at least 3 state one)."""
    p = pd.read_csv(postings_csv, usecols=["title", "soc_code", "annual_salary"], dtype={"soc_code": str},
                    low_memory=False).dropna(subset=["title", "soc_code"])
    p["key"] = p["title"].map(normalize_title)
    p = p[p["key"].str.len() > 0]
    groups = p.groupby(["soc_code", "key"])
    t = groups.agg(postings=("title", "size"), salary_samples=("annual_salary", "count"),
                   median_salary=("annual_salary", "median")).reset_index()
    t["title"] = groups["title"].agg(lambda s: s.map(display_title).value_counts().index[0]).values
    t = t[t.postings >= min_postings]
    t["median_salary"] = t["median_salary"].where(t.salary_samples >= 3).round(-2)
    t = t.sort_values(["soc_code", "postings"], ascending=[True, False]).groupby("soc_code").head(per_occupation)
    t["rank"] = t.groupby("soc_code").cumcount() + 1
    return t[["soc_code", "rank", "title", "postings", "median_salary"]]


def resume_tables(results, out, detailed, cache, user_agent, onet_version, h1b_year):
    """What the Resume check tab needs to match a resume in the browser."""
    results = Path(results)
    p = pd.read_csv(results / "postings_with_soc.csv", dtype={"onet_code": str, "soc_code": str}, low_memory=False)
    p = p.dropna(subset=["onet_code", "soc_code"])
    groups = []
    for group, rows in p.groupby(p["soc_code"].str[:2]):
        compact = pd.DataFrame({
            "id": rows["job_id"].astype(str), "t": rows["title"], "c": rows["company_name"], "l": rows["location"],
            "s": rows["state"], "p": rows["annual_salary"].round(), "o": rows["onet_code"],
            "k": rows.get("technologies"), "y": rows.get("years_required"), "d": rows.get("degree_required"),
            "f": rows.get("degree_flexible"), "u": rows.get("url"), "r": rows.get("credentials_required")})
        compact["f"] = compact["f"].map(lambda v: None if pd.isna(v) else (1 if v is True or v == "True" else 0))
        _save(out, f"postings_{group}", _clean(compact))
        groups.append(group)

    profiles, technologies = skills.load_profiles(onet_version, cache, user_agent)
    edu = {k: v for k, v in majors.BLS_EDUCATION.items()}
    occ = detailed.copy()
    occ["techs"] = occ.get("posting_technologies").fillna(occ.get("onet_hot_technologies"))
    occ["techs"] = occ["techs"].fillna("").map(
        lambda s: ";".join(t.strip().rsplit(" ", 1)[0] if t.strip().endswith("%") else t.strip()
                           for t in s.split(",") if t.strip()))
    occ["edu"] = occ.get("typical_education").map(edu) if "typical_education" in occ else None
    # What employers require (ORS / O*NET) and what postings ask for.
    need_cols = [c for c in [*(f"edu_{lv}" for lv in range(6)), "exp_pct", "exp_years", "license_pct",
                             "credentials_required", "credentials_preferred", "soft_skills",
                             # pay and outlook, for ranking by them
                             "bls_median_wage", "median_posted_salary", "salary_samples", "proj_growth_pct",
                             "proj_annual_openings"] if c in occ]
    _save(out, "fit_occupations", _clean(occ[["onet_code", "soc_code", "onet_title", "postings", "techs", "edu",
                                              "typical_education", *need_cols]].rename(columns={"onet_title": "title"})))
    _save(out, "credential_patterns", posting_requirements.export_rows())

    traits = personality.blend_posting_traits(personality.occupation_traits(onet_version, cache, user_agent),
                                              detailed)
    codes = set(occ["onet_code"])
    _save(out, "occupation_traits", [
        {"o": r.onet_code, **{k.lower(): None if pd.isna(getattr(r, k)) else round(getattr(r, k), 2)
                              for k in personality.RIASEC},
         **{f"b{k.lower()}": None if pd.isna(getattr(r, f"b5_{k}")) else round(getattr(r, f"b5_{k}"), 3)
            for k in personality.BIG_FIVE},
         "rz": r.reasoning_band if isinstance(r.reasoning_band, str) else None}
        for r in traits.itertuples(index=False) if r.onet_code in codes])
    model = occupation_text.TextModel(occupation_text.occupation_documents(onet_version, cache, user_agent))
    _save(out, "occupation_text", model.export_rows(set(occ["onet_code"])))

    oews_national = bls.read_oews(bls.oews_file(cache, 2024, "nat", user_agent))
    employer = h1b.reference_titles(h1b.read_h1b(h1b.h1b_files(cache, h1b_year, user_agent)))
    matcher = build_matcher(oews_national, load_onet_titles(onet_version, cache, user_agent), profiles,
                            technologies, load_title_overrides())
    index = {**employer, **matcher.lookup}
    _save(out, "title_index", [{"k": k, "o": v} for k, v in sorted(index.items())])

    _save(out, "tech_aliases", skills.resume_aliases(technologies))

    cip_csv, crosswalk = majors.cip_files(cache, user_agent)
    mm = majors.MajorMatcher(cip_csv, crosswalk)
    keys = {}
    for key, code in mm.names:
        keys.setdefault(code, []).append(key)
    _save(out, "majors", [{"code": code, "title": title, "keys": "|".join(dict.fromkeys(keys.get(code, []))),
                           "socs": ",".join(mm.occupations_for([code]))}
                          for code, title in sorted(mm.titles.items())])
    return groups


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.dashboard", description=__doc__.split("\n\n")[0])
    p.add_argument("--results", required=True, help="output folder of a main run")
    p.add_argument("--out", default="output/dashboard")
    p.add_argument("--cache", default="data/cache")
    p.add_argument("--onet-version", default="29_3")
    p.add_argument("--h1b-year", type=int, default=2024)
    p.add_argument("--contact-email", default=os.environ.get("BLS_CONTACT_EMAIL"))
    p.add_argument("--bls-api-key", default=os.environ.get("BLS_API_KEY"))
    args = p.parse_args(argv)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    user_agent = f"labor-jobs/1.0 ({args.contact_email})" if args.contact_email else "labor-jobs/1.0"
    print("Summary tables:")
    detailed = summary_tables(args.results, args.out, user_agent, args.bls_api_key, args.cache)
    print("Resume check data:")
    groups = resume_tables(args.results, args.out, detailed, args.cache, user_agent, args.onet_version, args.h1b_year)
    print(f"Postings in {len(groups)} occupation groups: {', '.join(groups)}")


if __name__ == "__main__":
    main()
