"""Tests on small hand-made fixtures shaped like the real BLS, O*NET and Kaggle files."""

import json
import zipfile
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from labor_jobs import bls, cli
from labor_jobs.linkedin import annual_salary, parse_state
from labor_jobs.soc_mapping import TitleMatcher, normalize_title, title_variants

OEWS_ROWS = [
    # AREA_TITLE, PRIM_STATE, OCC_CODE, OCC_TITLE, O_GROUP, TOT_EMP, A_MEDIAN, A_MEAN
    ("California", "CA", "00-0000", "All Occupations", "total", "17,000,000", "53,000", "75,000"),
    ("California", "CA", "15-1252", "Software Developers", "detailed", "300,000", "170,000", "175,000"),
    ("California", "CA", "29-1141", "Registered Nurses", "detailed", "320,000", "137,000", "140,000"),
    ("Texas", "TX", "00-0000", "All Occupations", "total", "13,000,000", "46,000", "60,000"),
    ("Texas", "TX", "15-1252", "Software Developers", "detailed", "100,000", "#", "130,000"),
    ("Texas", "TX", "29-1141", "Registered Nurses", "detailed", "220,000", "86,000", "88,000"),
]


def oews_frame(national=False):
    df = pd.DataFrame(OEWS_ROWS, columns=["AREA_TITLE", "PRIM_STATE", "OCC_CODE", "OCC_TITLE",
                                          "O_GROUP", "TOT_EMP", "A_MEDIAN", "A_MEAN"])
    df["I_GROUP"] = "cross-industry"
    df["JOBS_1000"] = "1.0"
    df["LOC_QUOTIENT"] = "1.0"
    if national:
        df = df[df.PRIM_STATE == "CA"].assign(AREA_TITLE="U.S.").drop(columns="PRIM_STATE")
        df["TOT_EMP"] = ["150,000,000", "1,600,000", "3,200,000"]
    return df


@pytest.fixture
def files(tmp_path):
    # OEWS state file as BLS ships it: an xlsx inside a zip.
    xlsx = tmp_path / "state_M2024_dl.xlsx"
    oews_frame().to_excel(xlsx, index=False)
    state_zip = tmp_path / "oesm24st.zip"
    with zipfile.ZipFile(state_zip, "w") as zf:
        zf.write(xlsx, "oesm24st/state_M2024_dl.xlsx")
    national = tmp_path / "national.csv"
    oews_frame(national=True).to_csv(national, index=False)

    onet = tmp_path / "onet"
    onet.mkdir()
    (onet / "Occupation Data.txt").write_text(
        "O*NET-SOC Code\tTitle\tDescription\n"
        "15-1252.00\tSoftware Developers\tWrite software.\n"
        "29-1141.00\tRegistered Nurses\tCare for patients.\n")
    (onet / "Alternate Titles.txt").write_text(
        "O*NET-SOC Code\tTitle\tAlternate Title\tShort Title\tSource(s)\n"
        "15-1252.00\tSoftware Developers\tSoftware Engineer\t\t08\n"
        "15-1252.00\tSoftware Developers\tApplication Developer\t\t08\n"
        "29-1141.00\tRegistered Nurses\tStaff Nurse\t\t08\n"
        "29-1141.00\tRegistered Nurses\tICU Registered Nurse\t\t08\n")

    postings = pd.DataFrame({
        "job_id": [str(i) for i in range(7)],
        "title": ["Sr. Software Engineer - Payments", "Software Engineer II",
                  "RN - ICU (Nights)", "Registered Nurse", "Aplication Developer",
                  "Barista", "Software Engineer"],
        "company_name": ["A", "B", "C", "D", "E", "F", "G"],
        "location": ["San Francisco, CA", "Austin, TX", "Texas, United States",
                     "Los Angeles, CA", "Dallas, TX", "Seattle, WA", "United States"],
        "pay_period": ["YEARLY", "HOURLY", "HOURLY", None, None, "HOURLY", None],
        "min_salary": [180000, 60, 40, None, None, 15, None],
        "max_salary": [220000, 80, 50, None, None, 17, None],
        "currency": ["USD"] * 7,
        # San Francisco listed 2024-03-09; everything else 2024-04-18.
        "listed_time": [1709942400000.0] + [1713398400000.0] * 6,
    })
    postings_csv = tmp_path / "postings.csv"
    postings.to_csv(postings_csv, index=False)
    projections_xlsx = tmp_path / "occupation.xlsx"
    write_projections(projections_xlsx)
    return {"state": state_zip, "national": national, "onet": onet, "postings": postings_csv,
            "projections": projections_xlsx, "out": tmp_path / "out"}


def write_projections(path):
    """Table 1.2 layout: a title row, then headers whose years change each release."""
    header = ["2025 National Employment Matrix title", "2025 National Employment Matrix code",
              "Occupation type", "Employment, 2025", "Employment, 2035",
              "Employment change, numeric, 2025\u201335", "Employment change, percent, 2025\u201335",
              "Occupational openings, 2025\u201335 annual average",
              "Typical education needed for entry"]
    rows = [["Table 1.2 Occupational projections"] + [None] * 8, header,
            ["Total, all occupations", "00-0000", "Summary", 170280.8, 176198.5, 5917.6, 3.5, 17461.8, "\u2014"],
            ["Software developers", "15-1252", "Line item", 1700.0, 1874.7, 174.7, 10.2, 95.3, "Bachelor's degree"],
            ["Registered nurses", "29-1141", "Line item", 3480.0, 3674.7, 194.7, 5.6, 180.8, "Bachelor's degree"]]
    with pd.ExcelWriter(path) as xl:
        pd.DataFrame([["index"]]).to_excel(xl, sheet_name="Index", header=False, index=False)
        pd.DataFrame(rows).to_excel(xl, sheet_name="Table 1.2", header=False, index=False)


def test_read_oews_from_zip_handles_commas_and_suppressed_values(files):
    df = bls.read_oews(files["state"])
    tx_dev = df[(df.state == "TX") & (df.soc_code == "15-1252")].iloc[0]
    assert tx_dev.tot_emp == 100000
    assert pd.isna(tx_dev.a_median)  # "#" = top-coded wage


def test_normalize_title():
    assert normalize_title("Sr. Software Engineer") == "software engineer"
    assert normalize_title("Registered Nurses") == normalize_title("Registered Nurse")
    assert normalize_title("Secretaries") == "secretary"
    assert title_variants("Software Engineer - Payments (Remote)") == [
        "software engineer payment", "software engineer"]


def test_parse_state():
    assert parse_state("Austin, TX") == "TX"
    assert parse_state("Texas, United States") == "TX"
    assert parse_state("United States") is None
    assert parse_state("United States", "6037") == "CA"  # county FIPS


def test_annual_salary_annualizes_and_prefers_normalized():
    df = pd.DataFrame({"pay_period": ["HOURLY", "YEARLY", "YEARLY"],
                       "min_salary": [50, 100000, 100000], "max_salary": [50, 120000, 120000],
                       "normalized_salary": [None, None, 125000]})
    assert annual_salary(df).tolist() == [104000, 110000, 125000]


def test_matcher_methods():
    ref = pd.DataFrame({"title": ["Software Developers", "Software Engineer"],
                        "soc_code": ["15-1252", "15-1252"]})
    m = TitleMatcher(ref, overrides={"barista": "35-3023"}, fuzzy_cutoff=90)
    got = m.map_titles(pd.Series(["Sofware Engineer", "Barista", "Astronaut",
                                  "Sr Software Engineer", "Embedded Software Engineer",
                                  "Software Trainee", "Assistant Engineer"]))
    assert got.match_method.tolist() == ["fuzzy", "override", "unmatched", "exact",
                                         "partial", "unmatched", "unmatched"]
    assert got.soc_code.tolist()[:2] == ["15-1252", "35-3023"]


def test_end_to_end(files):
    fake, empty = mock.Mock(), mock.Mock()
    empty.json.return_value = {"status": "REQUEST_SUCCEEDED", "Results": {"series": []}}
    fake.json.return_value = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": "LASST060000000000003", "data": [
            {"year": "2024", "period": "M13", "periodName": "Annual", "value": "9.9"},
            {"year": "2024", "period": "M05", "periodName": "May", "value": "8.0"},
            {"year": "2024", "period": "M04", "periodName": "April", "value": "5.6"},
            {"year": "2024", "period": "M03", "periodName": "March", "value": "5.0"}]},
        {"seriesID": "LASST480000000000003", "data": [
            {"year": "2024", "period": "M04", "periodName": "April", "value": "4.0"},
            {"year": "2024", "period": "M03", "periodName": "March", "value": "-"}]},
    ]}}
    with mock.patch("labor_jobs.bls.requests.post", side_effect=[fake, empty, empty]) as post:
        cli.main(["--postings", str(files["postings"]), "--out", str(files["out"]),
                  "--oews-state-file", str(files["state"]),
                  "--oews-national-file", str(files["national"]),
                  "--onet-dir", str(files["onet"]),
                  "--cache", str(files["out"] / "cache"),
                  "--history", str(files["out"] / "history.sqlite"),
                  "--projections-file", str(files["projections"]),
                  "--skip-jolts", "--skip-h1b", "--skip-metros", "--skip-perm"])
    assert post.call_count == 3  # 51 series in batches of 25
    payload = post.call_args.kwargs["json"]
    assert (payload["startyear"], payload["endyear"]) == ("2024", "2024")

    out = files["out"]
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["postings"] == 7
    assert cov["with_state"] == 6
    # Fuzzy matching is off by default, so the misspelled "Aplication Developer"
    # stays unmatched along with "Barista".
    assert cov["matched_soc"] == 5
    assert "fuzzy" not in cov["matched_by_method"]

    occ = pd.read_csv(out / "occupations.csv", dtype={"soc_code": str}).set_index("soc_code")
    assert occ.loc["15-1252", "postings"] == 3
    # 3/5 of postings vs 1.6M/150M of employment
    assert occ.loc["15-1252", "demand_index"] == pytest.approx((3 / 5) / (1.6 / 150), rel=1e-2)

    so = pd.read_csv(out / "state_occupations.csv").set_index(["state", "soc_code"])
    assert so.loc[("TX", "29-1141"), "median_posted_salary"] == 93600  # $45/h * 2080
    assert so.loc[("TX", "29-1141"), "tot_emp"] == 220000

    states = pd.read_csv(out / "states.csv").set_index("state")
    # CA: one posting in March (5.0) and one in April (5.6); May and the annual
    # average are outside the posting months.
    assert states.loc["CA", "unemployment_rate"] == 5.3
    assert states.loc["CA", "unemployment_period"] == "2024-03 to 2024-04"
    assert states.loc["TX", "unemployment_rate"] == 4.0
    assert states.loc["TX", "unemployment_period"] == "2024-04"
    assert states.loc["WA", "postings"] == 1 and pd.isna(states.loc["WA", "total_employment"])

    assert occ.loc["29-1141", "proj_annual_openings"] == 180800  # published in thousands
    assert occ.loc["15-1252", "proj_growth_pct"] == 10.2
    report_text = (out / "report.md").read_text()
    assert "BLS ten-year outlook (2025-35)" in report_text
    assert "Trends appear once there are at least 3" in report_text  # only 2 months of postings

    unmatched = pd.read_csv(out / "unmatched_titles.csv")
    assert sorted(unmatched.title) == ["Aplication Developer", "Barista"]
    assert "Top 15 occupations by postings" in (out / "report.md").read_text()


