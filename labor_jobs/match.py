"""Find occupations and job postings that fit a resume or a college major.

    python -m labor_jobs.match --resume data/resume.pdf --results output/adzuna-2026-10
    python -m labor_jobs.match --major "Mechanical Engineering" --state TX CA

Works from the files a main run wrote (postings_with_soc.csv,
detailed_occupations.csv), so run `python -m labor_jobs` on a postings file
first. A resume can be PDF, Word (.docx) or plain text. It is read in memory
only; outputs list what was found in it (job titles, technologies, degree,
major), never the resume text.

Each occupation gets a fit score from up to three signals:
  title  the resume lists a job title that maps to the occupation
  major  the college major leads to it, per the NCES/BLS CIP-SOC crosswalk
  skills technologies on the resume that the occupation's postings ask for
          (or O*NET lists for it)
Occupations that typically need a higher degree than the person has score
half.

Each posting in the best-fitting occupations is then checked against what it
states it requires:
  skills      share of the technologies its description names that the
              resume has
  experience  years it asks for ("5+ years of experience"), or what its title
              implies ("Senior" ~5), against years worked on the resume
  education   the degree it requires (allowing "or equivalent experience"), or
              the occupation's typical entry education, against the resume's
  occupation  the occupation fit above
Each check is meets, close, short or not stated. A posting whose stated
checks all meet (at least two of them) is a Strong match; two or more
shortfalls, or one with nothing met, is a Stretch; the rest are Partial
matches. This is a checklist of stated
requirements, not a prediction of being hired: no data here says who got
which job.
"""

import argparse
import math
import os
import re
from pathlib import Path

import pandas as pd

from . import (bls, h1b, job_requirements, majors, occupation_text, personality, posting_requirements, requirements,
               skills)
from .matching import build_matcher
from .report import table
from .soc_mapping import load_onet_titles, load_title_overrides, normalize_title

WEIGHTS = {"title": 3.0, "duties": 3.0, "major": 1.0, "skills": 1.5, "credentials": 2.0, **personality.WEIGHTS}
# A credential required by this share of an occupation's postings (percent) counts fully.
CREDENTIAL_FULL = 30
# A major leading to this many occupations or fewer gives full credit to each.
MAJOR_SPECIFIC = 6
CONFIDENT_METHODS = {"exact", "override", "employer", "technology", "keyword", "partial"}
EDUCATION_CHOICES = {"none": 0, "associate": 2, "bachelor": 3, "master": 4, "doctoral": 5}


