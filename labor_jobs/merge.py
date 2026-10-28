"""Join postings (already tagged with state and SOC code) to BLS statistics."""

import pandas as pd

OEWS_FIELDS = ["soc_code", "occ_title", "tot_emp", "a_median", "a_mean"]

# SOC occupations that BLS OEWS publishes only combined with others. O*NET
# (and so the title matching and H-1B data) keeps them separate; for joining
# BLS figures they use the combined code.
OEWS_COMBINED = {
    "21-1011": "21-1018", "21-1014": "21-1018",  # substance abuse / mental health counselors
    "13-1021": "13-1020", "13-1022": "13-1020", "13-1023": "13-1020",  # buyers, purchasing agents
    "53-1042": "53-1047", "53-1043": "53-1047", "53-1044": "53-1047",  # transport supervisors
    "29-2011": "29-2010", "29-2012": "29-2010",  # clinical lab technologists / technicians
    "51-2022": "51-2028", "51-2023": "51-2028",  # electrical and electronic assemblers
    "25-2055": "25-2052", "25-2056": "25-2052",  # special education, kindergarten / elementary
    "51-2092": "51-2090",  # team assemblers
    "31-1121": "31-1120", "31-1122": "31-1120",  # home health / personal care aides
    "13-2021": "13-2020", "13-2022": "13-2020", "13-2023": "13-2020",  # appraisers, assessors
    "25-9042": "25-9045", "25-9043": "25-9045",  # teaching assistants
}


def to_bls_soc(codes):
    """SOC codes as BLS OEWS publishes them."""
    return codes.map(lambda c: OEWS_COMBINED.get(c, c) if isinstance(c, str) else c)


def _posting_stats(postings, keys):
    return (postings.groupby(keys)
            .agg(postings=("job_id", "size"),
                 salary_samples=("annual_salary", "count"),
                 median_posted_salary=("annual_salary", "median"),
                 example_title=("title", lambda s: s.mode().iat[0]))
            .reset_index())


def _salary_gap(df, min_samples):
    """Posted median salary vs the OEWS median wage, in percent."""
    gap = (df["median_posted_salary"] / df["a_median"] - 1) * 100
    return gap.where(df["salary_samples"] >= min_samples).round(1)


def by_state_occupation(postings, oews_state, min_salary_samples=5):
    matched = postings.dropna(subset=["state", "soc_code"])
    detailed = oews_state[oews_state["o_group"] == "detailed"]
    df = _posting_stats(matched, ["state", "soc_code"]).merge(
        detailed[["state", *OEWS_FIELDS, "loc_quotient"]],
        on=["state", "soc_code"], how="left")
    df["postings_per_1k_employed"] = (df["postings"] / df["tot_emp"] * 1000).round(3)
    df["posted_vs_oews_median_pct"] = _salary_gap(df, min_salary_samples)
    return df.sort_values(["state", "postings"], ascending=[True, False])


def by_occupation(postings, oews_national, min_salary_samples=5):
    """National view. demand_index = share of postings / share of employment:
    above 1 means the occupation is over-represented in postings relative to
    how many people already work in it."""
    matched = postings.dropna(subset=["soc_code"])
    detailed = oews_national[oews_national["o_group"] == "detailed"]
    total = oews_national.loc[oews_national["o_group"] == "total", "tot_emp"]
    total_emp = total.iat[0] if len(total) else detailed["tot_emp"].sum()

    df = _posting_stats(matched, ["soc_code"]).merge(
        detailed[OEWS_FIELDS], on="soc_code", how="left")
    in_oews = df["tot_emp"].notna()
    df["share_of_postings_pct"] = (df["postings"] / df.loc[in_oews, "postings"].sum() * 100).where(in_oews)
    df["share_of_employment_pct"] = df["tot_emp"] / total_emp * 100
    df["demand_index"] = (df["share_of_postings_pct"] / df["share_of_employment_pct"]).round(2)
    df["postings_per_1k_employed"] = (df["postings"] / df["tot_emp"] * 1000).round(3)
    df["posted_vs_oews_median_pct"] = _salary_gap(df, min_salary_samples)
    for col in ("share_of_postings_pct", "share_of_employment_pct"):
        df[col] = df[col].round(3)
    return df.sort_values("postings", ascending=False)


def posting_months(postings):
    """Month ("YYYY-MM") each posting was listed, NaN when unknown."""
    if "listed_date" not in postings:
        return pd.Series(None, index=postings.index, dtype=object)
    dates = pd.to_datetime(postings["listed_date"], errors="coerce")
    return dates.dt.strftime("%Y-%m")


def history_months(postings):
    """Month to file each posting under in the history: when the postings
    were fetched, for snapshot feeds like Adzuna, otherwise when listed.
    Filing a snapshot by listing date would split it into a full current
    month and a sliver of the previous one."""
    listed = posting_months(postings)
    if "fetched_date" not in postings:
        return listed
    fetched = pd.to_datetime(postings["fetched_date"], errors="coerce").dt.strftime("%Y-%m")
    return fetched.fillna(listed)