def test_builtin_overrides_apply_to_variants():
    from labor_jobs.soc_mapping import load_title_overrides
    ref = pd.DataFrame({"title": ["Architectural and Engineering Managers"], "soc_code": ["11-9041"]})
    m = TitleMatcher(ref, overrides=load_title_overrides())
    got = m.map_titles(pd.Series(["Senior Project Manager", "IT Project Manager",
                                  "Operations Specialist", "Fleet Operations Specialist"]))
    assert got.soc_code.tolist()[:2] == ["13-1082", "13-1082"]
    assert got.soc_code.isna().tolist()[2:] == [True, True]
    assert got.match_method.tolist() == ["override", "partial", "excluded", "unmatched"]


def test_state_unemployment_falls_back_to_latest_month_without_dates():
    from labor_jobs.merge import state_unemployment
    postings = pd.DataFrame({"state": ["CA"], "job_id": ["1"]})
    monthly = pd.DataFrame({"state": ["CA", "CA"], "month": ["2026-08", "2026-09"],
                            "unemployment_rate": [5.5, 5.4]})
    got = state_unemployment(postings, monthly).set_index("state")
    assert got.loc["CA", "unemployment_rate"] == 5.4
    assert got.loc["CA", "unemployment_period"] == "2026-09"


def monthly_series(n=60, start="2020-01"):
    months = pd.period_range(start, periods=n, freq="M").astype(str)
    # Smooth upward trend with a little seasonality-free noise.
    rng = np.random.default_rng(0)
    values = 1000 * np.exp(0.01 * np.arange(n) + rng.normal(0, 0.01, n))
    return pd.DataFrame({"series_id": "JTS000000000000000JOL", "month": months, "value": values})


def test_jolts_forecast_and_backtest():
    from labor_jobs import jolts
    hist = monthly_series()
    fc = jolts.forecast(hist, horizon=6)
    assert fc["month"].tolist() == ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]
    assert (fc["lower_80"] < fc["forecast"]).all() and (fc["forecast"] < fc["upper_80"]).all()
    assert fc["forecast"].iat[-1] > hist["value"].iat[-1]  # follows the upward trend

    err = jolts.backtest(hist, horizon=6, origins=4, step=2)
    assert set(err["horizon"]) == set(range(1, 7)) and (err["tests"] == 4).all()
    # On a steady trend the model should beat assuming no change.
    assert (err["model_mape"] < err["naive_mape"]).all()

    summary = jolts.summarize(jolts.series_catalog(), fc, err, 6)
    row = summary.iloc[0]
    assert row["name"] == "Total nonfarm" and row["last_value"] == round(hist["value"].iat[-1] * 1000)


def test_jolts_series_ids():
    from labor_jobs import jolts
    assert jolts.series_id() == "JTS000000000000000JOL"
    assert jolts.series_id(state_fips=6) == "JTS000000060000000JOL"
    assert jolts.series_id("540099") == "JTS540099000000000JOL"
    assert len(jolts.series_catalog()) == len(jolts.INDUSTRIES) + 51


def test_history_trends(tmp_path):
    from labor_jobs import history
    db = tmp_path / "h.sqlite"
    rows = []
    # Nurses grow from 10% to 30% of postings over four months; developers shrink.
    for i, (month, nurses) in enumerate([("2024-01", 60), ("2024-02", 100),
                                         ("2024-03", 140), ("2024-04", 180)]):
        socs = ["29-1141"] * nurses + ["15-1252"] * (300 - nurses) + [None] * 300
        rows += [{"job_id": f"{month}-{k}", "soc_code": s, "state": "CA", "month": month}
                 for k, s in enumerate(socs)]
    postings = pd.DataFrame(rows)
    assert history.record(postings.drop(columns="month"), postings["month"], db, "a.csv",
                          "linkedin") == 2400
    # Loading the same postings again adds nothing.
    assert history.record(postings.drop(columns="month"), postings["month"], db, "a.csv",
                          "linkedin") == 0
    # A month from another job site doesn't join the trend.
    other = pd.DataFrame({"job_id": [f"adzuna-{k}" for k in range(600)], "soc_code": "15-1252",
                          "state": "CA"})
    assert history.detect_feed(other) == "adzuna" and history.detect_feed(postings) == "linkedin"
    history.record(other, pd.Series("2024-05", index=other.index), db, "b.csv", "adzuna")
    assert history.covered_months(history.monthly_counts(db, "adzuna")) == ["2024-05"]

    trend, months = history.trends(history.monthly_counts(db, "linkedin"))
    assert months == ["2024-01", "2024-02", "2024-03", "2024-04"]
    trend = trend.set_index("soc_code")
    assert trend.loc["29-1141", "first_month_share_pct"] == 10.0
    assert trend.loc["29-1141", "last_month_share_pct"] == 30.0
    assert trend.loc["29-1141", "trend_pct_per_month"] > 0 > trend.loc["15-1252", "trend_pct_per_month"]

    # Too few months: no trend yet.
    early = history.monthly_counts(db, "linkedin").query("month < '2024-03'")
    assert history.trends(early)[0].empty


def test_postings_without_job_id_get_stable_ids(tmp_path):
    from labor_jobs.linkedin import load_all
    csv = tmp_path / "export.csv"
    pd.DataFrame({"title": ["Nurse", "Nurse"], "location": ["Austin, TX", "Austin, TX"],
                  "posted_date": ["2024-05-01", "2024-05-02"]}).to_csv(csv, index=False)
    first, again = load_all([csv]), load_all([csv, csv])
    assert first["job_id"].tolist() == again["job_id"].tolist()  # duplicates dropped
    assert first["job_id"].nunique() == 2
    assert str(first["listed_date"].iat[0]) == "2024-05-01"


def test_detailed_occupations_and_new_tiers():
    ref = pd.DataFrame([
        ("Software Developers", "15-1252.00"), ("Software Engineer", "15-1252.00"),
        ("Bioinformatics Scientists", "19-1029.01"), ("Bioinformatics Scientist", "19-1029.01"),
        ("Solar Sales Representatives and Assessors", "41-4011.07"),
        ("Sales Representative", "41-4011.07"), ("Sales Representative", "41-4011.00"),
        ("Wind Energy Operations Managers", "11-9199.09"), ("Site Manager", "11-9199.09"),
        ("Occupational Therapists", "29-1122.00"),
        ("AI Engineer (Artificial Intelligence Engineer)", "15-1221.00"),
    ], columns=["title", "onet_code"])
    names = {"19-1029.01": "Bioinformatics Scientists",
             "41-4011.07": "Solar Sales Representatives and Assessors",
             "11-9199.09": "Wind Energy Operations Managers"}
    m = TitleMatcher(ref, technologies={"servicenow", "power bi", "python", "ruby on rail"},
                     occupation_names=names)
    titles = ["Senior Bioinformatics Scientist", "Sales Representative", "Site Manager",
              "Artificial Intelligence Engineer", "Home Care Occupational Therapist OT",
              "ServiceNow Developer", "Python Power BI Developer", "Ruby on Rails Engineer",
              "Marketing Developer"]
    got = m.map_titles(pd.Series(titles)).set_index(pd.Index(titles))
    # Niche occupation keeps its O*NET code; BLS joins on the SOC prefix.
    assert got.loc["Senior Bioinformatics Scientist", ["onet_code", "soc_code"]].tolist() == \
        ["19-1029.01", "19-1029"]
    # Generic titles listed by a specialized occupation stay general.
    assert got.loc["Sales Representative", "onet_code"] == "41-4011.00"
    assert got.loc["Site Manager", "onet_code"] == "11-9199.00"
    # Spelled-out form in brackets is a title of its own.
    assert got.loc["Artificial Intelligence Engineer", "onet_code"] == "15-1221.00"
    assert got.loc["Home Care Occupational Therapist OT", ["onet_code", "match_method"]].tolist() == \
        ["29-1122.00", "partial"]
    for title in ("ServiceNow Developer", "Python Power BI Developer", "Ruby on Rails Engineer"):
        assert got.loc[title, ["onet_code", "match_method"]].tolist() == ["15-1252.00", "technology"]
    assert got.loc["Marketing Developer", "match_method"] == "unmatched"


