# labor_jobs

Combines US Department of Labor (Bureau of Labor Statistics) employment data with
LinkedIn job postings, so you can compare where jobs are advertised against where
people actually work, and what postings pay against what BLS says the job pays.

## Data sources

| Source | What it gives | How it's fetched |
|---|---|---|
| [BLS OEWS](https://www.bls.gov/oes/tables.htm) (May 2024 by default) | Employment and median/mean annual wage for ~830 occupations, nationally and by state | Flat files downloaded automatically and cached in `data/cache/` |
| [BLS LAUS](https://www.bls.gov/lau/) | Unemployment rate per state for the months the postings were listed (April 2024 for the Kaggle data), weighted by each state's postings per month | BLS public API v2 |
| [BLS Employment Projections](https://www.bls.gov/emp/) (2025–35) | Ten-year projected growth, annual openings and typical education for each occupation | `occupation.xlsx` downloaded automatically |
| [BLS JOLTS](https://www.bls.gov/jlt/) | Monthly job openings since 2001 for 17 industries and every state | BLS public API v2 (needs a free API key) |
| [DOL H-1B / LCA disclosure data](https://www.dol.gov/agencies/eta/foreign-labor/performance) (fiscal year 2024) | Offered wages for ~540,000 sponsored jobs, with the employer, job title and detailed O*NET code | Four quarterly files downloaded automatically (~335 MB, read once into a slim cache) |
| [BLS OEWS metro areas](https://www.bls.gov/oes/tables.htm) (May 2024) | Employment and wages by occupation for ~390 metropolitan areas | `oesm24ma.zip` downloaded automatically |
| [Census metro delineations](https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html) (2023) | Which counties and principal cities make up each metro area | Downloaded automatically |
| [Adzuna API](https://developer.adzuna.com/) | Current US job postings with advertised salaries | Separate fetch command (needs a free key), see below |
| [Kaggle LinkedIn Job Postings](https://www.kaggle.com/datasets/arshkon/linkedin-job-postings) | ~124k US postings from 2023–24: title, company, location, salary | You download it once (see below) |
| [O*NET](https://www.onetcenter.org/database.html) | ~55k job titles, each mapped to an occupation code | Downloaded automatically |

### How the join works

BLS counts jobs by **SOC occupation code** (e.g. `15-1252 Software Developers`).
LinkedIn postings only have free-text titles ("Sr. Software Engineer - Payments").
The program normalizes each title (expands abbreviations like `Sr`/`RN`, drops seniority
words, strips suffixes after `-` or `,`) and looks it up in O*NET's occupation, alternate
and reported title lists. Matching runs in tiers:

1. **override**: the title is in `labor_jobs/title_overrides.csv`, a built-in list of
   corrections for common titles that O*NET files somewhere misleading. A `-` there means
   the title is too vague to match at all ("Operations Specialist").
2. **exact**: the normalized title is a known O*NET title. When a title is listed under
   several occupations, the program picks the one it appears under most often, then the
   one with the most BLS employment.
3. **employer**: the title is one that at least 3 employers used in H-1B filings, with one
   occupation code on at least 60% of them. Lawyers assign these codes because they set
   the minimum legal wage, which makes them a good source of real-world titles
   ("Director of Nursing", "Product Counsel", "Azure DevOps Engineer"). Internal codes
   like "JC60" are stripped first.
4. **keyword**: a few unambiguous patterns in long ad-style titles. For now this means
   truck-driver ads that mention CDL, Class A or OTR driving.
5. **technology**: one or more technology names followed by a role, such as "ServiceNow
   Developer", "Power BI Developer" or "Ruby on Rails Engineer". Technology names come from
   O*NET's list of 8,775 tools plus a few modern ones it lacks (Snowflake, Databricks).
   Developer and engineer map to Software Developers, administrator to Systems
   Administrators, architect to Computer Systems Engineers/Architects, and analyst,
   consultant or specialist to Computer Systems Analysts.
6. **partial**: dropping up to two leading words gives a known title
   ("Assistant Store Manager" becomes "Store Manager"), or dropping short trailing
   abbreviations does ("Occupational Therapist OT"). It never falls back to a lone
   generic word like "Manager".
7. **fuzzy** (off by default): similarity matching for typos.

### Niche and specialized occupations

Titles are matched to O*NET's 1,016 occupations, not just BLS's ~830. O*NET splits some
BLS occupations into specialized roles, such as Bioinformatics Scientists, Clinical Research
Coordinators, Validation Engineers, Penetration Testers and Blockchain Engineers. These keep
their own rows in `detailed_occupations.csv`. BLS employment, wages and projections
exist only for the broader BLS occupation, shown alongside.

O*NET also lists generic titles like "Sales Representative" under narrow specialized
occupations such as Solar Sales Representatives. To avoid sending generic titles there,
a title goes to a specialized occupation only when the BLS occupation's general code
doesn't list it, only one specialized occupation lists it, and the title shares a
distinctive word with the occupation's name.

Each occupation also gets:

- **Job Zone** (1–5): O*NET's rating of the preparation needed. 4 means considerable
  preparation (usually a bachelor's degree plus experience); 5 means extensive
  (often a graduate degree).
- **Top skills**: O*NET's five most important skills for it.
- **Hot technologies**: tools O*NET says employers often ask for in this occupation.
- **Technologies in postings**: the share of the occupation's postings whose description
  mentions each of O*NET's 172 "hot" technologies, such as "Python 78%, PyTorch 36%".
  Names are matched case-sensitively, and only in the forms used in postings ("AWS",
  "Power BI"). Product names that are also everyday words ("Word", "Access") only count
  with their vendor ("Microsoft Word"). Facebook and TikTok are skipped because they mostly
  appear as company social media links.

States are parsed from the posting's location (`Austin, TX`, `Texas, United States`) or its
FIPS code.

On the Kaggle data this maps 65% of postings to 828 distinct occupations, including
2,814 postings in specialized O*NET occupations. Hand spot-checks of random samples found
roughly 85–90% of exact matches, about 90% of employer-title matches, about 70% of partial
matches and about 85% of specialized matches to be the right occupation.

### Salaries and metro areas

- **H-1B offered wages** are summarized per occupation (filings, median offer, middle-50%
  range, share at the senior wage level, top sponsoring employers) in `occupations.csv`,
  `detailed_occupations.csv` and, by state, `state_occupations.csv`. They give salary
  figures for specialized roles BLS doesn't report, such as Financial Quantitative
  Analysts or Bioinformatics Scientists. They only cover visa-sponsored jobs, which lean
  towards tech, research, finance and medicine, and must be at least the local prevailing
  wage. Only the job, employer, worksite and wage columns are read. The file's contact
  names, emails and phone numbers are never loaded or written out.
- **Metro areas**: postings are placed in a metropolitan area by county (FIPS code, or the
  county name and state Adzuna gives), or else by matching "City, ST" to a metro's
  principal cities (74% of Kaggle postings). Each
  metro is compared with BLS employment and wages in `metros.csv`, and each metro and
  occupation pair in `metro_occupations.csv`.

### Fresh postings from Adzuna

Register for a free key at https://developer.adzuna.com/, then fetch recent postings and
run the tool on them. Each run adds to the postings history, so after three months of
fetches the report starts showing rising and falling occupations. History is kept per
feed (`adzuna` or `linkedin`; set another name with `--history-feed`), and trends only
compare months from the same feed. Different job sites carry different mixes of jobs, so
mixing them would show the difference between sites rather than change over time.

Adzuna carries many gig-app recruitment ads ("Drive with DoorDash", "Shop, Deliver, Earn
Cash - Instacart"). These recruit independent contractors, whom BLS employment data
doesn't cover, so they are counted separately as gig ads instead of matched to an
occupation. Requests that hit a temporary Adzuna server error are retried, and a page that
keeps failing is skipped.

```bash
export ADZUNA_APP_ID=... ADZUNA_APP_KEY=...
python -m labor_jobs.adzuna --pages 200 --out data/adzuna/2026-10.csv
python -m labor_jobs --postings data/adzuna/2026-10.csv
```

Each page is one API call of up to 50 postings. By default the pages are a sample. They are
split across Adzuna's 30 job categories in proportion to how many postings each has, then
across states in proportion to their BLS employment, at random positions in each search's
results. Adzuna serves at most 99 pages per search, so a date-sorted search only reaches the
newest few thousand postings, mostly from whichever employers posted in the last few hours.
Sampling this way spreads postings across the whole `--max-days-old` window (31 days by
default). `--newest` takes the newest pages instead, `--seed` makes a sample repeatable, and
`--what "data scientist"` or `--where Texas` narrow the search. Sizing the categories costs
30 small calls per run. Adzuna estimates salaries
for postings that don't state one; those estimates are dropped. Adzuna descriptions are
short snippets, so technology counts from them undercount.

## Looking ahead

The program adds three forward-looking pieces:

1. **BLS ten-year outlook.** Each occupation gets BLS's projected employment growth
   and projected openings per year (from growth plus people retiring or changing jobs).
   These are BLS's own forecasts, joined on the occupation code.
2. **Job openings forecast.** Monthly JOLTS openings by industry and state are fitted
   with damped-trend exponential smoothing and forecast `--forecast-months` ahead (default 6),
   with an 80% range. The forecast is also re-run from 12 earlier starting points over the
   last two years and compared with what happened, against the "no change" baseline of
   assuming openings stay at their latest level. On the October 2026 data the model's
   6-month error was about 10.7% against 11.8% for no change. It beat the baseline for
   59% of industries and 82% of states. So treat the forecasts as rough guides. State
   data is published later than national data, so state forecasts start from an older month.
3. **Posting trends.** Every run adds its postings to `data/history/postings.sqlite`, once
   per job, under the month they were listed. Once 3 or more months each have at least 500
   postings, the report shows which occupations' share of postings is rising or falling.
   The Kaggle dataset covers a single month (April 2024), so this needs new postings files
   over time. Any CSV with `title` and `location` columns plus a date (`listed_time` in
   milliseconds, or `listed_date`, `posted_date` or `date`) works:

   ```bash
   python -m labor_jobs --postings exports/2026-10.csv
   python -m labor_jobs --postings exports/2026-09.csv exports/2026-10.csv
   ```

   Industries in JOLTS don't line up one-to-one with occupations, so the openings forecast
   is reported by industry and state, not by occupation.

## Setup

```bash
pip install -r requirements.txt

# Get the postings (about 170 MB zipped; extract postings.csv)
curl -L -o linkedin.zip https://www.kaggle.com/api/v1/datasets/download/arshkon/linkedin-job-postings
unzip linkedin.zip postings.csv -d data/linkedin
```

## Run

```bash
python -m labor_jobs --postings data/linkedin/postings.csv --contact-email you@example.com
```

`bls.gov` rejects anonymous downloads, so give it a contact email (or set
`BLS_CONTACT_EMAIL`). If downloads are still blocked, download the files in a browser
and point at them:

```bash
python -m labor_jobs --postings data/linkedin/postings.csv \
  --oews-state-file oesm24st.zip --oews-national-file oesm24nat.zip \
  --onet-dir db_29_3_text/
```

Useful options (`python -m labor_jobs --help` lists them all):

- `--oews-year 2025` uses newer BLS estimates. The default 2024 matches the posting dates.
- `--onet-version 30_0` uses a newer O*NET title list.
- `--bls-api-key KEY` (or `BLS_API_KEY`) raises API limits; [register free](https://data.bls.gov/registrationEngine/).
  Without a key, BLS allows 25 requests a day per IP address. On shared cloud networks that
  limit is often already used up.
- `--skip-unemployment`, `--skip-jolts` and `--skip-projections` leave those parts out.
- `--h1b-year 2025` uses a different fiscal year of H-1B filings. `--h1b-files` uses local
  copies, and `--skip-h1b` leaves them out.
- `--skip-metros` leaves out the metro-area comparison. `--oews-metro-file` uses a local copy.
- `--skip-posting-skills` skips scanning descriptions for technologies, which saves about
  a minute on the full Kaggle file.
- `--forecast-months 12` forecasts further ahead. Accuracy drops the further out you go.
- `--jolts-start-year 2010` fits the forecast on less history.
- `--history other.sqlite` keeps postings history in a different file. `--no-history` turns history off.
- `--title-map fixes.csv` takes a CSV with `title,soc_code` columns, applied on top of the
  built-in corrections. Use `-` as the code to leave a title unmatched.
- `--fuzzy-cutoff 95` turns on fuzzy matching for typos. Spot checks found about half its
  matches wrong, which is why it's off by default.
- `--min-salary-samples 20` sets how many salaried postings an occupation needs before
  its salary gap is reported.

## Output (`output/`)

| File | Contents |
|---|---|
| `report.md` | Coverage stats, top occupations, over/under-represented occupations, salary gaps, states |
| `occupations.csv` | Per SOC code: postings, BLS employment, share of each, **demand index**, posted vs BLS median salary, BLS projected growth and annual openings |
| `state_occupations.csv` | Same, per state × occupation, plus postings per 1,000 employed |
| `states.csv` | Per state: postings, BLS total employment, postings per 1,000 employed, unemployment rate, latest JOLTS openings and forecast |
| `postings_with_soc.csv` | Every posting with its state, annual salary, O*NET and SOC codes, match method/score, and technologies mentioned |
| `detailed_occupations.csv` | Per O*NET occupation, including specialized ones: postings, posted salary, Job Zone, top skills, O*NET hot technologies, technologies mentioned in postings, plus BLS figures for the parent occupation |
| `metros.csv` | Per metro area: postings, BLS employment, postings per 1,000 employed, posted vs BLS median wage |
| `metro_occupations.csv` | Per metro and occupation: postings, posted salary, BLS metro employment and median wage |
| `unmatched_titles.csv` | Most common titles that couldn't be mapped. Use these to build a `--title-map` |
| `coverage.json` | Counts at each step of the join |
| `jolts_forecast.csv` | Every industry and state openings forecast, month by month, with 80% ranges |
| `jolts_backtest.csv` | Accuracy check: forecast error vs the no-change baseline, per series and months ahead |
| `posting_trends.csv` | Rising and falling occupations, once 3+ months of postings are recorded |

**Demand index** = (occupation's share of postings) ÷ (occupation's share of national
employment). Above 1 means the occupation shows up in LinkedIn postings more than its
share of the workforce would predict.

## Caveats

- LinkedIn over-represents professional and tech roles. Many retail, food-service and
  trades jobs are posted elsewhere, so a low demand index doesn't mean low demand.
- Only 29% of postings list a salary. Salary gaps need at least `--min-salary-samples` (default 20).
- BLS top-codes very high wages (shown as `#`). These become blank.
- Title matching is imperfect. Before relying on a specific occupation's numbers, check
  which titles were mapped to it in `postings_with_soc.csv`.

## Tests

```bash
pip install pytest
python -m pytest
```