def state_unemployment(postings, monthly):
    """Unemployment rate per state for the months the postings were listed:
    the average of the monthly rates, weighted by that state's postings per
    month. Falls back to the latest month when postings have no dates."""
    months = posting_months(postings)
    # Months BLS hasn't published yet use the latest published month.
    available = sorted(monthly["month"].unique())
    if available:
        months = months.map(lambda m: m if not isinstance(m, str) or m in available
                            else max((a for a in available if a <= m), default=None))
    weights = (postings.assign(month=months).dropna(subset=["state", "month"])
               .groupby(["state", "month"]).size().rename("weight").reset_index())
    if weights.empty:
        latest = monthly.sort_values("month").groupby("state").tail(1)
        return latest.rename(columns={"month": "unemployment_period"})

    df = weights.merge(monthly, on=["state", "month"], how="inner")
    df["weighted"] = df["unemployment_rate"] * df["weight"]
    out = df.groupby("state").agg(weighted=("weighted", "sum"), weight=("weight", "sum"),
                                  first=("month", "min"), last=("month", "max")).reset_index()
    out["unemployment_rate"] = (out["weighted"] / out["weight"]).round(2)
    out["unemployment_period"] = out["first"].where(out["first"] == out["last"],
                                                    out["first"] + " to " + out["last"])
    return out[["state", "unemployment_rate", "unemployment_period"]]


def by_detailed_occupation(postings, profiles, occupations, min_postings=1):
    """One row per O*NET occupation found in the postings, including
    specialized ones finer than BLS reports (Bioinformatics Scientists inside
    Life Scientists, All Other). BLS figures are for the parent SOC occupation."""
    matched = postings.dropna(subset=["onet_code"])
    df = (matched.groupby("onet_code")
          .agg(postings=("job_id", "size"),
               salary_samples=("annual_salary", "count"),
               median_posted_salary=("annual_salary", "median"),
               example_title=("title", lambda s: s.mode().iat[0]))
          .reset_index())
    df = df[df["postings"] >= min_postings]
    df["soc_code"] = df["onet_code"].str[:7]
    df["specialized"] = ~df["onet_code"].str.endswith(".00")
    if profiles is not None:
        df = df.merge(profiles, on="onet_code", how="left")
    parent = occupations.drop(columns=["postings", "salary_samples", "median_posted_salary",
                                       "example_title", "posting_technologies", "job_zone",
                                       "preparation", "top_skills", "onet_hot_technologies",
                                       "onet_title"], errors="ignore")
    parent = parent.rename(columns={"occ_title": "bls_occupation", "tot_emp": "bls_employment",
                                    "a_median": "bls_median_wage", "a_mean": "bls_mean_wage"})
    df = df.merge(parent, on="soc_code", how="left")
    if "onet_title" not in df:
        df["onet_title"] = None
    df["onet_title"] = df["onet_title"].fillna(df["bls_occupation"])
    return df.sort_values("postings", ascending=False)


def add_profiles(occupations, profiles, posting_tech=None):
    """O*NET profile of each SOC occupation's general (.00) O*NET code."""
    if profiles is not None:
        general = profiles.assign(soc_code=profiles["onet_code"].str[:7])
        general = general[profiles["onet_code"].str.endswith(".00")]
        occupations = occupations.merge(
            general[["soc_code", "job_zone", "preparation", "top_skills", "onet_hot_technologies"]],
            on="soc_code", how="left")
    if posting_tech is not None:
        occupations = occupations.merge(posting_tech, left_on="soc_code", right_index=True, how="left")
    return occupations


def add_projections(occupations, projections):
    """BLS ten-year outlook alongside the posting statistics."""
    return occupations.merge(projections, on="soc_code", how="left")


def by_state(postings, oews_state, unemployment=None):
    with_state = postings.dropna(subset=["state"])
    df = (with_state.groupby("state")
          .agg(postings=("job_id", "size"),
               matched_postings=("soc_code", "count"),
               median_posted_salary=("annual_salary", "median"))
          .reset_index())
    totals = (oews_state[oews_state["o_group"] == "total"]
              [["state", "area_title", "tot_emp", "a_median"]]
              .rename(columns={"tot_emp": "total_employment",
                               "a_median": "oews_median_wage_all_occupations"}))
    df = df.merge(totals, on="state", how="left")
    df["postings_per_1k_employed"] = (df["postings"] / df["total_employment"] * 1000).round(3)
    if unemployment is not None and len(unemployment):
        df = df.merge(state_unemployment(postings, unemployment), on="state", how="left")
    return df.sort_values("postings", ascending=False)


def coverage(postings, oews_national):
    """How much of the postings data made it through each join step."""
    total = len(postings)
    oews_codes = set(oews_national.loc[oews_national["o_group"] == "detailed", "soc_code"])
    methods = postings["match_method"].value_counts()
    return {
        "postings": total,
        "with_state": int(postings["state"].notna().sum()),
        "with_salary": int(postings["annual_salary"].notna().sum()),
        "matched_soc": int(postings["soc_code"].notna().sum()),
        "matched_specialized": int((postings["onet_code"].notna()
                                    & ~postings["onet_code"].fillna("").str.endswith(".00")).sum()),
        "occupations_found": int(postings["onet_code"].nunique()),
        "matched_by_method": {k: int(v) for k, v in methods.items()},
        "soc_in_oews": int(postings["soc_code"].isin(oews_codes).sum()),
    }


def unmatched_titles(postings, limit=500):
    counts = postings.loc[postings["match_method"] == "unmatched", "title"].value_counts()
    return counts.head(limit).rename_axis("title").reset_index(name="postings")


def pct(part, whole):
    return f"{part / whole * 100:.1f}%" if whole else "n/a"