def test_technology_names_and_mentions():
    from labor_jobs import skills
    assert skills.display_name("Amazon Web Services AWS software") == "AWS"
    assert skills.display_name("Microsoft Power BI") == "Power BI"
    assert skills.display_name("Microsoft Word") == "Microsoft Word"  # "Word" alone is ambiguous
    assert skills.display_name("Hypertext markup language HTML") == "HTML"
    assert "Word" not in skills.aliases("Microsoft Word")

    tech = pd.DataFrame({"example": ["Python", "Microsoft Power BI", "Structured query language SQL",
                                     "Microsoft Word", "C++", "Facebook", "Obscure Tool"],
                         "hot": [True, True, True, True, True, True, False]})
    text = pd.Series([
        "Must know Python, SQL and Power BI. A word of advice: follow us on Facebook.",
        "C++ and Microsoft Word; Pythonic code is not a skill name",
        None,
    ])
    found = skills.technologies_in_postings(text, tech)
    assert found.tolist() == [{"Python", "SQL", "Power BI"}, {"C++", "Microsoft Word"}, set()]

    postings = pd.DataFrame({"soc_code": ["15-1252", "15-1252", "29-1141"]})
    top = skills.top_mentions(postings, found, "soc_code", min_share=0.5)
    assert top.loc["15-1252"] == "C++ 50%, Microsoft Word 50%, Power BI 50%, Python 50%, SQL 50%"
    assert "29-1141" not in top.index

    names = skills.title_technologies(tech, ["Software Developers", "Word Processors"])
    assert {"python", "power bi", "sql"} <= names
    assert "word" not in names


def test_h1b_wages_and_employer_titles(tmp_path):
    from labor_jobs import h1b
    rows = []
    for i, (title, code, employer, wage, unit, level) in enumerate([
        ("Bioinformatics Scientist", "19-1029.01", "A LABS", "120,000", "Year", "II"),
        ("Bioinformatics Scientist II", "19-1029.01", "B BIO", "130000", "Year", "III"),
        ("Bioinformatics Scientist", "19-1029.01", "C GENE", "60", "Hour", "IV"),
        ("Bioinformatics Scientist", "19-1029.00", "D INC", "110000", "Year", "I"),
        ("Data Engineer JC60", "15-1243.00", "A LABS", "150000", "Year", "II"),
        ("Data Engineer", "15-1243.00", "B BIO", "140000", "Year", "II"),
        ("Data Engineer - 2 - US", "15-1243.00", "C GENE", "145000", "Year", "II"),
        ("Data Engineer", "15-1252.00", "D INC", "135000", "Year", "II"),
        ("Data Engineer", "15-1243.00", "D INC", "1", "Year", "II"),  # implausible wage
    ]):
        rows.append({"CASE_NUMBER": f"I-{i}", "CASE_STATUS": "Certified", "DECISION_DATE": "2024-05-01",
                     "VISA_CLASS": "H-1B", "JOB_TITLE": title, "SOC_CODE": code,
                     "FULL_TIME_POSITION": "Y", "TOTAL_WORKER_POSITIONS": "1",
                     "EMPLOYER_NAME": employer, "NAICS_CODE": "541511", "WORKSITE_CITY": "Boston",
                     "WORKSITE_STATE": "MA", "WAGE_RATE_OF_PAY_FROM": wage,
                     "WAGE_UNIT_OF_PAY": unit, "PW_WAGE_LEVEL": level})
    rows.append({**rows[0], "CASE_NUMBER": "I-denied", "CASE_STATUS": "Denied"})
    rows.append({**rows[0], "CASE_NUMBER": "I-part", "FULL_TIME_POSITION": "N"})
    path = tmp_path / "lca.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    filings = h1b.read_h1b(path)
    assert len(filings) == 8  # denied, part-time and the $1 wage dropped
    assert filings.loc[filings.case_number == "I-2", "annual_wage"].iat[0] == 60 * 2080

    by_code = h1b.summarize(filings, "onet_code").set_index("onet_code")
    assert by_code.loc["19-1029.01", "h1b_filings"] == 3
    assert by_code.loc["19-1029.01", "h1b_median_wage"] == 124800
    assert by_code.loc["19-1029.01", "h1b_level_iv_pct"] == 33.3
    assert by_code.loc["15-1243.00", "h1b_top_employers"].startswith("A Labs")

    refs = h1b.reference_titles(filings)
    # Codes and "- 2 - US" suffixes are stripped; 3 of 4 employers agree.
    assert refs[normalize_title("Data Engineer")] == "15-1243.00"
    assert refs[normalize_title("Bioinformatics Scientist")] == "19-1029.01"


def test_metro_placement(tmp_path):
    from labor_jobs import metros
    header = ["CBSA Code", "Metropolitan Division Code", "CSA Code", "CBSA Title",
              "Metropolitan/Micropolitan Statistical Area", "Metropolitan Division Title",
              "CSA Title", "County/County Equivalent", "State Name", "FIPS State Code",
              "FIPS County Code", "Central/Outlying County"]
    list1 = [["title"] + [None] * 11, [None] * 12, header,
             ["12420", None, None, "Austin-Round Rock-San Marcos, TX", "Metropolitan Statistical Area",
              None, None, "Travis County", "Texas", "48", "453", "Central"],
             ["99999", None, None, "Smalltown, TX", "Micropolitan Statistical Area",
              None, None, "Small County", "Texas", "48", "001", "Central"]]
    list2 = [["title"] + [None] * 5, [None] * 6,
             ["CBSA Code", "CBSA Title", "Metropolitan/Micropolitan Statistical Area",
              "Principal City Name", "FIPS State Code", "FIPS Place Code"],
             ["12420", "Austin-Round Rock-San Marcos, TX", "Metropolitan Statistical Area",
              "Round Rock", "48", "63500"]]
    for name, rows in (("l1.xlsx", list1), ("l2.xlsx", list2)):
        pd.DataFrame(rows).to_excel(tmp_path / name, header=False, index=False)
    by_county, by_city, titles = metros.read_delineations(tmp_path / "l1.xlsx", tmp_path / "l2.xlsx")
    # Micropolitan areas left out.
    assert by_county == {"48453": "12420", ("travis county", "48"): "12420"}

    postings = pd.DataFrame({
        "job_id": ["1", "2", "3", "4", "5"], "soc_code": ["15-1252", "15-1252", None, "15-1252", None],
        "annual_salary": [150000, 130000, None, 90000, None],
        "location": ["Austin, TX", "Round Rock, TX", "Smalltown, TX", "Remote", "Pflugerville, TX"],
        "county_fips": ["48453", None, "48001", None, None],
        "county_name": [None, None, None, None, "Travis County"]})
    postings["metro"] = metros.assign_metros(postings, by_county, by_city)
    assert postings["metro"].tolist()[:2] == ["12420", "12420"]
    assert postings["metro"].isna().tolist()[2:4] == [True, True]
    assert postings["metro"].iat[4] == "12420"  # placed by county name (as Adzuna gives it)
    postings = postings.iloc[:4]

    oews = pd.DataFrame({"area": ["12420", "12420"], "soc_code": ["00-0000", "15-1252"],
                         "occ_title": ["All", "Software Developers"], "o_group": ["total", "detailed"],
                         "tot_emp": [1_300_000, 40_000], "a_median": [55000, 140000]})
    table = metros.by_metro(postings, oews, titles).set_index("cbsa")
    assert table.loc["12420", "postings"] == 2
    assert table.loc["12420", "metro_name"] == "Austin-Round Rock-San Marcos, TX"
    occ = metros.by_metro_occupation(postings, oews, titles, min_salary_samples=2)
    assert occ.iloc[0]["posted_vs_oews_median_pct"] == 0.0  # median 140,000 vs 140,000


def test_adzuna_rows_and_paging(tmp_path):
    from labor_jobs import adzuna
    from labor_jobs.linkedin import load_postings

    def result(i, predicted="0"):
        return {"id": str(i), "title": "<strong>Data</strong> Scientist", "created": "2026-10-20T09:00:00Z",
                "location": {"display_name": "Austin, Travis County",
                             "area": ["US", "Texas", "Travis County", "Austin"]},
                "company": {"display_name": "Acme"}, "salary_min": 120000, "salary_max": 140000,
                "salary_is_predicted": predicted, "description": "Python and SQL",
                "category": {"label": "IT Jobs"}, "contract_time": "full_time"}

    pages = [[result(i) for i in range(50)], [result(50, predicted="1")]]
    session = mock.Mock()
    session.get.side_effect = [mock.Mock(status_code=200, json=mock.Mock(return_value={"results": p}),
                                         raise_for_status=mock.Mock()) for p in pages]
    rows = list(adzuna.fetch("id", "key", pages=5, session=session, pause=0))
    assert session.get.call_count == 2  # stopped when a page came back short
    assert rows[0]["title"] == "Data Scientist" and rows[0]["location"] == "Austin, TX"
    assert rows[0]["county"] == "Travis County"
    assert rows[-1]["min_salary"] is None  # Adzuna's estimated salary dropped

    out = tmp_path / "adzuna.csv"
    pd.DataFrame(rows, columns=adzuna.FIELDS).to_csv(out, index=False)
    loaded = load_postings(out)
    assert loaded["state"].iat[0] == "TX"
    assert loaded["county_name"].iat[0] == "Travis County"
    assert loaded["annual_salary"].iat[0] == 130000
    assert str(loaded["listed_date"].iat[0]) == "2026-10-20"


