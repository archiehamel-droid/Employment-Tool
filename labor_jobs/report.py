"""Markdown summary of the combined data."""

import pandas as pd

from .merge import pct


def _fmt(value):
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        if round(value, 3) == 0:
            return "0"
        if abs(value) >= 100:
            return f"{value:,.0f}"
        return f"{value:.2f}" if abs(value) >= 1 else f"{value:.3g}"
    if isinstance(value, int):
        return f"{value:,}"
    return "" if value is None else str(value)


def table(df, columns):
    """Render selected columns of df as a Markdown table. columns: {col: header}."""
    lines = ["| " + " | ".join(columns.values()) + " |",
             "|" + "---|" * len(columns)]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_fmt(row[c]) for c in columns) + " |")
    return "\n".join(lines)


def build_report(cov, occupations, states, sources, extras=None, top=15, min_postings=50):
    extras = extras or {}
    n = cov["postings"]
    methods = ", ".join(f"{k} {v:,}" for k, v in cov["matched_by_method"].items())
    out = [
        "# BLS employment vs job postings",
        "",
        "## Sources",
        *[f"- {s}" for s in sources],
        "",
        "## Coverage",
        f"- Postings loaded: {n:,}",
        f"- With a US state: {cov['with_state']:,} ({pct(cov['with_state'], n)})",
        f"- With salary information: {cov['with_salary']:,} ({pct(cov['with_salary'], n)})",
        *([f"- Placed in a metro area: {cov['with_metro']:,} ({pct(cov['with_metro'], n)})"]
          if "with_metro" in cov else []),
        f"- Title mapped to a SOC occupation: {cov['matched_soc']:,} ({pct(cov['matched_soc'], n)}) [{methods}]",
        f"- SOC code found in OEWS: {cov['soc_in_oews']:,} ({pct(cov['soc_in_oews'], n)})",
        f"- Distinct occupations found: {cov.get('occupations_found', 0):,}, and "
        f"{cov.get('matched_specialized', 0):,} postings ({pct(cov.get('matched_specialized', 0), n)}) "
        "matched a specialized O*NET occupation finer than BLS reports",
        "",
        *([f"- Gig-app contractor ads (Uber, DoorDash and similar), counted separately: "
           f"{cov['matched_by_method']['gig']:,}"] if "gig" in cov["matched_by_method"] else []),
        "",
        "Unmapped titles are listed in `unmatched_titles.csv`. Add corrections to a "
        "CSV with `title,soc_code` columns and pass it with `--title-map`.",
        "",
        f"## Top {top} occupations by postings",
        table(occupations.head(top), {
            "soc_code": "SOC", "occ_title": "Occupation", "postings": "Postings",
            "tot_emp": "BLS employment", "demand_index": "Demand index",
            "median_posted_salary": "Posted median $", "a_median": "BLS median $",
        }),
        "",
        "Demand index = share of postings / share of national employment. "
        "Above 1 means the occupation is over-represented in these postings.",
        "",
    ]

    ranked = occupations[(occupations["postings"] >= min_postings) & occupations["demand_index"].notna()]
    if len(ranked):
        out += [
            f"## Most over-represented in postings (at least {min_postings} postings)",
            table(ranked.nlargest(top, "demand_index"), {
                "occ_title": "Occupation", "postings": "Postings",
                "tot_emp": "BLS employment", "demand_index": "Demand index"}),
            "",
            f"## Most under-represented in postings (at least {min_postings} postings)",
            table(ranked.nsmallest(top, "demand_index"), {
                "occ_title": "Occupation", "postings": "Postings",
                "tot_emp": "BLS employment", "demand_index": "Demand index"}),
            "",
        ]

    gaps = occupations.dropna(subset=["posted_vs_oews_median_pct"])
    if len(gaps):
        out += [
            "## Posted salaries vs BLS median wage (largest gaps)",
            table(gaps.reindex(gaps["posted_vs_oews_median_pct"].abs()
                               .sort_values(ascending=False).index).head(top), {
                "occ_title": "Occupation", "salary_samples": "Postings with salary",
                "median_posted_salary": "Posted median $", "a_median": "BLS median $",
                "posted_vs_oews_median_pct": "Gap %"}),
            "",
            "Postings that list a salary are not a random sample, and job sites skew "
            "toward white-collar roles, so read gaps as indicative only.",
            "",
        ]

    if "detailed" in extras:
        out += detailed_section(extras["detailed"], top)
    if "h1b" in extras:
        out += h1b_section(*extras["h1b"], top=top)
    if "metros" in extras:
        out += metro_section(*extras["metros"], top=top)
    if "projection_period" in extras:
        out += projections_section(occupations, extras["projection_period"], top, min_postings)
    if "jolts" in extras:
        out += jolts_section(*extras["jolts"])
    if "history" in extras:
        out += history_section(*extras["history"], top=top)

    state_cols = {"state": "State", "postings": "Postings",
                  "total_employment": "BLS employment",
                  "postings_per_1k_employed": "Postings per 1k employed"}
    if "unemployment_rate" in states:
        state_cols["unemployment_rate"] = "Unemployment %"
    out += [
        "## States by postings per 1,000 employed",
        table(states.dropna(subset=["postings_per_1k_employed"])
              .nlargest(top, "postings_per_1k_employed"), state_cols),
        "",
    ]
    return "\n".join(out)


