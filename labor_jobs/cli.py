"""Command-line entry point: python -m labor_jobs --postings postings.csv"""

import argparse
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

from . import bls, h1b, history, jolts, merge, metros, projections, report, skills
from .linkedin import load_all
from .soc_mapping import TitleMatcher, is_gig, load_onet_titles, load_title_overrides


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="labor_jobs",
        description="Combine BLS employment statistics with LinkedIn job postings.")
    p.add_argument("--postings", required=True, nargs="+",
                   help="postings.csv from the Kaggle LinkedIn Job Postings dataset, or "
                        "several postings files (e.g. monthly exports)")
    p.add_argument("--out", default="output", help="output directory (default: output)")
    p.add_argument("--cache", default="data/cache", help="download cache directory")
    p.add_argument("--contact-email", default=os.environ.get("BLS_CONTACT_EMAIL"),
                   help="sent in the User-Agent; bls.gov blocks anonymous downloads "
                        "(or set BLS_CONTACT_EMAIL)")
    p.add_argument("--oews-year", type=int, default=2024,
                   help="OEWS reference year (May estimates; default 2024 to match "
                        "the 2023-24 Kaggle postings)")
    p.add_argument("--oews-state-file", help="local OEWS state file instead of downloading")
    p.add_argument("--oews-national-file", help="local OEWS national file instead of downloading")
    p.add_argument("--onet-version", default="29_3",
                   help="O*NET database version for the job title list (default 29_3)")
    p.add_argument("--onet-dir", help="local folder with O*NET 'Occupation Data.txt' "
                                      "and 'Alternate Titles.txt'")
    p.add_argument("--title-map", help="CSV of title,soc_code overrides, applied on top "
                                       "of the built-in labor_jobs/title_overrides.csv")
    p.add_argument("--fuzzy-cutoff", type=float, default=101,
                   help="enable fuzzy matching with this minimum score 0-100, e.g. 95 "
                        "(default: off; spot checks found about half its matches wrong)")
    p.add_argument("--min-salary-samples", type=int, default=20,
                   help="postings with salary needed before comparing wages (default 20)")
    p.add_argument("--bls-api-key", default=os.environ.get("BLS_API_KEY"),
                   help="BLS API registration key (or set BLS_API_KEY)")
    p.add_argument("--skip-unemployment", action="store_true",
                   help="don't fetch state unemployment rates from the BLS API")
    p.add_argument("--projections-file",
                   help="local BLS Employment Projections occupation.xlsx instead of downloading")
    p.add_argument("--skip-projections", action="store_true",
                   help="leave out the BLS ten-year employment projections")
    p.add_argument("--skip-jolts", action="store_true",
                   help="leave out the JOLTS job openings forecast")
    p.add_argument("--jolts-start-year", type=int, default=2001,
                   help="first year of JOLTS history to fit the forecast on (default 2001)")
    p.add_argument("--forecast-months", type=int, default=6,
                   help="how far ahead to forecast job openings (default 6)")
    p.add_argument("--history", default="data/history/postings.sqlite",
                   help="SQLite file that accumulates postings across runs")
    p.add_argument("--no-history", action="store_true",
                   help="don't record postings or report posting trends")
    p.add_argument("--history-feed",
                   help="name of the postings source for trends, e.g. 'linkedin' (default: "
                        "'adzuna' for files from labor_jobs.adzuna, otherwise 'linkedin')")
    p.add_argument("--h1b-year", type=int, default=2024,
                   help="federal fiscal year of H-1B wage filings (Oct-Sep; default 2024 "
                        "to match the Kaggle postings)")
    p.add_argument("--h1b-files", nargs="+",
                   help="local H-1B disclosure files instead of downloading")
    p.add_argument("--skip-h1b", action="store_true",
                   help="leave out H-1B offered wages and employer job titles")
    p.add_argument("--oews-metro-file", help="local OEWS metro file (oesm24ma.zip)")
    p.add_argument("--skip-metros", action="store_true",
                   help="leave out metro-area comparisons")
    p.add_argument("--skip-posting-skills", action="store_true",
                   help="don't scan posting descriptions for technologies (saves a minute "
                        "or two on large files)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    contact = f" ({args.contact_email})" if args.contact_email else ""
    user_agent = f"labor-jobs/1.0{contact}"
    sources = []

    print("Loading BLS OEWS employment and wages...")
    state_path = args.oews_state_file or bls.oews_file(args.cache, args.oews_year, "st", user_agent)
    nat_path = args.oews_national_file or bls.oews_file(args.cache, args.oews_year, "nat", user_agent)
    oews_state = bls.read_oews(state_path)
    oews_national = bls.read_oews(nat_path)
    sources.append(f"BLS OEWS state file: {state_path}")
    sources.append(f"BLS OEWS national file: {nat_path}")

    print("Loading LinkedIn postings...")
    postings = load_all(args.postings, descriptions=not args.skip_posting_skills)
    sources.append(f"LinkedIn postings: {', '.join(args.postings)} ({len(postings):,} rows)")

    unemployment = None
    if not args.skip_unemployment:
        print("Fetching state unemployment rates from the BLS API...")
        months = merge.posting_months(postings).dropna()
        years = {}
        if len(months):
            years = {"start_year": int(months.min()[:4]), "end_year": int(months.max()[:4])}
        try:
            unemployment = bls.fetch_unemployment(user_agent, args.bls_api_key, **years)
            if len(months):
                span = months.min() if months.min() == months.max() else f"{months.min()} to {months.max()}"
                note = f"months the postings were listed ({span}), weighted by postings per month"
            else:
                note = "latest month (postings have no dates)"
            sources.append(f"BLS LAUS unemployment rate (seasonally adjusted), {note}")
        except Exception as exc:  # the rest of the output is still useful
            print(f"  skipped: {exc}")

    print("Mapping job titles to occupations...")
    onet = load_onet_titles(args.onet_version, args.cache, user_agent, args.onet_dir)
    profiles, technologies = skills.load_profiles(args.onet_version, args.cache, user_agent,
                                                  args.onet_dir)
    version = args.onet_version.replace("_", ".")
    sources.append(f"O*NET {version} occupation, alternate and reported titles")
    if profiles is not None:
        sources.append(f"O*NET {version} Job Zones, Skills and Technology Skills")
    oews_titles = oews_national.loc[oews_national["o_group"] == "detailed", ["occ_title", "soc_code"]]
    reference = pd.concat([
        pd.DataFrame({"title": oews_titles["occ_title"], "onet_code": oews_titles["soc_code"]}),
        onet])
    overrides = load_title_overrides(*([args.title_map] if args.title_map else []))
    employment = dict(zip(oews_national["soc_code"], oews_national["tot_emp"]))
    tech_names = None
    if technologies is not None:
        tech_names = skills.title_technologies(
            technologies, pd.concat([profiles["onet_title"], oews_titles["occ_title"]]))
    names = dict(zip(profiles["onet_code"], profiles["onet_title"])) if profiles is not None else None

    filings, employer_titles = None, None
    if not args.skip_h1b:
        print("Loading H-1B wage filings...")
        paths = args.h1b_files or h1b.h1b_files(args.cache, args.h1b_year, user_agent)
        filings = h1b.read_h1b(paths)
        employer_titles = h1b.reference_titles(filings)
        sources.append(f"DOL H-1B/LCA disclosure data, fiscal year {args.h1b_year} "
                       f"({len(filings):,} certified full-time filings)")

    matcher = TitleMatcher(reference, overrides, args.fuzzy_cutoff, employment, tech_names, names,
                           employer_titles)
    postings = postings.join(matcher.map_titles(postings["title"]))
    gig = [is_gig(t, c) for t, c in zip(postings["title"], postings["company_name"])]
    postings.loc[gig, ["onet_code", "soc_code"]] = None
    postings.loc[gig, "match_method"] = "gig"

    mentions = None
    if "description" in postings and technologies is not None:
        print("Finding technologies mentioned in posting descriptions...")
        mentions = skills.technologies_in_postings(postings["description"], technologies)
        postings["technologies"] = mentions.map(lambda m: "; ".join(sorted(m)))
    postings = postings.drop(columns=["description"], errors="ignore")

    print("Combining...")
    occupations = merge.by_occupation(postings, oews_national, args.min_salary_samples)
    state_occ = merge.by_state_occupation(postings, oews_state, args.min_salary_samples)
    states = merge.by_state(postings, oews_state, unemployment)
    cov = merge.coverage(postings, oews_national)
    extras = {}

    if not args.skip_projections:
        print("Loading BLS Employment Projections...")
        path = args.projections_file or projections.projections_file(args.cache, user_agent)
        proj, period = projections.read_projections(path)
        occupations = merge.add_projections(occupations, proj)
        extras["projection_period"] = period
        sources.append(f"BLS Employment Projections {period} (Table 1.2)")

    soc_tech = skills.top_mentions(postings, mentions, "soc_code") if mentions is not None else None
    occupations = merge.add_profiles(occupations, profiles, soc_tech)
    detailed = merge.by_detailed_occupation(postings, profiles, occupations)
    if mentions is not None:
        detailed = detailed.merge(skills.top_mentions(postings, mentions, "onet_code"),
                                  left_on="onet_code", right_index=True, how="left")
    if filings is not None:
        detailed = detailed.merge(h1b.summarize(filings, "onet_code"), on="onet_code", how="left")
        occupations = occupations.merge(h1b.summarize(filings, "soc_code"), on="soc_code", how="left")
        state_occ = state_occ.merge(h1b.summarize(filings, ["state", "soc_code"]),
                                    on=["state", "soc_code"], how="left")
        extras["h1b"] = (detailed, args.h1b_year)
    detailed.to_csv(out / "detailed_occupations.csv", index=False)
    extras["detailed"] = detailed

    if not args.skip_metros:
        print("Placing postings in metro areas...")
        list1, list2 = metros.delineation_files(args.cache, user_agent)
        by_county, by_city, metro_names = metros.read_delineations(list1, list2)
        oews_metro = bls.read_oews(args.oews_metro_file or
                                   bls.oews_file(args.cache, args.oews_year, "ma", user_agent),
                                   sheet_prefix="MSA_")
        postings["metro"] = metros.assign_metros(postings, by_county, by_city)
        metro_table = metros.by_metro(postings, oews_metro, metro_names)
        metro_occ = metros.by_metro_occupation(postings, oews_metro, metro_names,
                                               args.min_salary_samples)
        metro_table.to_csv(out / "metros.csv", index=False)
        metro_occ.to_csv(out / "metro_occupations.csv", index=False)
        cov["with_metro"] = int(postings["metro"].notna().sum())
        extras["metros"] = (metro_table, metro_occ)
        sources.append(f"BLS OEWS metropolitan areas, May {args.oews_year}; Census 2023 "
                       "metro area delineations")

    if not args.skip_jolts:
        print("Fetching JOLTS job openings and forecasting...")
        try:
            catalog = jolts.series_catalog()
            openings = bls.fetch_series(catalog["series_id"].tolist(), args.jolts_start_year,
                                        date.today().year, user_agent, args.bls_api_key)
            forecasts = jolts.forecast(openings, args.forecast_months)
            errors = jolts.backtest(openings, args.forecast_months)
            summary = jolts.summarize(catalog, forecasts, errors, args.forecast_months)
            forecasts.merge(catalog, on="series_id").to_csv(out / "jolts_forecast.csv", index=False)
            errors.merge(catalog, on="series_id").to_csv(out / "jolts_backtest.csv", index=False)
            extras["jolts"] = (summary, errors, args.forecast_months)
            state_fc = summary[summary["group"] == "state"][
                ["state", "last_month", "last_value", "forecast_month", "forecast",
                 "lower_80", "upper_80"]].add_prefix("jolts_openings_").rename(
                columns={"jolts_openings_state": "state"})
            states = states.merge(state_fc, on="state", how="left")
            sources.append(f"BLS JOLTS job openings (seasonally adjusted), "
                           f"{openings['month'].min()} to {openings['month'].max()}")
        except Exception as exc:  # the rest of the output is still useful
            print(f"  skipped: {exc}")

    if not args.no_history:
        feed = args.history_feed or history.detect_feed(postings)
        added = history.record(postings, merge.posting_months(postings), args.history,
                               "; ".join(args.postings), feed)
        counts = history.monthly_counts(args.history, feed)
        trend, months = history.trends(counts)
        if len(trend):
            titles = oews_national[["soc_code", "occ_title"]]
            trend = trend.merge(titles, on="soc_code", how="left")
            trend.to_csv(out / "posting_trends.csv", index=False)
        else:
            (out / "posting_trends.csv").unlink(missing_ok=True)
        extras["history"] = (trend, months, args.history, added, feed)
        print(f"  history: {added:,} new postings recorded; "
              f"{len(months)} month(s) with enough postings to compare")

    postings.to_csv(out / "postings_with_soc.csv", index=False)
    occupations.to_csv(out / "occupations.csv", index=False)
    state_occ.to_csv(out / "state_occupations.csv", index=False)
    states.to_csv(out / "states.csv", index=False)
    merge.unmatched_titles(postings).to_csv(out / "unmatched_titles.csv", index=False)
    (out / "coverage.json").write_text(json.dumps(cov, indent=2))
    (out / "report.md").write_text(report.build_report(cov, occupations, states, sources, extras))

    n = cov["postings"]
    print(f"\n{n:,} postings, {merge.pct(cov['matched_soc'], n)} mapped to an occupation, "
          f"{merge.pct(cov['with_state'], n)} with a state.")
    print(f"Wrote {out}/: " + ", ".join(sorted(p.name for p in out.iterdir())))