def test_adzuna_sample_spreads_pages_across_categories_and_states():
    from labor_jobs import adzuna
    assert adzuna.allocate({"it": 600, "sales": 300, "tiny": 1, "empty": 0}, 10) == \
        {"it": 6, "sales": 3, "tiny": 1}

    def response(body):
        return mock.Mock(status_code=200, json=mock.Mock(return_value=body), raise_for_status=mock.Mock())

    calls = []

    def get(url, params, timeout):
        if url.endswith("/categories"):
            return response({"results": [{"tag": "it"}, {"tag": "sales"}]})
        if params["results_per_page"] == 1:
            return response({"count": {"it": 3_000_000, "sales": 1_000_000}[params["category"]]})
        calls.append(params | {"page": int(url.rsplit("/", 1)[1])})
        key = f"{params['category']}-{params['where']}-{calls[-1]['page']}"
        return response({"results": [{"id": f"{key}-{i}", "title": "Job",
                                      "location": {"area": ["US", "Ohio", "Franklin County", "Columbus"]}}
                                     for i in range(50)]})

    session = mock.Mock()
    session.get.side_effect = get
    rows = list(adzuna.sample("id", "key", pages=8, session=session, pause=0, seed=1))
    assert len(rows) == 400
    assert [c["category"] for c in calls].count("it") == 6  # 3:1 like the category sizes
    assert all(c["where"] and "sort_by" not in c and 1 <= c["page"] <= adzuna.MAX_PAGE for c in calls)
    # Larger states are picked more often, but several are covered.
    assert len({c["where"] for c in calls}) >= 3


def test_adzuna_retries_server_errors_without_leaking_key(monkeypatch):
    from labor_jobs import adzuna
    monkeypatch.setattr(adzuna.time, "sleep", lambda s: None)
    ok = mock.Mock(status_code=200, json=mock.Mock(return_value={"results": []}))
    flaky = mock.Mock()
    flaky.get.side_effect = [mock.Mock(status_code=503), adzuna.requests.Timeout("secret-key"), ok]
    assert adzuna._get(flaky, "https://x/?app_key=secret-key", {}) == {"results": []}

    down = mock.Mock()
    down.get.return_value = mock.Mock(status_code=503)
    with pytest.raises(adzuna.PageFailed) as err:
        adzuna._get(down, "https://x/?app_key=secret-key", {"app_key": "secret-key"})
    assert "secret-key" not in str(err.value) and "4 tries" in str(err.value)

    limited = mock.Mock()
    limited.get.return_value = mock.Mock(status_code=429)
    with pytest.raises(RuntimeError, match="rate limit"):
        adzuna._get(limited, "https://x/", {})


def test_history_files_from_before_feeds_are_upgraded(tmp_path):
    import sqlite3
    from labor_jobs import history
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE postings (job_id TEXT PRIMARY KEY, month TEXT NOT NULL, "
                    "soc_code TEXT, state TEXT, source TEXT)")
        con.executemany("INSERT INTO postings VALUES (?, ?, ?, ?, ?)",
                        [("123", "2024-04", "15-1252", "CA", "postings.csv"),
                         ("adzuna-9", "2026-10", "15-1252", "CA", "adzuna.csv")])
    assert history.monthly_counts(db, "linkedin")["month"].tolist() == ["2024-04"]
    assert history.monthly_counts(db, "adzuna")["month"].tolist() == ["2026-10"]


def test_truck_driver_keyword_and_gig_ads():
    from labor_jobs.soc_mapping import is_gig
    ref = pd.DataFrame({"title": ["Software Developers"], "onet_code": ["15-1252.00"]})
    got = TitleMatcher(ref).map_titles(pd.Series([
        "Class A CDL Company Driver - 1-5mo EXP Required - OTR - Dry Van - $1.2k per week",
        "Local Truck Driver - Home Daily", "Screwdriver Assembler"]))
    assert got["onet_code"].tolist()[:2] == ["53-3032.00", "53-3032.00"]
    assert got["match_method"].tolist() == ["keyword", "keyword", "unmatched"]

    assert is_gig("Drive with DoorDash - No CDL license needed", "DoorDash")
    assert is_gig("Repartidor (de tiempo parcial o completo) con DoorDash", "DoorDash")
    assert not is_gig("Senior Software Engineer, Maps", "Uber")  # a real Uber job
    assert not is_gig("Delivery Driver", "Sysco")


def test_snapshot_feeds_file_history_under_fetch_month():
    from labor_jobs.merge import history_months
    postings = pd.DataFrame({"listed_date": ["2026-09-28", "2026-10-20", "2024-04-02"],
                             "fetched_date": ["2026-10-28", "2026-10-28", None]})
    assert history_months(postings).tolist() == ["2026-10", "2026-10", "2024-04"]
    assert history_months(postings.drop(columns="fetched_date")).tolist() == \
        ["2026-09", "2026-10", "2024-04"]


def test_zero_salary_ends_bls_combined_codes_and_unpublished_months():
    from labor_jobs.merge import state_unemployment, to_bls_soc
    df = pd.DataFrame({"pay_period": ["YEARLY", "YEARLY"], "min_salary": [0, 90000],
                       "max_salary": [110000, 0]})
    assert annual_salary(df).tolist() == [110000, 90000]  # not halved by a 0

    assert to_bls_soc(pd.Series(["21-1014", "15-1252", None])).tolist()[:2] == ["21-1018", "15-1252"]

    postings = pd.DataFrame({"state": ["CA", "CA", "TX"], "job_id": ["1", "2", "3"],
                             "listed_date": ["2026-09-28", "2026-10-20", "2026-10-21"]})
    monthly = pd.DataFrame({"state": ["CA", "TX"], "month": ["2026-09", "2026-09"],
                            "unemployment_rate": [5.5, 4.1]})
    got = state_unemployment(postings, monthly).set_index("state")
    # October isn't published yet, so October postings use September.
    assert got.loc["TX", "unemployment_rate"] == 4.1
    assert got.loc["CA", "unemployment_period"] == "2026-09"


def write_cip_fixtures(tmp_path):
    cip = pd.DataFrame({
        "CIPFamily": ['="11"'] * 3 + ['="27"', '="36"'],
        "CIPCode": ['="11.0701"', '="11.0802"', '="11.0101"', '="27.0501"', '="36.0115"'],
        "CIPTitle": ["Computer Science.", "Data Modeling/Warehousing and Database Administration.",
                     "Computer and Information Sciences, General.", "Statistics, General.", "Music."],
        "Examples": ["", "", "", "Examples: - Applied Statistics", ""]})
    cip_csv = tmp_path / "cip.csv"
    cip.to_csv(cip_csv, index=False)
    xw = pd.DataFrame({
        "CIP2020Code": ["11.0701", "11.0701", "11.0701", "27.0501", "27.0501", "36.0115"],
        "CIP2020Title": ["Computer Science."] * 3 + ["Statistics, General."] * 2 + ["Music."],
        "SOC2018Code": ["15-1252", "15-2051", "25-1021", "15-2041", "15-2051", "27-2042"],
        "SOC2018Title": ["Software Developers", "Data Scientists", "Computer Science Teachers",
                         "Statisticians", "Data Scientists", "Musicians"]})
    crosswalk = tmp_path / "xw.xlsx"
    with pd.ExcelWriter(crosswalk) as xl:
        pd.DataFrame({"File Name": ["CIP-SOC"]}).to_excel(xl, sheet_name="File Guide", index=False)
        xw.to_excel(xl, sheet_name="CIP-SOC", index=False)
    return cip_csv, crosswalk


def test_major_matching_and_resume_education(tmp_path):
    from labor_jobs import majors
    m = majors.MajorMatcher(*write_cip_fixtures(tmp_path))
    assert m.match("CS")[0][0] == "11.0701"
    assert m.match("B.S. in Computer Science")[0][0] == "11.0701"
    assert m.match("Applied Statistics")[0][0] == "27.0501"  # from the program's examples
    assert m.match("Music") == []  # leisure-course family 36 isn't a major
    # Postsecondary teaching (25-1xxx) is left out.
    assert m.occupations_for(["11.0701"]) == ["15-1252", "15-2051"]

    text = ("Austin, TX | MS Office, Excel\nB.S. in Statistics, 2022\n"
            "Master of Science in Data Science from Rice University")
    assert majors.majors_in_text(text) == ["Statistics", "Data Science"]
    assert majors.education_level(text) == (4, "Master's degree")
    # "MS Office" and "TX"/"MA" abbreviations aren't degrees.
    assert majors.education_level("Skills: MS Office\nBoston, MA") == (0, "No degree found")