def projections_section(occupations, period, top, min_postings):
    cols = {"occ_title": "Occupation", "postings": "Postings", "demand_index": "Demand index",
            "proj_growth_pct": f"Projected growth % {period}",
            "proj_annual_openings": "Projected openings / year",
            "typical_education": "Typical education"}
    with_proj = occupations.dropna(subset=["proj_growth_pct"])
    ranked = with_proj[with_proj["postings"] >= min_postings]
    return [
        f"## BLS ten-year outlook ({period})",
        "BLS Employment Projections for the occupations in the postings. Projected openings "
        "count new jobs from growth plus jobs left by people retiring or changing "
        "occupations, so a shrinking occupation can still have many openings.",
        "",
        "### Most-posted occupations",
        table(with_proj.head(top), cols),
        "",
        f"### Fastest projected growth (at least {min_postings} postings)",
        table(ranked.nlargest(top, "proj_growth_pct"), cols),
        "",
        f"### Projected to shrink (at least {min_postings} postings)",
        table(ranked.nsmallest(top, "proj_growth_pct"), cols),
        "",
    ]


def jolts_section(summary, errors, horizon):
    at_h = errors[errors["horizon"] == horizon]
    beat = (summary["model_mape"] < summary["naive_mape"])
    industries = summary[summary["group"] == "industry"]
    state_rows = summary[summary["group"] == "state"]
    cols = {"name": "Series", "last_month": "Latest month", "last_value": "Openings",
            "forecast_month": "Forecast for", "forecast": "Forecast",
            "lower_80": "80% range low", "upper_80": "80% range high", "change_pct": "Change %",
            "model_mape": "Backtest error %", "naive_mape": "No-change error %"}
    out = [
        f"## Job openings forecast, {horizon} months ahead (JOLTS)",
        "Damped-trend exponential smoothing fitted to each series' monthly history of "
        "seasonally adjusted job openings. The 80% range is where the model expects the "
        "value to land 4 times out of 5.",
        "",
        f"**Accuracy check:** the forecast was re-run from {int(at_h['tests'].max())} "
        f"earlier starting points over the last two years and compared with what actually "
        f"happened. At {horizon} months ahead its typical (median) error was "
        f"{at_h['model_mape'].median():.1f}%, against {at_h['naive_mape'].median():.1f}% for "
        f"simply assuming openings stay at their latest level. It beat that no-change "
        f"assumption for {beat[summary['group'] == 'industry'].mean():.0%} of industries "
        f"and {beat[summary['group'] == 'state'].mean():.0%} of states. Treat these as "
        "rough guides: they can't anticipate shocks like recessions, and the two-year "
        "test period had none.",
        "",
        "### By industry (national)",
        table(industries, cols),
        "",
    ]
    if len(state_rows):
        state_cols = {**cols, "name": "State"}
        out += [
            f"### States with the largest forecast change (state data ends "
            f"{state_rows['last_month'].max()})",
            table(state_rows.reindex(state_rows["change_pct"].abs()
                                     .sort_values(ascending=False).index).head(10), state_cols),
            "",
            "All states are in `states.csv`; every forecast month is in `jolts_forecast.csv`.",
            "",
        ]
    return out


def history_section(trend, months, db_path, added, feed, top):
    out = ["## Posting trends over time",
           f"Postings are recorded in `{db_path}` ({added:,} new this run). Trends compare "
           f"months of {feed} postings only. "]
    if not len(trend):
        listed = ", ".join(months) if months else "none"
        out[-1] += (f"Months with enough postings to compare so far: {listed}. Trends appear "
                    "once there are at least 3; load a new postings file each month to "
                    "build them up.")
        return out + [""]
    cols = {"occ_title": "Occupation", "postings": "Postings",
            "first_month_share_pct": f"Share % {months[0]}",
            "last_month_share_pct": f"Share % {months[-1]}",
            "trend_pct_per_month": "Trend % / month"}
    out[-1] += (f"Comparing {len(months)} months ({months[0]} to {months[-1]}) by each "
                "occupation's share of that month's postings.")
    return out + [
        "",
        "### Rising", table(trend.head(top), cols), "",
        "### Falling", table(trend.tail(top).iloc[::-1], cols), "",
    ]