def read_document(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if suffix == ".docx":
        import docx
        document = docx.Document(path)
        cells = [c.text for t in document.tables for row in t.rows for c in row.cells]
        return "\n".join([p.text for p in document.paragraphs] + cells)
    return path.read_text(errors="ignore")


# Phrases ending like this name an employer or school, not a job
# ("Lincoln High School", "Mercy Hospital", "Acme Retail Co.").
ORGANIZATION_END = re.compile(
    r"(?i)\b(?:school|university|college|academy|institute|hospital|clinic|center|centre|inc|llc|llp|ltd|corp|"
    r"corporation|company|co|group|bank|agency|department|associates|partners|foundation|church|district)\.?$")


def title_candidates(text, technologies=()):
    """Short phrases that could be job titles: lines and parts of lines
    split at separators, 1-7 words, without long numbers (dates, phones).
    Skill lists (lines with several commas) and bare technology names are
    left out."""
    tech = {normalize_title(t) for t in technologies}
    out = []
    for line in text.splitlines():
        # Skill lists, and bullets, which describe duties rather than name jobs.
        if line.count(",") >= 3 or re.match(r"\s*[-•*·▪●◦–]\s", line):
            continue
        for part in re.split(r"\s+[|–—-]\s+|\s+at\s+|,|\t|\s{2,}", line):
            part = part.strip(" •*·:")
            words = part.split()
            if 1 <= len(words) <= 7 and re.search(r"[A-Za-z]{3}", part) \
                    and not re.search(r"\d{3,}|@|www\.|https?:", part) \
                    and normalize_title(part) not in tech \
                    and not ORGANIZATION_END.search(part):
                out.append(part)
    return list(dict.fromkeys(out))


def resume_profile(text, matcher, technologies, major_matcher, text_model=None):
    """What the resume says, as plain data (no resume text kept). With a
    text_model, also how closely its wording matches each occupation's tasks."""
    aliases = skills.resume_aliases(technologies)
    candidates = pd.Series(title_candidates(text, [r["alias"] for r in aliases]), dtype=object)
    titles = {}
    if len(candidates):
        mapped = matcher.map_titles(candidates)
        for phrase, row in zip(candidates, mapped.itertuples()):
            if row.match_method in CONFIDENT_METHODS and isinstance(row.onet_code, str):
                titles.setdefault(row.onet_code, phrase)
    techs = skills.resume_technologies(text, aliases)
    level, label = majors.education_level(text)
    return {"titles": titles, "technologies": techs,
            "majors": majors.majors_in_text(text), "education_level": level,
            "education": label, "years": requirements.resume_years(text),
            # Words of the resume, for comparing posting titles; not written to any output.
            "words": set(normalize_title(text).split()),
            "text_similarity": text_model.similarity(text) if text_model else {},
            "credentials": posting_requirements.credentials_in(text)}


def _tech_names(value):
    """"Python 78%, SQL 35%" or "Python, SQL" -> {"Python", "SQL"}."""
    if not isinstance(value, str):
        return set()
    return {re.sub(r"\s+\d+%$", "", t.strip()) for t in value.split(",") if t.strip()}


def _shares(value):
    """"Name 42%; Name 30%" -> {"Name": 42.0, "Name": 30.0}."""
    if not isinstance(value, str):
        return {}
    out = {}
    for part in value.split("; "):
        name, _, pct = part.rpartition(" ")
        if name and pct.endswith("%"):
            out[name] = float(pct[:-1])
    return out


def _value(occ, name):
    v = getattr(occ, name, None)
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def requirement_adjustments(occ, profile):
    """(multiplier, reasons) from what employers require for the occupation,
    against the resume: education (the share of jobs needing more than the
    resume's highest degree), prior experience, and licenses most postings
    require that the resume doesn't show."""
    factor, why = 1.0, []
    level = profile.get("education_level")
    row = {f"edu_{lv}": _value(occ, f"edu_{lv}") for lv in range(6)}
    more = job_requirements.share_requiring_more(row, level) if level is not None else None
    if more is not None:
        factor *= 1 - 0.5 * more / 100
        if more >= 30:
            why.append(f"{more:.0f}% of jobs need more than {job_requirements.LEVEL_NAMES[level]}")
    else:
        needs = majors.BLS_EDUCATION.get(_value(occ, "typical_education"))
        # Only a degree: certificates and training (a CDL, an apprenticeship)
        # don't show up as an education level on a resume.
        if needs is not None and needs >= 2 and level is not None and needs > level:
            factor *= 0.5
            why.append(f"usually needs: {occ.typical_education}")
    years, need, pct = profile.get("years"), _value(occ, "exp_years"), _value(occ, "exp_pct")
    if profile.get("from_resume") and years is not None and need and pct and years < need:
        factor *= 1 - 0.3 * (pct / 100) * min(1.0, (need - years) / need)
        if pct >= 50:
            why.append(f"{pct:.0f}% of jobs want prior experience, typically {need:g}+ years")
    if profile.get("from_resume"):
        held = set(profile.get("credentials") or [])
        required = _shares(_value(occ, "credentials_required"))
        licensed = (_value(occ, "license_pct") or 0) >= 50
        hard = {c: v for c, v in required.items() if c not in posting_requirements.EASY_TO_GET}
        top = max(hard.items(), key=lambda kv: kv[1], default=None)
        if top and top[1] >= 20 and top[0] not in held and (licensed or top[1] >= 50):
            factor *= 0.85
            why.append(f"most jobs require {top[0]}, not on the resume")
    return factor, why


# What to rank occupations by, and how much (any scale; only the ratios matter).
RANK_BY = {"resume": 100, "personality": 40, "salary": 0, "outlook": 0}
RANK_PRESETS = {
    "balanced": {"resume": 100, "personality": 40, "salary": 30, "outlook": 20},
    "resume": {"resume": 100, "personality": 0, "salary": 0, "outlook": 0},
    "personality": {"resume": 20, "personality": 100, "salary": 0, "outlook": 0},
    "pay": {"resume": 50, "personality": 10, "salary": 100, "outlook": 0},
    "outlook": {"resume": 50, "personality": 10, "salary": 0, "outlook": 100},
}
RESUME_SIGNALS = ("title", "duties", "major", "skills", "credentials")
MARKET_GATE = 0.4


def _rank_pct(values):
    """{key: 0-1 percentile} of the numbers given, ignoring blanks."""
    s = pd.Series(values, dtype=float).dropna()
    return s.rank(pct=True).to_dict() if len(s) else {}


def market_scores(detailed):
    """Per O*NET code: (pay, salary score 0-1, growth %, outlook score 0-1).
    Pay is the BLS median wage, else the posted median when 20+ postings state
    one. Outlook mixes BLS projected 10-year growth (60%) and annual openings
    (40%), both ranked across occupations."""
    pay, growth, openings = {}, {}, {}
    for occ in detailed.itertuples(index=False):
        bls = _value(occ, "bls_median_wage")
        posted = _value(occ, "median_posted_salary") if (_value(occ, "salary_samples") or 0) >= 20 else None
        pay[occ.onet_code] = bls if bls else posted
        growth[occ.onet_code] = _value(occ, "proj_growth_pct")
        openings[occ.onet_code] = _value(occ, "proj_annual_openings")
    pay_pct, growth_pct, open_pct = _rank_pct(pay), _rank_pct(growth), _rank_pct(openings)
    out = {}
    for code in pay:
        g, o = growth_pct.get(code), open_pct.get(code)
        outlook = (0.6 * g + 0.4 * o) if g is not None and o is not None else g if g is not None else o
        out[code] = (pay[code], pay_pct.get(code), growth[code], outlook)
    return out


def score_occupations(detailed, profile, major_socs, rank_by=None, requirements=True):
    """Fit score 0-1 per occupation in the results, with the reasons. Four signals:
    title    a job title on the resume names the occupation; the first (latest)
             counts fully, earlier ones a little less
    duties   the resume's wording against the occupation's O*NET tasks, scaled
             so the closest occupation is 1
    major    the major's college program leads to it, counting less when the
             program leads to many occupations
    skills   technologies the occupation's postings ask for, rare ones counting
             more than ones nearly every occupation lists (Excel)
    credentials  licenses and certifications on the resume that the occupation's
             postings require (only when the resume shows any)
    and, from personality.trait_signals when test results are given, interests
    (Holland/RIASEC), work styles (Big Five, blended with the soft skills its
    postings ask for) and, weakly, Myers-Briggs. Then requirement_adjustments
    lowers occupations whose jobs mostly need more education, experience or a
    license than the resume shows.

    The resume signals average into resume_fit, the test signals into
    personality_fit; fit_score weighs those with salary_score and
    outlook_score (market_scores) by rank_by (RANK_BY by default), counting
    only what is known for each occupation. requirements=False skips the
    requirement adjustment."""
    held = list(profile["titles"].items())
    recency = {code: max(0.6, 1 - 0.2 * i) for i, (code, _) in enumerate(held)}
    held_socs = {}
    for code, r in recency.items():
        held_socs[code[:7]] = max(held_socs.get(code[:7], 0.0), r)
    similarity = profile.get("text_similarity") or {}
    best_text = max((similarity.get(c, 0.0) for c in detailed["onet_code"]), default=0.0)
    occ_tech = {occ.onet_code: _tech_names(getattr(occ, "posting_technologies", None))
                or _tech_names(getattr(occ, "onet_hot_technologies", None))
                for occ in detailed.itertuples(index=False)}
    tech_df = {}
    for techs in occ_tech.values():
        for t in techs:
            tech_df[t] = tech_df.get(t, 0) + 1
    resume_tech = set(profile["technologies"])
    tech_weight = {t: math.log(len(occ_tech) / tech_df.get(t, 1)) for t in resume_tech}
    tech_total = sum(sorted(tech_weight.values(), reverse=True)[:5]) or 1.0
    major_credit = min(1.0, math.sqrt(MAJOR_SPECIFIC / len(major_socs))) if major_socs else 0.0
    # Every signal the person provided counts for every occupation (as 0
    # where it doesn't match), so occupations aren't scored on fewer signals.
    traits = profile.get("traits") or {}
    trait_keys = sorted({k for t in traits.values() for k in t["signals"]})
    held_credentials = set(profile.get("credentials") or [])
    # How specific each credential is: required by few occupations counts more
    # (a bar admission) than one many require (a driver's license, CPR).
    listing = {}
    for value in getattr(detailed, "credentials_required", pd.Series(dtype=object)):
        for c in _shares(value):
            listing[c] = listing.get(c, 0) + 1
    n_listed = max(1, sum(1 for v in getattr(detailed, "credentials_required", []) if _shares(v)))
    specificity = {c: math.log(n_listed / k) / math.log(n_listed) if n_listed > 1 else 1.0 for c, k in listing.items()}
    available = [k for k, present in (("title", bool(held)), ("duties", best_text > 0),
                                      ("major", bool(major_socs)), ("skills", bool(resume_tech)),
                                      ("credentials", bool(held_credentials))) if present]
    available += trait_keys
    rank_by = {**RANK_BY, **(rank_by or {})}
    market = market_scores(detailed)
    if not available and not (rank_by["salary"] or rank_by["outlook"]):
        return detailed.iloc[0:0].assign(fit_score=[], why=[], skills_to_add=[])
    rows = []
    for occ in detailed.itertuples(index=False):
        signals, why = dict.fromkeys(available, 0.0), []
        if occ.onet_code in recency:
            signals["title"] = recency[occ.onet_code]
            why.append(f"held title: {profile['titles'][occ.onet_code]}")
        elif occ.soc_code in held_socs:
            signals["title"] = 0.6 * held_socs[occ.soc_code]
            why.append("held a title in the same occupation")
        if "duties" in available:
            signals["duties"] = min(1.0, similarity.get(occ.onet_code, 0.0) / best_text)
            if signals["duties"] >= 0.5:
                why.append("duties match its tasks")
        if major_socs and occ.soc_code in major_socs:
            signals["major"] = major_credit
            why.append("fits the major")
        if resume_tech:
            overlap = sorted(resume_tech & occ_tech[occ.onet_code])
            signals["skills"] = min(1.0, sum(tech_weight[t] for t in overlap) / tech_total)
            if overlap:
                why.append("skills: " + ", ".join(overlap[:6]))
        asked = _shares(_value(occ, "credentials_required"))
        preferred = _shares(_value(occ, "credentials_preferred"))
        # Only occupations whose postings name credentials are scored on them.
        scored = [k for k in available if k != "credentials" or asked or preferred]
        if held_credentials and (asked or preferred):
            best = max(((min(1.0, (asked.get(c, 0) + 0.5 * preferred.get(c, 0)) / CREDENTIAL_FULL)
                         * specificity.get(c, 0.0), c) for c in held_credentials), default=(0, None))
            signals["credentials"] = best[0]
            best = (asked.get(best[1], 0) + 0.5 * preferred.get(best[1], 0), best[1]) if best[1] else (0, None)
            if best[0] >= 10:
                why.append(f"holds {best[1]} ({asked.get(best[1], 0):.0f}% of postings require it)")
        trait = traits.get(occ.onet_code, {"signals": {}})
        for k in trait_keys:
            signals[k] = trait["signals"].get(k, 0.0)
        if trait["signals"].get("interests", 0) >= 0.7 or trait["signals"].get("mbti", 0) >= 0.7:
            why.append(f"interests match ({trait['holland']})")
        if trait["signals"].get("styles", 0) >= 0.7:
            why.append("personality suits its work styles")

        def average(keys):
            keys = [k for k in scored if k in keys]
            return sum(WEIGHTS[k] * signals[k] for k in keys) / sum(WEIGHTS[k] for k in keys) if keys else None
        resume_fit = average(RESUME_SIGNALS)
        personality_fit = average(trait_keys)
        pay, salary_score, growth, outlook_score = market.get(occ.onet_code, (None, None, None, None))
        parts = {"resume": resume_fit, "personality": personality_fit, "salary": salary_score,
                 "outlook": outlook_score}
        used = {k: v for k, v in parts.items() if v is not None and rank_by[k] > 0}
        # Pay and outlook count for occupations the person fits: fully from a
        # fit of MARKET_GATE up, proportionally below (none at all only when
        # there's nothing personal to rank by).
        personal = [v for k, v in used.items() if k in ("resume", "personality")]
        if personal:
            gate = min(1.0, max(personal) / MARKET_GATE)
            used = {k: v * gate if k in ("salary", "outlook") else v for k, v in used.items()}
        fit = sum(rank_by[k] * v for k, v in used.items()) / sum(rank_by[k] for k in used) if used else 0.0
        if rank_by["salary"] and salary_score is not None and salary_score >= 0.75:
            why.append(f"pays well: median ${pay:,.0f}, above {salary_score:.0%} of occupations")
        if rank_by["outlook"] and outlook_score is not None and outlook_score >= 0.75 and growth is not None:
            why.append(f"strong outlook: {growth:+.1f}% projected growth")
        if requirements:
            factor, reasons = requirement_adjustments(occ, profile)
            fit *= factor
            why += reasons
        missing = sorted(_tech_names(getattr(occ, "posting_technologies", None)) - resume_tech)
        rows.append({"onet_code": occ.onet_code, "fit_score": round(fit, 3),
                     "resume_fit": None if resume_fit is None else round(resume_fit, 3),
                     "personality_fit": None if personality_fit is None else round(personality_fit, 3),
                     "pay": pay, "salary_score": None if salary_score is None else round(salary_score, 3),
                     "outlook_score": None if outlook_score is None else round(outlook_score, 3),
                     "why": "; ".join(why), "skills_to_add": ", ".join(missing[:6]),
                     "holland_code": trait.get("holland"), "reasoning_demand": trait.get("reasoning")})
    scores = pd.DataFrame(rows, columns=["onet_code", "fit_score", "resume_fit", "personality_fit", "pay",
                                         "salary_score", "outlook_score", "why", "skills_to_add", "holland_code",
                                         "reasoning_demand"])
    out = detailed.merge(scores, on="onet_code")
    out = out[out["fit_score"] > 0]
    return out.sort_values(["fit_score", "postings"], ascending=False)


RELEVANCE_STOP = {"and", "of", "the", "for", "to", "in", "with", "at", "on", "or", "&"}
# Words for the same job, compared as one.
SAME_WORD = {"counsel": "attorney", "lawyer": "attorney", "solicitor": "attorney"}


def _same_words(words):
    """Words with synonyms merged; "intellectual property" counts as "ip"."""
    words = {SAME_WORD.get(t, t) for t in words} - RELEVANCE_STOP
    if {"intellectual", "property"} <= words:
        words = (words - {"intellectual", "property"}) | {"ip"}
    return words


def _relevance_words(text):
    return _same_words(normalize_title(text).split())


def title_relevance(titles, resume_words):
    """For each posting title, the share of its words that the resume also
    uses, each word weighted by how rare it is among these titles. So for a
    patent attorney's resume, "Patent Counsel" scores far above "Family Law
    Attorney": every title has "attorney" or "counsel", few have "patent"."""
    resume_words = _same_words(resume_words) | ({"ip"} if {"intellectual", "property"} <= set(resume_words) else set())
    tokens = [_relevance_words(t) for t in titles]
    counts = {}
    for tok in tokens:
        for t in tok:
            counts[t] = counts.get(t, 0) + 1
    n = len(tokens)
    weight = {t: math.log(1 + n / c) for t, c in counts.items()}
    # Damping by the weight of a word every title has, so a title sharing a
    # rare word ("Patent Counsel") beats one sharing only a common one ("Attorney").
    damping = math.log(2)
    out = []
    for tok in tokens:
        total = sum(weight[t] for t in tok)
        shared = sum(weight[t] for t in tok if t in resume_words)
        out.append(round(shared / (total + damping), 3) if total else 0.0)
    return out


# Ratings order postings only a little, so a closely related posting with a
# gap still comes before an unrelated one that meets everything.
RATING_BONUS = {"Strong match": 0.3, "Partial match": 0.15, "Stretch": 0.0}


def rank_postings(postings, occupations, profile, top_occupations, states=None, metro=None,
                  metro_names=None, min_salary=None, rank_by=None, sort="match"):
    """Postings in the best-fitting occupations, checked against the resume.
    sort="match" orders them by fit, title relevance, skills and rating, plus
    their own salary (ranked among these postings) in proportion to the salary
    weight in rank_by; sort="salary" puts the highest advertised pay first."""
    best = occupations.head(top_occupations)
    fit = dict(zip(best["onet_code"], best["fit_score"]))
    names = dict(zip(best["onet_code"], best["onet_title"]))
    df = postings[postings["onet_code"].isin(fit)].copy()
    if states:
        df = df[df["state"].isin([s.upper() for s in states])]
    if metro and "metro" in df and metro_names is not None:
        wanted = {code for code, name in metro_names.items() if metro.lower() in str(name).lower()}
        df = df[df["metro"].astype(str).str.replace(r"\.0$", "", regex=True).isin(wanted)]
    if min_salary:
        df = df[df["annual_salary"] >= min_salary]
    resume_tech = set(profile["technologies"])
    posting_tech = df.get("technologies", pd.Series("", index=df.index)).fillna("") \
        .map(lambda s: {t.strip() for t in s.split(";") if t.strip()})
    df["matched_skills"] = posting_tech.map(lambda t: ", ".join(sorted(t & resume_tech)))
    df["missing_skills"] = posting_tech.map(lambda t: ", ".join(sorted(t - resume_tech)))
    coverage = posting_tech.map(lambda t: len(t & resume_tech) / len(t) if t else 0.0)
    df["occupation"] = df["onet_code"].map(names)
    df["title_relevance"] = title_relevance(df["title"], profile.get("words", set())) if len(df) else []
    df["match_score"] = (df["onet_code"].map(fit) + 0.5 * coverage + df["title_relevance"]).round(3)
    education = dict(zip(best["onet_code"], best.get("typical_education", pd.Series(dtype=object))))
    experience = dict(zip(best["onet_code"], zip(best.get("exp_years", pd.Series([None] * len(best))),
                                                 best.get("exp_pct", pd.Series([None] * len(best))))))
    checks = [posting_checks(row, posting_tech[i], profile, fit.get(row.onet_code),
                             education.get(row.onet_code), experience.get(row.onet_code))
              for i, row in zip(df.index, df.itertuples(index=False))]
    df = df.join(pd.DataFrame(checks, index=df.index))
    df["rank_score"] = df["match_score"] + df["rating"].map(RATING_BONUS).fillna(0)
    weights = {**RANK_BY, **(rank_by or {})}
    share = weights["salary"] / (sum(weights.values()) or 1)
    if share and len(df):
        df["rank_score"] += 2 * share * df["annual_salary"].rank(pct=True).fillna(0)
    order = ["annual_salary", "rank_score"] if sort == "salary" else ["rank_score", "annual_salary"]
    return df.sort_values(order, ascending=[False, False], na_position="last").drop(columns="rank_score")


RATINGS = ("Strong match", "Partial match", "Stretch")
RATING_ORDER = {r: i for i, r in enumerate(RATINGS)}


def _grade(have, need, close):
    if have >= need:
        return "meets"
    return "close" if have >= close else "short"


def posting_checks(row, posting_tech, profile, occupation_fit, occupation_education, occupation_experience=None):
    """The checks for one posting (skills, experience, education, credentials,
    occupation), their descriptions, and the rating. occupation_experience is
    (typical years, percent of jobs requiring experience) from ORS/O*NET, used
    when the posting states no years."""
    resume_tech = set(profile["technologies"])
    out = {}
    # Skills the posting names.
    if posting_tech and profile.get("from_resume"):
        share = len(posting_tech & resume_tech) / len(posting_tech)
        out["skills_check"] = "meets" if share >= 0.6 else ("close" if share >= 0.3 else "short")
        out["skills_detail"] = f"{len(posting_tech & resume_tech)} of {len(posting_tech)} listed"
    else:
        out["skills_check"], out["skills_detail"] = "not stated", "no technologies listed"             if not posting_tech else "no resume"
    # Experience.
    need = getattr(row, "years_required", None)
    need = None if need is None or pd.isna(need) else float(need)
    basis = "asked"
    if need is None:
        need, label = requirements.seniority(row.title)
        basis = label.lower() if label else None
    if need is None and occupation_experience and occupation_experience[0] and (occupation_experience[1] or 0) >= 50:
        need, basis = float(occupation_experience[0]), "typical for the occupation"
    have = profile.get("years")
    if need is None or have is None:
        out["experience_check"] = "not stated"
        out["experience_detail"] = "no years asked" if need is None else "no dates on resume"
    else:
        out["experience_check"] = _grade(have, need, max(need - 1, need * 0.75))
        asked = (f"{need:g}+ yrs {basis}" if basis == "asked" else
                 f"~{need:g}+ yrs typical for the occupation" if basis == "typical for the occupation" else
                 f"{basis} (~{need:g}+ yrs)")
        out["experience_detail"] = f"{asked}; resume shows {have:g}"
    # Education.
    level = getattr(row, "degree_required", None)
    flexible = bool(getattr(row, "degree_flexible", False)) and not pd.isna(getattr(row, "degree_flexible", False))
    source = "posting"
    if level is None or pd.isna(level):
        level, source = majors.BLS_EDUCATION.get(occupation_education), "typical"
    has = profile.get("education_level")
    if level is None or has is None:
        out["education_check"], out["education_detail"] = "not stated", "no degree stated"
    else:
        level = int(level)
        name = requirements.DEGREE_NAMES.get(level, occupation_education or "degree")
        check = "meets" if has >= level else ("close" if flexible or has == level - 1 else "short")
        out["education_check"] = check
        out["education_detail"] = (f"{name} required" + (" or equivalent experience" if flexible else "")
                                   if source == "posting" else f"{name} typical for the occupation")
    # Credentials the posting requires.
    required = [c for c in str(getattr(row, "credentials_required", "") or "").split("; ") if c and c != "nan"]
    if not required:
        out["credentials_check"], out["credentials_detail"] = "not stated", "none required"
    elif not profile.get("from_resume"):
        out["credentials_check"], out["credentials_detail"] = "not stated", "requires " + ", ".join(required)
    else:
        held = set(profile.get("credentials") or [])
        missing = [c for c in required if c not in held]
        hard = [c for c in missing if posting_requirements.CREDENTIALS[c][1] in ("license", "clearance")
                and c not in posting_requirements.EASY_TO_GET]
        out["credentials_check"] = "meets" if not missing else ("short" if hard else "close")
        out["credentials_detail"] = ("requires " + ", ".join(required) +
                                     ("; all on resume" if not missing else "; not on resume: " + ", ".join(missing)))
    # Occupation fit.
    if occupation_fit is None:
        out["occupation_check"] = "not stated"
    else:
        out["occupation_check"] = "meets" if occupation_fit >= 0.5 else ("close" if occupation_fit >= 0.25 else "short")
    grades = [out[k] for k in ("skills_check", "experience_check", "education_check", "credentials_check",
                               "occupation_check")]
    meets, close, short = grades.count("meets"), grades.count("close"), grades.count("short")
    if short == 0 and close == 0 and meets >= 2:
        out["rating"] = "Strong match"
    elif short >= 2 or (short and not meets):
        out["rating"] = "Stretch"
    else:
        out["rating"] = "Partial match"
    names = {"skills_check": "skills", "experience_check": "experience", "education_check": "education",
             "credentials_check": "credentials", "occupation_check": "occupation"}
    gaps = [names[k] for k in names if out[k] in ("short", "close")]
    out["rating_reason"] = ("meets what it states" if not gaps else
                            "check " + ", ".join(gaps))
    return out


def skills_to_add(postings, limit=10):
    """Technologies most often missing from the resume among the postings
    that are a strong or partial match, in occupations the resume fits well."""
    near = postings[postings["rating"].isin(RATINGS[:2]) & (postings["occupation_check"] == "meets")]
    counts = near["missing_skills"].fillna("").str.split(", ").explode()
    counts = counts[counts != ""].value_counts().head(limit)
    return counts.rename_axis("technology").reset_index(name="postings")


def _top(value, n):
    """The first n entries of a "Name 42%; Name 30%" list."""
    return "; ".join(str(value).split("; ")[:n]) if isinstance(value, str) and value else ""


def build_report(profile, major_text, major_hits, occupations, postings, results_dir, top):
    lines = ["# Job matches", ""]
    if profile.get("from_resume"):
        held = ", ".join(sorted(set(profile["titles"].values()))) or "none recognized"
        lines += ["## From the resume",
                  f"- Job titles recognized: {held}",
                  f"- Technologies: {', '.join(profile['technologies']) or 'none recognized'}",
                  f"- Licenses and certifications: {', '.join(profile.get('credentials') or []) or 'none recognized'}",
                  f"- Highest degree: {profile['education']}",
                  "- Years of work experience (from job dates): "
                  + (f"{profile['years']:g}" if profile.get("years") is not None else "no dates found"),
                  f"- Majors found: {', '.join(profile['majors']) or 'none'}",
                  "",
                  "The resume was read in memory only; nothing from it is saved beyond this list.",
                  ""]
    tests = profile.get("tests")
    if tests:
        lines += ["## From the test results"]
        if tests.get("riasec"):
            lines.append(f"- Holland interests: {personality.holland_code(tests['riasec'])} ("
                         + ", ".join(f"{personality.RIASEC_NAMES[k]} {v:g}" for k, v in tests["riasec"].items()) + ")")
        if tests.get("big_five"):
            big5 = ", ".join(f"{personality.BIG_FIVE_NAMES[k]} {v:.0%}" for k, v in tests["big_five"].items())
            lines.append(f"- Big Five: {big5}")
        if tests.get("mbti"):
            note = "ignored, since interest scores were given" if tests.get("riasec") else \
                "turned into rough interests and counted lightly: Myers-Briggs types predict job fit poorly"
            lines.append(f"- Myers-Briggs: {tests['mbti']} ({note})")
        if tests.get("iq"):
            lines.append(f"- IQ: {tests['iq']} ({personality.iq_band(tests['iq'])}). Shown as each "
                         "occupation's reasoning demand; it doesn't change the ranking.")
        lines.append("")
    if major_hits:
        lines += ["## Major", f"\"{major_text}\" was matched to: " +
                  "; ".join(f"{title} (CIP {code})" for code, title, _ in major_hits), ""]
    occupations = occupations.assign(employers_ask=[
        "; ".join(x for x in (_top(r.get("credentials_required"), 3), _top(r.get("soft_skills"), 3)) if x)
        for r in occupations.to_dict("records")])
    lines += [f"## Best-fitting occupations (from `{results_dir}`)",
              table(occupations.head(top), {
                  "onet_title": "Occupation", "fit_score": "Fit", "postings": "Postings",
                  "median_posted_salary": "Posted median $", "bls_median_wage": "BLS median $",
                  "proj_growth_pct": "Growth % (10 yr)", "typical_education": "Typical education",
                  "why": "Why", "skills_to_add": "Skills in postings not on resume",
                  "pay": "Median pay $",
                  "employers_ask": "Employers often ask for",
                  **({"holland_code": "Holland code", "reasoning_demand": "Reasoning demand"} if tests else {})}),
              "",
              "Fit (0 to 1) weighs held job titles, how closely described duties match each "
              "occupation's O*NET tasks, the major, and technologies, plus interests and "
              "personality when test results are given. Occupations that usually need a higher "
              "degree count half.",
              "",
              "## Postings checked against the resume",
              "Each posting's stated requirements against the resume. This is a checklist, "
              "not a prediction of being hired: no data here says who got which job, and "
              "employers weigh things a resume and a posting don't show. Experience counts "
              "the date ranges of jobs on the resume; education uses the posting's stated "
              "degree, or the occupation's typical one when the posting states none.",
              "",
              ", ".join(f"{r}: {int((postings['rating'] == r).sum()):,}" for r in RATINGS)
              if "rating" in postings else "",
              "",
              table(postings.head(top * 2), {
                  "rating": "Rating", "title": "Title", "company_name": "Company",
                  "location": "Location", "annual_salary": "Salary $",
                  "matched_skills": "Your skills it lists", "missing_skills": "Skills it lists you lack",
                  "experience_detail": "Experience", "education_detail": "Education",
                  "credentials_detail": "Credentials",
                  "rating_reason": "Look at", "url": "Link"}),
              ""]
    if "rating" in postings and len(postings):
        gaps = skills_to_add(postings)
        if len(gaps):
            lines += ["## Skills to add",
                      "Technologies most often listed by strong and partial matches, in occupations "
                      "the resume fits well, that the resume doesn't mention.",
                      "",
                      table(gaps, {"technology": "Technology", "postings": "Postings listing it"}),
                      ""]
    return "\n".join(lines)


def parse_rank_by(text):
    """A preset name or "resume=50,salary=100" -> weights for all four parts
    (any not given count 0 when weights are given)."""
    text = (text or "").strip().lower()
    if text in RANK_PRESETS:
        return dict(RANK_PRESETS[text])
    weights = dict.fromkeys(RANK_BY, 0.0)
    for key, value in re.findall(r"([a-z]+)\s*[=:]\s*(\d+(?:\.\d+)?)", text):
        if key not in weights:
            raise SystemExit(f"--rank-by: unknown part {key!r}; use resume, personality, salary or outlook")
        weights[key] = float(value)
    if not any(weights.values()):
        raise SystemExit("--rank-by: give a preset or at least one weight above 0")
    return weights


def _pairs(text):
    """"O=70, C=85" -> {"O": 70.0, "C": 85.0}."""
    return {k.strip().upper(): float(v) for k, v in re.findall(r"([A-Za-z])\s*[=:]\s*(\d+(?:\.\d+)?)", text or "")}


def read_tests(args):
    """Test results from a report file and/or the command line (which wins)."""
    tests = personality.parse_results(read_document(args.tests)) if args.tests else \
        {"riasec": None, "big_five": None, "mbti": None, "iq": None}
    if args.riasec:
        pairs = _pairs(args.riasec)
        tests["riasec"] = {k: pairs.get(k, 0.0) for k in personality.RIASEC} if len(pairs) >= 3 \
            else personality.riasec_from_code(args.riasec)
    if args.big_five:
        pairs = _pairs(args.big_five)
        if "N" in pairs:
            pairs["_neuroticism"] = pairs.pop("N")
        tests["big_five"] = personality.normalize_big_five(pairs)
    if args.mbti:
        tests["mbti"] = args.mbti.strip().upper()[:4]
    if args.iq:
        tests["iq"] = args.iq
    return tests


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.match",
                                description="Find occupations and postings that fit a resume or major.")
    p.add_argument("--resume", help="resume file (.pdf, .docx or .txt)")
    p.add_argument("--major", help='college major, e.g. "Computer Science" or "BS in Nursing"')
    p.add_argument("--education", choices=list(EDUCATION_CHOICES),
                   help="highest degree, if not on the resume (default: bachelor with --major alone)")
    p.add_argument("--results", default="output", help="output folder of a main run (default: output)")
    p.add_argument("--state", nargs="+", help="only postings in these states, e.g. TX CA")
    p.add_argument("--metro", help='only postings in metro areas whose name contains this, e.g. "Austin"')
    p.add_argument("--min-salary", type=float, help="only postings advertising at least this much a year")
    p.add_argument("--tests", help="a personality or interest test report (.pdf, .docx or .txt) to read "
                                   "RIASEC, Big Five, Myers-Briggs and IQ results from")
    p.add_argument("--riasec", help='Holland interests: a code like "SIA" or scores like '
                                    '"R=12,I=30,A=8,S=35,E=20,C=15"')
    p.add_argument("--big-five", help='Big Five percentiles, e.g. "O=70,C=85,E=30,A=60,N=40" (N = neuroticism)')
    p.add_argument("--mbti", help="Myers-Briggs type, e.g. INTJ (weak evidence; used lightly)")
    p.add_argument("--iq", type=int, help="IQ or cognitive score (shown against each job's reasoning demand; "
                                          "never changes the ranking)")
    p.add_argument("--rank-by", default="resume=100,personality=40",
                   help='what to rank occupations by and how much: a preset (' + ", ".join(RANK_PRESETS) +
                        ') or weights like "resume=50,personality=20,salary=100,outlook=30"')
    p.add_argument("--ignore-requirements", action="store_true",
                   help="don't lower occupations whose jobs mostly need more education, experience or a license")
    p.add_argument("--sort-postings", choices=["match", "salary"], default="match",
                   help="order postings by best match (default) or highest advertised salary")
    p.add_argument("--top", type=int, default=15, help="occupations to show (default 15)")
    p.add_argument("--out", help="folder for the match files (default: <results>/matches)")
    p.add_argument("--cache", default="data/cache")
    p.add_argument("--onet-version", default="29_3")
    p.add_argument("--onet-dir")
    p.add_argument("--oews-year", type=int, default=2024)
    p.add_argument("--h1b-year", type=int, default=2024)
    p.add_argument("--skip-h1b", action="store_true", help="don't use H-1B job titles for matching")
    p.add_argument("--contact-email", default=os.environ.get("BLS_CONTACT_EMAIL"))
    args = p.parse_args(argv)
    if not (args.resume or args.major or args.tests or args.riasec or args.big_five or args.mbti):
        p.error("give --resume, --major, test results (--tests, --riasec, --big-five, --mbti) or a mix")

    results = Path(args.results)
    out = Path(args.out) if args.out else results / "matches"
    out.mkdir(parents=True, exist_ok=True)
    postings = pd.read_csv(results / "postings_with_soc.csv", dtype={"onet_code": str, "soc_code": str},
                           low_memory=False)
    detailed = pd.read_csv(results / "detailed_occupations.csv", dtype={"onet_code": str, "soc_code": str})
    metro_names = None
    if (results / "metros.csv").exists():
        m = pd.read_csv(results / "metros.csv", dtype={"cbsa": str})
        metro_names = dict(zip(m["cbsa"], m["metro_name"]))

    user_agent = f"labor-jobs/1.0 ({args.contact_email})" if args.contact_email else "labor-jobs/1.0"
    cip_csv, crosswalk = majors.cip_files(args.cache, user_agent)
    major_matcher = majors.MajorMatcher(cip_csv, crosswalk)

    profile = {"titles": {}, "technologies": [], "majors": [], "education_level": 3,
               "education": "Bachelor's degree (assumed)"}
    if args.resume:
        profiles, technologies = skills.load_profiles(args.onet_version, args.cache, user_agent,
                                                      args.onet_dir)
        oews_national = bls.read_oews(bls.oews_file(args.cache, args.oews_year, "nat", user_agent))
        employer_titles = None
        if not args.skip_h1b:
            employer_titles = h1b.reference_titles(
                h1b.read_h1b(h1b.h1b_files(args.cache, args.h1b_year, user_agent)))
        matcher = build_matcher(oews_national,
                                load_onet_titles(args.onet_version, args.cache, user_agent, args.onet_dir),
                                profiles, technologies, load_title_overrides(),
                                employer_titles=employer_titles)
        text_model = occupation_text.TextModel(occupation_text.occupation_documents(
            args.onet_version, args.cache, user_agent, args.onet_dir))
        profile = resume_profile(read_document(args.resume), matcher, technologies, major_matcher, text_model)
        profile["from_resume"] = True
    if args.education:
        profile["education_level"] = EDUCATION_CHOICES[args.education]
        profile["education"] = args.education.title()

    # A given major wins; otherwise the first major on the resume that names a program.
    options = [args.major] if args.major else profile["majors"]
    major_text, major_hits = next(((t, h) for t in options if (h := major_matcher.match(t))),
                                  (options[0] if options else None, []))
    # The best-matching program's occupations (the dashboard does the same).
    major_socs = set(major_matcher.occupations_for([major_hits[0][0]])) if major_hits else None
    if major_text and not major_hits:
        print(f"Couldn't match the major \"{major_text}\" to a college program; ignoring it.")

    tests = read_tests(args)
    if any(tests.values()):
        profile["tests"] = tests
        traits = personality.occupation_traits(args.onet_version, args.cache, user_agent, args.onet_dir)
        traits = personality.blend_posting_traits(traits, detailed)
        profile["traits"] = personality.trait_signals(tests, traits[traits["onet_code"].isin(detailed["onet_code"])])
    rank_by = parse_rank_by(args.rank_by)
    occupations = score_occupations(detailed, profile, major_socs, rank_by, not args.ignore_requirements)
    ranked = rank_postings(postings, occupations, profile, args.top, args.state, args.metro,
                           metro_names, args.min_salary, rank_by, args.sort_postings)
    occupations.to_csv(out / "matching_occupations.csv", index=False)
    ranked.drop(columns=[c for c in ("description",) if c in ranked]).to_csv(
        out / "matching_postings.csv", index=False)
    (out / "matches.md").write_text(build_report(profile, major_text, major_hits, occupations,
                                                 ranked, results, args.top))
    print(f"{len(occupations):,} fitting occupations, {len(ranked):,} postings. "
          f"Wrote {out}/matches.md, matching_occupations.csv, matching_postings.csv")


if __name__ == "__main__":
    main()