def test_resume_and_major_matching(tmp_path):
    import docx
    from labor_jobs import match, majors
    resume = tmp_path / "resume.docx"
    doc = docx.Document()
    for line in ["Taylor Example (fictional)", "Austin, TX | taylor@example.com | (555) 010-0000",
                 "Data Analyst | Acme Co. | 2022 - Present",
                 "Built dashboards with Python and SQL", "B.S. in Statistics, 2022",
                 "Skills: Python, SQL, Tableau, Excel, Git"]:
        doc.add_paragraph(line)
    doc.save(resume)
    text = match.read_document(resume)
    assert "Data Analyst | Acme Co." in text

    ref = pd.DataFrame({"title": ["Data Scientists", "Data Analyst", "Software Developers", "Tableau Developer"],
                        "onet_code": ["15-2051.00", "15-2051.00", "15-1252.00", "15-1252.00"]})
    matcher = TitleMatcher(ref)
    tech = pd.DataFrame({"example": ["Python", "Structured query language SQL", "Tableau", "Microsoft Excel",
                                     "Git", "Kubernetes"], "hot": [True] * 6})
    major_matcher = majors.MajorMatcher(*write_cip_fixtures(tmp_path))
    profile = match.resume_profile(text, matcher, tech, major_matcher)
    assert profile["titles"] == {"15-2051.00": "Data Analyst"}  # "Tableau" in the skills line isn't a title
    assert profile["technologies"] == ["Excel", "Git", "Python", "SQL", "Tableau"]
    assert profile["majors"] == ["Statistics"] and profile["education_level"] == 3

    detailed = pd.DataFrame({
        "onet_code": ["15-2051.00", "15-1252.00", "15-2041.00", "29-1141.00"],
        "soc_code": ["15-2051", "15-1252", "15-2041", "29-1141"],
        "onet_title": ["Data Scientists", "Software Developers", "Statisticians", "Registered Nurses"],
        "postings": [5, 9, 1, 20],
        "posting_technologies": ["Python 60%, SQL 50%, Spark 20%", "Python 40%, Kubernetes 30%", None, None],
        "onet_hot_technologies": [None, None, "R, SAS, Python", "Microsoft Office"],
        "typical_education": ["Bachelor's degree", "Bachelor's degree", "Master's degree", "Bachelor's degree"]})
    major_socs = set(major_matcher.occupations_for(["27.0501"]))
    occ = match.score_occupations(detailed, profile, major_socs).set_index("onet_code")
    assert occ.index[0] == "15-2051.00"  # held title + major + skills
    # Title 1, major 1 (its program leads to 2 occupations), skills weighted by
    # rarity: Python is in 3 of 4 occupations' lists, the rest in at most 1.
    import math
    rare, common = math.log(4), math.log(4 / 3)
    skills = (rare + common) / (4 * rare + common)
    assert occ.loc["15-2051.00", "fit_score"] == round((3 + 1 + 1.5 * skills) / 5.5, 3)
    assert "Spark" in occ.loc["15-2051.00", "skills_to_add"]
    # Statisticians fit the major but usually need a master's: counted half.
    assert "usually needs: Master's degree" in occ.loc["15-2041.00", "why"]
    assert "29-1141.00" not in occ.index  # nothing in common

    postings = pd.DataFrame({
        "job_id": ["1", "2", "3", "4"], "title": ["Data Analyst", "Data Scientist", "Backend Engineer", "Nurse"],
        "onet_code": ["15-2051.00", "15-2051.00", "15-1252.00", "29-1141.00"],
        "state": ["TX", "CA", "TX", "TX"], "annual_salary": [90000, 150000, 120000, 80000],
        "technologies": ["Python; SQL", "Spark", "Kubernetes", ""], "metro": ["12420", None, None, None]})
    ranked = match.rank_postings(postings, occ.reset_index(), profile, top_occupations=5)
    assert ranked["job_id"].tolist()[:2] == ["1", "2"]  # same occupation; posting 1 lists their skills
    assert ranked.loc[ranked.job_id == "1", "matched_skills"].iat[0] == "Python, SQL"
    texas = match.rank_postings(postings, occ.reset_index(), profile, 5, states=["tx"])
    assert set(texas["state"]) == {"TX"}
    austin = match.rank_postings(postings, occ.reset_index(), profile, 5, metro="austin",
                                 metro_names={"12420": "Austin-Round Rock, TX"})
    assert austin["job_id"].tolist() == ["1"]
    assert match.rank_postings(postings, occ.reset_index(), profile, 5, min_salary=100000)["job_id"].tolist() == ["2", "3"]


def test_pay_and_us_location_parsing():
    from labor_jobs.feeds import parse_pay, us_location
    assert parse_pay("The range is $120,000 - $150,000 per year") == (120000, 150000, "YEARLY")
    assert parse_pay("$150,000.00 - $210,000.00") == (150000, 210000, "YEARLY")
    assert parse_pay("$120-150K base") == (120000, 150000, "YEARLY")
    assert parse_pay("Pay: $45.00 - $55.00 per hour") == (45, 55, "HOURLY")
    for text in ("We raised $100M", "a $1.2 billion valuation", "$100 sign-on bonus",
                 "$75/month phone stipend", "no pay listed", None):
        assert parse_pay(text) == (None, None, None), text
    assert us_location("San Francisco, CA") == ("San Francisco, CA", "CA")
    assert us_location("Seattle, Washington") == ("Seattle, WA", "WA")
    assert us_location("Washington, D.C.") == ("Washington, DC", "DC")
    assert us_location("New York, NY; London, UK") == ("New York, NY", "NY")
    assert us_location("Remote - US") == ("Remote, US", None)
    assert us_location("Remote") == ("Remote", None)
    assert us_location("London, UK") is None and us_location("Remote (Canada)") is None


def test_merging_the_same_job_across_sources():
    from labor_jobs.linkedin import merge_duplicates, source_of
    assert source_of(pd.Series(["adzuna-1", "greenhouse-stripe-9", "3901234"])).tolist() == \
        ["adzuna", "greenhouse", "linkedin"]
    postings = pd.DataFrame({
        "job_id": ["greenhouse-acme-1", "adzuna-7", "adzuna-8", "adzuna-9", "adzuna-10", "usajobs-5"],
        "source": ["greenhouse", "adzuna", "adzuna", "adzuna", "adzuna", "usajobs"],
        "title": ["Data Engineer", "Data Engineer ", "Data Engineer", "Nurse", "Nurse", "Nurse"],
        "company_name": ["Acme", "ACME, Inc.", "Acme", "VA", "VA", "VA"],
        "state": ["TX", "TX", "CA", "DC", "DC", "DC"],
        "location": ["Austin, TX", "Austin, TX", "San Jose, CA", "Washington, DC", "Washington, DC",
                     "Washington, DC"]})
    kept, merged = merge_duplicates(postings)
    # The Adzuna copy of Acme's Austin job and the two Adzuna copies of the VA job go;
    # Acme's San Jose job is a different job.
    assert merged == 3
    assert kept["job_id"].tolist() == ["greenhouse-acme-1", "adzuna-8", "usajobs-5"]
    assert kept.loc[kept.job_id == "greenhouse-acme-1", "also_listed_on"].iat[0] == "adzuna"
    # Identical postings within one source are separate openings and are kept.
    same = pd.concat([postings.iloc[[3]]] * 2, ignore_index=True).assign(job_id=["adzuna-1", "adzuna-2"])
    assert merge_duplicates(same)[1] == 0


def test_usajobs_rows():
    from labor_jobs import usajobs
    item = {"usajobsControlNumber": 759375300, "hiringAgencyName": "Veterans Health Administration",
            "positionTitle": "Registered Nurse ", "positionOpenDate": "2026-10-01", "salaryType": "Per Year",
            "minimumSalary": 75332.0, "maximumSalary": 133142.0, "workSchedule": "Full-time",
            "jobcategories": [{"series": "0610"}],
            "positionlocations": [{"positionLocationCity": "Austin", "positionLocationState": "Texas",
                                   "positionLocationCountry": "United States"},
                                  {"positionLocationCity": "Waco", "positionLocationState": "Texas",
                                   "positionLocationCountry": "United States"}]}
    row, in_us = usajobs.to_row(item, "2026-10-29")
    assert in_us and row["location"] == "Austin, TX" and row["title"] == "Registered Nurse"
    assert (row["min_salary"], row["max_salary"], row["pay_period"]) == (75332.0, 133142.0, "YEARLY")
    assert row["work_type"] == "Full-time; 2 location(s)" and row["category"] == "OPM series 0610"
    abroad = {**item, "positionlocations": [{"positionLocationCity": "Seoul",
                                              "positionLocationCountry": "Korea, South"}]}
    assert usajobs.to_row(abroad, "2026-10-29")[1] is False


def test_job_board_parsers():
    from labor_jobs import ats
    gh = {"jobs": [{"id": 1, "title": "Data Engineer", "location": {"name": "Austin, TX"},
                    "updated_at": "2026-10-20T10:00:00Z", "absolute_url": "https://x/1",
                    "content": "&lt;p&gt;Python and SQL. Pay: $140,000 - $170,000 per year&lt;/p&gt;"},
                   {"id": 2, "title": "Engineer", "location": {"name": "London, UK"}, "content": ""}]}
    rows = [r for r in ats.greenhouse("acme", gh, "Acme") if r]
    assert len(rows) == 1  # the London job is dropped
    assert rows[0]["job_id"] == "greenhouse-acme-1" and rows[0]["company_name"] == "Acme"
    assert (rows[0]["min_salary"], rows[0]["max_salary"]) == (140000, 170000)
    assert "Python and SQL" in rows[0]["description"]

    lv = [{"id": "a", "text": "Nurse", "categories": {"location": "Denver, CO", "commitment": "Full-time"},
           "createdAt": 1790000000000, "hostedUrl": "https://x/a", "descriptionPlain": "Care",
           "salaryRange": {"min": 90000, "max": 110000, "currency": "USD", "interval": "per-year-salary"}}]
    row = next(r for r in ats.lever("acme", lv) if r)
    assert (row["min_salary"], row["pay_period"], row["location"]) == (90000, "YEARLY", "Denver, CO")

    ab = {"jobs": [{"id": "z", "title": "ML Engineer", "isRemote": True, "location": "United States",
                    "publishedAt": "2026-10-01", "jobUrl": "https://x/z", "descriptionPlain": "PyTorch",
                    "compensation": {"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR",
                                                            "currencyCode": "USD", "minValue": 200000,
                                                            "maxValue": 260000}]}}]}
    row = next(r for r in ats.ashby("acme", ab) if r)
    assert row["location"] == "Remote, US" and (row["min_salary"], row["max_salary"]) == (200000, 260000)