def detailed_section(detailed, top, min_postings=10):
    has_tech = "posting_technologies" in detailed
    out = ["## Specialized and high-preparation occupations",
           "O*NET splits some BLS occupations into more specific roles and rates how much "
           "preparation each needs (Job Zone 1-5; 5 typically means a graduate degree or "
           "many years of experience). BLS employment and projections exist only for the "
           "broader BLS occupation, shown alongside. "
           + ("Technologies are the share of each occupation's postings whose description "
              "mentions them." if has_tech else ""),
           ""]
    tech_col = {"posting_technologies": "Technologies in postings"} if has_tech else {}

    specialized = detailed[detailed["specialized"] & (detailed["postings"] >= min_postings)]
    if len(specialized):
        out += [f"### Specialized occupations (at least {min_postings} postings)",
                table(specialized.head(top * 2), {
                    "onet_title": "Occupation", "bls_occupation": "BLS occupation",
                    "postings": "Postings", "job_zone": "Job Zone",
                    "median_posted_salary": "Posted median $", **tech_col}),
                ""]

    if "job_zone" in detailed:
        extensive = detailed[(detailed["job_zone"] == 5) & (detailed["postings"] >= min_postings)]
        if len(extensive):
            out += [f"### Extensive preparation (Job Zone 5, at least {min_postings} postings)",
                    table(extensive.head(top * 2), {
                        "onet_title": "Occupation", "postings": "Postings",
                        "median_posted_salary": "Posted median $",
                        "typical_education": "BLS typical education",
                        "proj_growth_pct": "Projected growth %",
                        "top_skills": "Top O*NET skills", **tech_col}),
                    ""]
        zones = detailed.dropna(subset=["job_zone"]).groupby("job_zone").agg(
            occupations=("onet_code", "size"), postings=("postings", "sum"),
            median_posted_salary=("median_posted_salary", "median")).reset_index()
        zones["preparation"] = zones["job_zone"].astype(int).map(
            lambda z: f"{z}: " + {1: "Little or no", 2: "Some", 3: "Medium",
                                  4: "Considerable", 5: "Extensive"}[z])
        out += ["### Postings by preparation level",
                table(zones, {"preparation": "Job Zone", "occupations": "Occupations",
                              "postings": "Postings",
                              "median_posted_salary": "Median of occupation medians $"}),
                "",
                "Every occupation with its skills and technologies is in "
                "`detailed_occupations.csv`.",
                ""]
    return out


def h1b_section(detailed, fiscal_year, top, min_filings=50):
    rows = detailed.dropna(subset=["h1b_filings"])
    rows = rows[rows["h1b_filings"] >= min_filings]
    cols = {"onet_title": "Occupation", "postings": "Postings", "h1b_filings": "H-1B filings",
            "h1b_median_wage": "H-1B median offer $", "h1b_wage_p25": "25th pct $",
            "h1b_wage_p75": "75th pct $", "median_posted_salary": "Posted median $",
            "bls_median_wage": "BLS median $", "h1b_top_employers": "Top sponsoring employers"}
    out = [f"## Offered salaries from H-1B filings (fiscal year {fiscal_year})",
           "Employers sponsoring H-1B and similar visas must file the wage they will pay for "
           "each job. These are real offers for specific roles, including specialized ones "
           "BLS doesn't report wages for, but only for sponsored jobs: mostly tech, research, "
           "finance and medicine, and at least the local prevailing wage.",
           "",
           f"### Occupations in the postings with the most filings (at least {min_filings})",
           table(rows.sort_values("h1b_filings", ascending=False).head(top), cols),
           ""]
    niche = rows[rows["specialized"]]
    if len(niche):
        out += [f"### Specialized occupations (at least {min_filings} filings)",
                table(niche.sort_values("h1b_filings", ascending=False).head(top), cols), ""]
    return out


def metro_section(metros, metro_occ, top, min_postings=200):
    big = metros[metros["postings"] >= min_postings]
    out = ["## Metro areas",
           "Postings placed in metropolitan areas by county or principal city, compared with "
           "BLS employment and wages for that metro.",
           "",
           "### Most postings",
           table(metros.head(top), {
               "metro_name": "Metro area", "postings": "Postings",
               "total_employment": "BLS employment",
               "postings_per_1k_employed": "Postings per 1k employed",
               "median_posted_salary": "Posted median $",
               "oews_median_wage_all_occupations": "BLS median $ (all jobs)"}),
           ""]
    if len(big):
        out += [f"### Most postings per 1,000 employed (at least {min_postings} postings)",
                table(big.nlargest(top, "postings_per_1k_employed"), {
                    "metro_name": "Metro area", "postings": "Postings",
                    "total_employment": "BLS employment",
                    "postings_per_1k_employed": "Postings per 1k employed"}),
                ""]
    gaps = metro_occ.dropna(subset=["posted_vs_oews_median_pct"])
    if len(gaps):
        out += ["### Posted salary vs the metro's BLS median wage, by occupation",
                table(gaps.sort_values("salary_samples", ascending=False).head(top), {
                    "metro_name": "Metro area", "occ_title": "Occupation",
                    "salary_samples": "Postings with salary",
                    "median_posted_salary": "Posted median $", "a_median": "Metro BLS median $",
                    "posted_vs_oews_median_pct": "Gap %"}),
                "",
                "Every metro and metro-occupation pair is in `metros.csv` and "
                "`metro_occupations.csv`.",
                ""]
    return out