def test_perm_wages(tmp_path):
    from labor_jobs import perm
    old = pd.DataFrame({"CASE_NUMBER": ["A-1", "A-2", "A-3"], "CASE_STATUS": ["Certified", "Denied", "Certified-Expired"],
                        "DECISION_DATE": ["2024-01-05"] * 3, "EMPLOYER_NAME": ["Acme"] * 3,
                        "JOB_TITLE": ["Software Engineer"] * 3, "PW_SOC_CODE": ["15-1252", "15-1252", "15-1252.00"],
                        "WAGE_OFFER_FROM": ["150,000", "1", "70"], "WAGE_OFFER_UNIT_OF_PAY": ["Year", "Year", "Hour"],
                        "WORKSITE_CITY": ["AUSTIN"] * 3, "WORKSITE_STATE": ["TEXAS"] * 3})
    new = pd.DataFrame({"CASE_NUMBER": ["G-1"], "CASE_STATUS": ["Certified"], "DECISION_DATE": ["2024-02-01"],
                        "EMP_BUSINESS_NAME": ["Beta"], "JOB_TITLE": ["Accountant"], "JOB_OPP_WAGE_FROM": ["80000"],
                        "JOB_OPP_WAGE_PER": ["Year"], "PRIMARY_WORKSITE_CITY": ["Boston"],
                        "PRIMARY_WORKSITE_STATE": ["MA"]})
    old.to_csv(tmp_path / "old.csv", index=False)
    new.to_csv(tmp_path / "new.csv", index=False)
    d = perm.read_perm([tmp_path / "old.csv", tmp_path / "new.csv"]).set_index("case_number")
    assert list(d.index) == ["A-1", "A-3", "G-1"]  # the denied filing is dropped
    assert d.loc["A-1", "onet_code"] == "15-1252.00" and d.loc["A-1", "state"] == "TX"
    assert d.loc["A-3", "annual_wage"] == 70 * 2080
    assert pd.isna(d.loc["G-1", "onet_code"]) and d.loc["G-1", "state"] == "MA"  # matched by title later


def test_history_records_each_postings_source(tmp_path):
    from labor_jobs import history
    db = tmp_path / "h.sqlite"
    postings = pd.DataFrame({"job_id": ["a1", "u1", "u2"], "soc_code": ["15-1252", "29-1141", "29-1141"],
                             "state": ["TX"] * 3, "source": ["adzuna", "usajobs", "usajobs"]})
    history.record(postings, pd.Series("2026-10", index=postings.index), db, "x", postings["source"])
    assert history.monthly_counts(db, "usajobs")["postings"].sum() == 2
    assert history.monthly_counts(db, ["adzuna", "usajobs"])["postings"].sum() == 3


def test_appending_to_a_file_in_an_older_layout(tmp_path):
    import csv
    from labor_jobs.feeds import FIELDS, write_rows
    from labor_jobs.linkedin import load_postings
    old = tmp_path / "old.csv"
    with old.open("w", newline="") as f:  # no "source" column, as written before it existed
        f.write("job_id,title,location,min_salary\nadzuna-1,Nurse,\"Austin, TX\",90000\n")
    added = write_rows([{"job_id": "adzuna-1", "title": "dup"},
                        {"job_id": "adzuna-2", "source": "adzuna", "title": "Welder", "location": "Waco, TX"}],
                       old, append=True)
    assert added == 1
    with old.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == FIELDS
    assert [(r["job_id"], r["title"], r["location"]) for r in rows] == \
        [("adzuna-1", "Nurse", "Austin, TX"), ("adzuna-2", "Welder", "Waco, TX")]
    loaded = load_postings(old)
    assert loaded["source"].tolist() == ["adzuna", "adzuna"]  # blank source worked out from the ID


def test_reading_posting_requirements():
    from labor_jobs.requirements import degree_required, seniority, years_required
    assert years_required("5+ years of experience in software engineering.") == 5
    assert years_required("Minimum of two years of related work experience.") == 2
    assert years_required("3+ years of Python. 7+ years experience building distributed systems.") == 7
    assert years_required("Experience with AWS preferred; 2+ years experience a plus.") is None
    assert years_required("We have 10 years of history as a company.") is None
    # Preferred sections don't count; alternative paths count only as the lower option.
    assert years_required("BASIC QUALIFICATIONS:\n1+ years of experience welding\n"
                          "PREFERRED SKILLS AND EXPERIENCE:\n5+ years of professional experience") == 1
    assert years_required("Bachelor's and 3+ years of experience, or 7+ years of equivalent experience") == 3
    assert degree_required("Bachelor's degree in Computer Science or equivalent experience required.") == (3, True)
    assert degree_required("BS/BA required; Master's preferred.") == (3, False)
    assert degree_required("High school diploma or equivalency certificate") == (0, False)
    assert degree_required("Master's degree preferred.") == (None, False)
    assert seniority("Associate Director, Finance") == (8, "Director or above")
    assert seniority("Senior Data Engineer") == (5, "Senior")
    assert seniority("Associate Attorney") == (0, "Entry level")
    assert seniority("Nurse") == (None, None)


def test_majors_skip_dates_and_schools():
    from labor_jobs.majors import majors_in_text
    assert majors_in_text("Bachelor of Science, October 2022") == []
    assert majors_in_text("Bachelor's degree (October 2025)") == []
    assert majors_in_text("Bachelor of Science\nUniversity of Michigan") == []
    assert majors_in_text("BS in Computer Science - October 2024") == ["Computer Science"]
    assert majors_in_text("B.A. Psychology, May 2020") == ["Psychology"]
    assert majors_in_text("Bachelor of Business Administration, Dec 2020") == ["Business Administration"]
    assert majors_in_text("Degree: Bachelor of Science in Nursing, Graduated October 2019") == ["Nursing"]
    assert majors_in_text("B.S. Mechanical Engineering   Ohio State University") == ["Mechanical Engineering"]


def test_resume_technologies_ignore_case_but_not_everyday_words():
    from labor_jobs import skills
    tech = pd.DataFrame({"example": ["Python", "Microsoft Excel", "Microsoft Word", "Atlassian JIRA",
                                     "Epic Systems", "PostgreSQL"], "hot": True})
    rows = skills.resume_aliases(tech)
    find = lambda text: skills.resume_technologies(text, rows)  # noqa: E731
    assert find("Skills: python, jira, MS Excel, Postgres") == ["Excel", "JIRA", "PostgreSQL", "Python"]
    assert find("PYTHON | EXCEL") == ["Excel", "Python"]
    # Everyday words: not in running text, but "Word" counts in a list of skills.
    assert find("I excel at word processing and epic projects.") == []
    assert find("Tools: Word, Excel, Snowflake") == ["Excel", "Microsoft Word", "Snowflake"]


def test_resume_years_of_experience():
    from datetime import date
    from labor_jobs.requirements import resume_years
    text = ("Data Analyst | Acme | Jan 2022 - Present\n"
            "Intern - Lone Star - Jun 2021 – Aug 2021\n"      # inside the next job: counted once
            "Analyst, Foo Corp, 2019 - 2021\n"
            "University of Texas, B.S. Statistics, 2018 - 2022\n")  # schooling: left out
    # Jan 2019-Dec 2021 (36 months) + Jan 2022-Oct 2026 (58 months) = 94 months.
    assert resume_years(text, date(2026, 10, 30)) == 7.8
    assert resume_years("No dates here") is None


def test_posting_checks_and_ratings():
    from labor_jobs import match
    profile = {"technologies": ["Python", "SQL", "Tableau"], "education_level": 3, "years": 3.0,
               "from_resume": True}
    Row = __import__("collections").namedtuple("Row", "title years_required degree_required degree_flexible onet_code")
    strong = match.posting_checks(Row("Data Analyst", 2, 3, False, "x"), {"Python", "SQL"}, profile, 0.8,
                                  "Bachelor's degree")
    assert strong["rating"] == "Strong match" and strong["rating_reason"] == "meets what it states"
    assert strong["experience_detail"] == "2+ yrs asked; resume shows 3"

    senior = match.posting_checks(Row("Senior Data Scientist", None, 4, False, "x"),
                                  {"Python", "Spark", "Kubernetes", "Scala"}, profile, 0.3, "Master's degree")
    # ~5 years implied by "Senior" vs 3; a master's required vs a bachelor's; 1 of 4 skills.
    assert (senior["experience_check"], senior["education_check"], senior["skills_check"]) == \
        ("short", "close", "short")
    assert senior["rating"] == "Stretch"

    close = match.posting_checks(Row("Data Analyst", 2, 4, False, "x"), {"Python", "SQL"}, profile, 0.8, None)
    assert close["education_check"] == "close" and close["rating"] == "Partial match"  # a master's asked

    flexible = match.posting_checks(Row("Analyst", 4, 4, True, "x"), set(), profile, 0.6, None)
    assert flexible["education_check"] == "close"  # "or equivalent experience"
    assert flexible["skills_check"] == "not stated" and flexible["experience_check"] == "close"
    assert flexible["rating"] == "Partial match"


def test_patent_and_federal_titles_normalize():
    assert title_variants("Patent Examiner (Physics) Direct-Hire Authority (DHA)") == ["patent examiner"]
    assert title_variants("Senior Patent Agent- Biologics")[-1] == "patent agent"
    assert title_variants("IP Counsel, Trademarks & Domains")[-1] == "intellectual property counsel"
    assert normalize_title("IP Network Engineer") == "ip network engineer"  # networking IP stays
    assert normalize_title("Veterinarian NF-04") == "veterinarian"
    assert normalize_title("Full-Stack Engineer") == "full stack engineer"


def test_title_relevance_favors_rare_shared_words():
    from labor_jobs.match import title_relevance
    words = set(normalize_title("Patent Attorney at Smith LLP, patent prosecution").split())
    titles = ["Patent Counsel", "Family Law Attorney", "Litigation Attorney", "Attorney", "Real Estate Attorney"]
    scores = title_relevance(titles, words)
    # "counsel" and "attorney" count as the same word; "patent" is rare, so it counts most.
    assert scores[0] == max(scores) and scores[0] > 0.7
    assert scores[1] < scores[3] < scores[0]  # bare "Attorney" between the patent and family-law titles
    assert scores[1] < 0.3


def test_ad_noise_and_contained_titles():
    ref = pd.DataFrame({"title": ["Registered Nurse", "Retail Salesperson", "Sales Associate", "Food Service Worker",
                                  "Physical Therapist"],
                        "onet_code": ["29-1141.00", "41-2031.00", "41-2031.00", "35-3023.00", "29-1123.00"]})
    m = TitleMatcher(ref)
    out = m.map_titles(pd.Series([
        "Travel Cardiac Stepdown RN - $2,084 per week",            # pay stripped, RN found inside
        "LEAD SALES ASSOCIATE-PT in MINOT ND S24123",               # store number and town around it
        "Full-Time Licensed Physical Therapist at Rehab Center",
        "Food Service Worker Supervisor",                           # the job is the supervisor's: no match
    ]))
    assert out["onet_code"].tolist()[:3] == ["29-1141.00", "41-2031.00", "29-1123.00"]
    assert pd.isna(out["onet_code"].iloc[3])
    assert normalize_title("Process Lead, Allston, #1178") == "process allston"
    assert normalize_title("Quantitative Trader Intern - Summer 2027") == "quantitative trader intern"


def test_duties_find_the_occupation_without_a_known_title():
    from labor_jobs import match
    from labor_jobs.occupation_text import TextModel, tokens
    assert tokens("Prosecuting patents")[:2] == ["prosecut", "patent"]
    model = TextModel({
        "23-1011.00": "Lawyers. Represent clients. Prepare and prosecute patent applications. Draft legal opinions.",
        "29-2072.00": "Medical Records Specialists. Assign ICD-10 and CPT codes to patient encounters. Submit claims.",
        "47-2111.00": "Electricians. Install conduit, wiring and panels. Read blueprints.",
    })
    text = "Associate, Patent Prosecution Group\nDraft and prosecute patent applications; respond to office actions"
    sims = model.similarity(text)
    assert max(sims, key=sims.get) == "23-1011.00"
    detailed = pd.DataFrame({"onet_code": list(sims), "soc_code": [c[:7] for c in sims],
                             "onet_title": ["Lawyers", "Medical Records Specialists", "Electricians"],
                             "postings": [3, 3, 3], "posting_technologies": [None] * 3,
                             "onet_hot_technologies": [None] * 3, "typical_education": [None] * 3})
    profile = {"titles": {}, "technologies": [], "education_level": 5, "text_similarity": sims}
    occ = match.score_occupations(detailed, profile, None)
    assert occ["onet_code"].iloc[0] == "23-1011.00" and occ["fit_score"].iloc[0] == 1.0
    assert "duties match its tasks" in occ["why"].iloc[0]


def test_reading_personality_and_interest_results():
    from labor_jobs import personality as P
    interest_report = """O*NET Interest Profiler results
Realistic: 12   Investigative: 34   Artistic: 9
Social: 38   Enterprising: 15   Conventional: 22"""
    r = P.parse_results(interest_report)["riasec"]
    assert P.holland_code(r) == "SIC" and r["S"] == 38
    assert P.holland_code(P.parse_results("Your Holland Code is: AES")["riasec"]) == "AES"
    big5 = P.parse_results("Openness to Experience 72%\nConscientiousness 88th percentile\n"
                           "Extraversion 25\nAgreeableness 60\nNeuroticism 30")["big_five"]
    assert big5 == {"O": 0.72, "C": 0.88, "E": 0.25, "A": 0.6, "N": 0.7}  # neuroticism -> stability
    assert P.parse_results("Openness 4.5\nConscientiousness 3\nExtraversion 1")["big_five"]["O"] == 0.875  # 1-5 scale
    found = P.parse_results("Your personality type: INFJ-T (Advocate)\nFull Scale IQ: 118")
    assert found["mbti"] == "INFJ" and found["iq"] == 118
    assert P.parse_results("Team meeting notes")["riasec"] is None
    assert P.holland_code(P.mbti_to_riasec("ESTJ")) in {"CER", "ECR", "CRE", "ERC"}
    assert P.iq_band(118) == "above average"


def test_interests_rank_matching_occupations_first():
    from labor_jobs import personality as P
    traits = pd.DataFrame({
        "onet_code": ["17-2141.00", "25-2021.00", "41-3091.00"],
        "R": [6.5, 1.5, 1.5], "I": [6.0, 3.0, 2.0], "A": [3.0, 3.5, 2.5],
        "S": [2.0, 6.8, 4.0], "E": [3.0, 4.0, 6.5], "C": [4.5, 3.0, 4.5],
        "b5_O": [0.8, 0.5, 0.3], "b5_C": [0.9, 0.6, 0.4], "b5_E": [0.2, 0.7, 0.9],
        "b5_A": [0.3, 0.9, 0.5], "b5_N": [0.5, 0.7, 0.6], "reasoning": [4.5, 3.6, 3.0]})
    traits["reasoning_band"] = [P.reasoning_band(v, traits["reasoning"]) for v in traits["reasoning"]]
    sig = P.trait_signals({"riasec": P.riasec_from_code("RIC"), "big_five": {"O": .8, "C": .9, "E": .2}}, traits)
    assert max(sig, key=lambda c: sig[c]["signals"]["interests"]) == "17-2141.00"
    assert sig["17-2141.00"]["signals"]["styles"] == 1.0 and sig["17-2141.00"]["holland"] == "RIC"
    assert sig["17-2141.00"]["reasoning"] == "High"  # above 2 of the 3 occupations
    # Myers-Briggs is used only without real interest scores.
    assert "mbti" in P.trait_signals({"mbti": "ENFJ"}, traits)["25-2021.00"]["signals"]
    assert "mbti" not in P.trait_signals({"mbti": "ENFJ", "riasec": P.riasec_from_code("S")}, traits)["25-2021.00"]["signals"]


def test_posting_credentials_and_soft_skills():
    from labor_jobs.posting_requirements import credentials_in, posting_requirements
    text = ("Requirements\nActive RN license in the state of Texas. BLS required.\n"
            "Strong communication skills and attention to detail.\n"
            "Preferred Qualifications\nACLS certification.\nCPA or CMA a plus.")
    required, preferred, soft = posting_requirements(text)
    assert required == ["BLS/CPR", "Registered nurse (RN) license"]
    assert preferred == ["ACLS/PALS", "CPA"]
    assert soft == ["Attention to detail", "Communication"]
    assert posting_requirements("") == ([], [], [])
    assert credentials_in("Licensed journeyman electrician; OSHA 30") == ["Electrician license", "OSHA 10/30"]
    assert credentials_in("Class A CDL with Hazmat") == ["Commercial driver's license (CDL)"]
    assert credentials_in("RN-to-BSN program coordinator") == []  # "RN to" isn't a license


def test_education_and_experience_requirements():
    from labor_jobs import job_requirements as J
    ors = pd.DataFrame([
        {"soc_code": "29-1141", "edu_0": 0, "edu_1": None, "edu_2": 61.8, "edu_3": 38.2, "edu_4": None, "edu_5": None,
         "edu_published": 100, "exp_pct": 17.6, "exp_years": None, "license_pct": None, "apprenticeship_pct": None},
        {"soc_code": "15-1250", "edu_0": 5, "edu_1": None, "edu_2": 10, "edu_3": 85, "edu_4": None, "edu_5": None,
         "edu_published": 100, "exp_pct": 76, "exp_years": 2.0, "license_pct": 1.3, "apprenticeship_pct": None}])
    onet = pd.DataFrame([{"onet_code": "29-1141.00", "edu_0": 1, "edu_1": 22, "edu_2": 19, "edu_3": 57, "edu_4": 1,
                          "edu_5": 0, "exp_pct": 81, "exp_years": 1.5}])
    t = J.requirements_table(["29-1141.00", "15-1252.00"], ors, onet).set_index("onet_code")
    rn = t.loc["29-1141.00"].to_dict()
    assert rn["edu_source"] == "ORS" and rn["exp_source"] == "O*NET"  # ORS experience years suppressed
    assert round(J.share_requiring_more(rn, 2)) == 38 and J.typical_education(rn) == 2
    dev = t.loc["15-1252.00"].to_dict()  # from the broad group 15-1250
    assert dev["exp_years"] == 2.0 and dev["license_pct"] == 1.3 and J.typical_education(dev) == 3


def test_usajobs_announcement_text():
    from labor_jobs.usajobs import announcement_text
    item = {"requirementsQualifications": "<p>Specialized experience:</p><ul><li>One year at GS-11</li></ul>",
            "evaluations": "You will be evaluated on <b>Communication</b> &amp; Customer Service.",
            "requirementsEducation": "None"}
    text = announcement_text(item)
    assert text.startswith("Qualifications\nSpecialized experience:\n- One year at GS-11")
    assert "How you will be evaluated\nYou will be evaluated on Communication & Customer Service." in text
    assert "Education" not in text  # "None" sections are left out


def test_requirements_and_credentials_shape_the_fit():
    from labor_jobs import match
    detailed = pd.DataFrame({
        "onet_code": ["29-1141.00", "53-3032.00"], "soc_code": ["29-1141", "53-3032"],
        "onet_title": ["Registered Nurses", "Heavy and Tractor-Trailer Truck Drivers"], "postings": [50, 50],
        "posting_technologies": [None, None], "onet_hot_technologies": [None, None],
        "typical_education": ["Bachelor's degree", "Postsecondary nondegree award"],
        "edu_0": [0, 95], "edu_1": [0, 5], "edu_2": [62, 0], "edu_3": [38, 0], "edu_4": [0, 0], "edu_5": [0, 0],
        "exp_pct": [80, 71], "exp_years": [1.5, 1.0], "license_pct": [None, 95],
        "credentials_required": ["Registered nurse (RN) license 82%; BLS/CPR 40%", "Commercial driver's license (CDL) 90%"],
        "credentials_preferred": [None, None]})
    nurse = {"titles": {}, "technologies": [], "education_level": 2, "years": 3.0, "from_resume": True,
             "credentials": ["Registered nurse (RN) license", "BLS/CPR"]}
    occ = match.score_occupations(detailed, nurse, None).set_index("onet_code")
    assert occ.index[0] == "29-1141.00"
    assert "holds Registered nurse (RN) license (82% of postings require it)" in occ.loc["29-1141.00", "why"]
    assert "38% of jobs need more than an associate's degree" in occ.loc["29-1141.00", "why"]
    # The driver occupation isn't scored on credentials the nurse lacks... but mostly needs a CDL.
    factor, why = match.requirement_adjustments(next(detailed.iloc[[1]].itertuples(index=False)), nurse)
    assert factor == 0.85 and why == ["most jobs require Commercial driver's license (CDL), not on the resume"]
    # Too little experience lowers the fit a little, scaled by how many jobs require it.
    junior = {**nurse, "years": 0.0}
    f_junior, why_junior = match.requirement_adjustments(next(detailed.iloc[[0]].itertuples(index=False)), junior)
    f_senior, _ = match.requirement_adjustments(next(detailed.iloc[[0]].itertuples(index=False)), nurse)
    assert round(f_senior / f_junior, 3) == round(1 / (1 - 0.3 * 0.8), 3)
    assert "80% of jobs want prior experience, typically 1.5+ years" in why_junior

    Row = __import__("collections").namedtuple(
        "Row", "title years_required degree_required degree_flexible onet_code credentials_required")
    row = Row("Travel RN", None, None, False, "29-1141.00", "Registered nurse (RN) license; ACLS/PALS")
    checks = match.posting_checks(row, set(), nurse, 0.8, "Bachelor's degree", (1.5, 80))
    assert checks["credentials_check"] == "close"  # ACLS is a certification, not a license
    assert checks["credentials_detail"] == "requires Registered nurse (RN) license, ACLS/PALS; not on resume: ACLS/PALS"
    assert checks["experience_detail"] == "~1.5+ yrs typical for the occupation; resume shows 3"


def test_rank_by_settings():
    from labor_jobs import match
    detailed = pd.DataFrame({
        "onet_code": ["15-2051.00", "15-1252.00", "43-9061.00"], "soc_code": ["15-2051", "15-1252", "43-9061"],
        "onet_title": ["Data Scientists", "Software Developers", "Office Clerks"], "postings": [5, 9, 3],
        "posting_technologies": ["Python 60%, SQL 50%", "Java 40%", None], "onet_hot_technologies": [None] * 3,
        "typical_education": ["Bachelor's degree", "Bachelor's degree", "High school diploma or equivalent"],
        "bls_median_wage": [112000, 133000, 41000], "salary_samples": [0, 0, 0], "median_posted_salary": [None] * 3,
        "proj_growth_pct": [34.0, 15.0, -3.0], "proj_annual_openings": [20000, 140000, 300000]})
    profile = {"titles": {"15-2051.00": "Data Analyst"}, "technologies": ["Python", "SQL"], "education_level": 3}
    by_resume = match.score_occupations(detailed, profile, None)
    assert by_resume["onet_code"].iloc[0] == "15-2051.00"
    assert by_resume.set_index("onet_code").loc["15-2051.00", "fit_score"] == \
        by_resume.set_index("onet_code").loc["15-2051.00", "resume_fit"]  # only the resume counts by default
    by_pay = match.score_occupations(detailed, profile, None, {"resume": 0, "personality": 0, "salary": 1, "outlook": 0})
    assert by_pay["onet_code"].tolist() == ["15-1252.00", "15-2051.00", "43-9061.00"]
    assert "pays well: median $133,000" in by_pay.set_index("onet_code").loc["15-1252.00", "why"]
    assert match.parse_rank_by("pay") == match.RANK_PRESETS["pay"]
    assert match.parse_rank_by("salary=100, outlook=50") == {"resume": 0, "personality": 0, "salary": 100, "outlook": 50}


def test_industries_employing_each_job():
    from labor_jobs import industries as I
    table = pd.DataFrame({"soc_code": ["29-1141"] * 3 + ["47-2111"] * 2,
                          "naics": ["622", "621", "999", "238", "113"],
                          "industry": ["Hospitals", "Ambulatory Health Care Services",
                                       I.short_title("Federal, State, and Local Government, excluding ... (OEWS Designation)"),
                                       "Specialty Trade Contractors", "Forestry and Logging"],
                          "employment": [600, 300, 100, 900, 100], "median_wage": [97000, 84000, 106000, 61000, 50000]})
    top = I.top_industries(table, n=2)
    rn = top[top.soc_code == "29-1141"]
    assert rn["industry"].tolist() == ["Hospitals", "Ambulatory Health Care Services"]
    assert rn["share_pct"].tolist() == [60.0, 30.0]  # of the job's wage and salary employment
    assert rn["jolts"].tolist() == ["Health care and social assistance"] * 2
    assert I.jolts_industry("113") == "Mining and logging" and I.jolts_industry("999") == "Government"
    assert I.short_title("Federal, State, and Local Government, excluding X (OEWS Designation)") == \
        "Government (federal, state and local)"


def test_adzuna_fill_plan_targets_shortfalls(tmp_path):
    from labor_jobs import adzuna
    (tmp_path / "Sample of Reported Titles.txt").write_text(
        "O*NET-SOC Code\tReported Job Title\tShown in My Next Move\n"
        "25-2021.00\tClassroom Teacher\tY\n25-2021.00\tTeacher\tN\n"
        "25-2031.00\tTeacher\tY\n25-2031.00\tScience Teacher (HS)\tN\n"
        "15-1252.00\tSoftware Engineer\tY\n35-2014.00\tLine Cook\tY\n")
    titles = adzuna.read_titles(tmp_path, counts={"teacher": 50})
    assert titles["25-2021"][0] == "Teacher"          # the title employers use most comes first
    assert titles["25-2031"] == ["Teacher", "Science Teacher"]
    employment = {"25-2021": 1_000_000, "25-2031": 1_000_000, "15-1252": 1_000_000, "35-2014": 10_000}
    postings = {"25-2021": 10, "25-2031": 20, "15-1252": 5000}
    plan = adzuna.fill_plan(employment, postings, titles, calls=5)
    # Software developers are over-posted and line cooks too small to count. "Teacher" goes to the furthest-short
    # occupation only; far-short ones get deeper pages of their best title before their next title.
    assert plan == [("25-2021", "Teacher", 1), ("25-2031", "Science Teacher", 1), ("25-2021", "Teacher", 2),
                    ("25-2031", "Science Teacher", 2), ("25-2021", "Teacher", 3)]
    # Searches an earlier fill made are skipped.
    again = adzuna.fill_plan(employment, postings, titles, calls=2, searched=["Teacher", "teacher#2", "Science Teacher"])
    assert again == [("25-2021", "Teacher", 3), ("25-2031", "Science Teacher", 2)]
    assert adzuna._on_topic([{"title": "Warehouse Picker"}, {"title": "Store Cashier"}], "Cashier") == 0.5
    # A small occupation with few postings is too little short to earn a share; --thin gives it one search first.
    employment["35-2014"] = 30_000
    assert all(soc != "35-2014" for soc, _, _ in adzuna.fill_plan(employment, postings, titles, calls=4))
    first = adzuna.fill_plan(employment, postings, titles, calls=4, thin=10)
    assert first[0] == ("35-2014", "Line Cook", 1) and len(first) == 4


def test_one_word_job_at_the_end_of_a_title():
    ref = pd.DataFrame({"title": ["Cashier", "Electrician", "Architect", "Cashier Supervisor"],
                        "onet_code": ["41-2011.00", "47-2111.00", "17-1011.00", "41-1011.00"]})
    m = TitleMatcher(ref)
    out = m.map_titles(pd.Series(["Subway Cashier", "Gift Shop Cashier - PRN / Days", "Residential Electrician",
                                  "Solutions Architect", "Cashier Supervisor"]))
    assert out["onet_code"].tolist()[:3] == ["41-2011.00"] * 2 + ["47-2111.00"]
    assert pd.isna(out["onet_code"].iloc[3])          # "architect" alone is too broad to trust
    assert out["onet_code"].iloc[4] == "41-1011.00"


def test_refresh_month_and_thin_states(tmp_path):
    from datetime import date
    from labor_jobs import refresh
    assert refresh.last_month(date(2026, 12, 3)) == "2026-11"
    assert refresh.last_month(date(2027, 1, 2)) == "2026-12"
    (tmp_path / "states.csv").write_text("state,postings\nDE,57\nTX,4000\nUS,9\n")
    assert refresh.thin_states(tmp_path, 300) == ["Delaware"]


def test_publish_points_datasets_at_new_uploads():
    from labor_jobs import publish
    docs = {"summary": {"description": "Totals from the October 2026 run; BLS wages are May 2024.",
                        "source": {"kind": "file", "url": "/_blob/old", "name": "summary.json", "format": "json"}},
            "live": {"source": {"kind": "call", "connector": "x"}}}
    out = publish.updates(docs, {"summary": "/_blob/new", "live": "/_blob/y", "gone": "/_blob/z"}, "2026-11",
                          now="2026-12-03T00:00:00Z")
    assert list(out) == ["summary"]                       # only file datasets that exist
    assert out["summary"]["source"] == {"kind": "file", "url": "/_blob/new", "name": "summary.json", "format": "json"}
    assert out["summary"]["description"] == "Totals from the November 2026 run; BLS wages are May 2024."
    assert publish.previous_month("2027-01") == "2026-12"


def test_occupation_titles_group_and_clean(tmp_path):
    from labor_jobs.dashboard import display_title, occupation_titles
    assert display_title("Travel ICU RN - $3,065 per week") == "Travel ICU RN"
    assert display_title("Store Clerk #1234 - Miami") == "Store Clerk - Miami"
    rows = ["title,soc_code,annual_salary", "Cashier,41-2011,30000", "Cashier - $15/hour,41-2011,31200",
            "cashier,41-2011,32000", "Store Clerk,41-2011,", "Store Clerk,41-2011,", "One-off Title,41-2011,"]
    (tmp_path / "p.csv").write_text("\n".join(rows) + "\n")
    t = occupation_titles(tmp_path / "p.csv")
    assert t.title.tolist() == ["Cashier", "Store Clerk"]          # pay variants grouped; singletons dropped
    assert t.postings.tolist() == [3, 2] and t.median_salary.iloc[0] == 31200
