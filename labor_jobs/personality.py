"""Match interest and personality test results to occupations.

How much each test can say about job fit differs, and the scoring follows that:

* Holland interests (RIASEC: Realistic, Investigative, Artistic, Social,
  Enterprising, Conventional) are what career counselors match people to
  jobs with. O*NET rates every occupation on all six (1-7), so a person's
  scores are compared with each occupation's profile directly. This is the
  main signal.
* Big Five personality (openness, conscientiousness, extraversion,
  agreeableness, emotional stability) relates more loosely to fit. It is
  compared with O*NET's work-style ratings (how important dependability,
  attention to detail, cooperation and so on are in the job) as a light
  signal.
* Myers-Briggs types have weak evidence for predicting job fit and no
  official mapping to occupations. A type is turned into rough interest
  scores using the direction of published MBTI-RIASEC correlations (E with
  Enterprising and Social, N with Artistic and Investigative, and so on), and
  counts for little. It is ignored when actual RIASEC scores are given.
* An IQ or other cognitive score is only described against each
  occupation's reasoning demands (O*NET ability levels). It never changes a
  ranking: one number says little about which field suits someone.
* Thinking strengths and weaknesses are a person's own rating of seven areas
  (verbal, numbers, reasoning, spatial, memory, speed and attention, hands-on
  and physical), compared with how much each occupation relies on the
  matching O*NET abilities. A job that leans on a strength scores higher, one
  that leans on a weakness lower. Abilities relate to doing a job well more
  than to enjoying it; self-ratings are rough, so this counts moderately.
* Work values (achievement, independence, recognition, relationships,
  support, working conditions) are what a person most wants from work,
  compared with O*NET's ratings of how much each occupation offers them.
  With interests, values are the better guide to satisfaction.

The Profile tab groups these: interests and values into "satisfaction",
abilities and work styles into "strengths".
"""

import math
import re

import pandas as pd

from .soc_mapping import onet_file, read_onet

RIASEC = ("R", "I", "A", "S", "E", "C")
RIASEC_NAMES = {"R": "Realistic", "I": "Investigative", "A": "Artistic", "S": "Social",
                "E": "Enterprising", "C": "Conventional"}
BIG_FIVE = ("O", "C", "E", "A", "N")
BIG_FIVE_NAMES = {"O": "Openness", "C": "Conscientiousness", "E": "Extraversion", "A": "Agreeableness",
                  "N": "Emotional stability"}
# Which O*NET work styles each Big Five trait shows up as. N is scored as
# emotional stability (the reverse of neuroticism).
WORK_STYLES = {
    "O": ("Innovation", "Analytical Thinking", "Adaptability/Flexibility"),
    "C": ("Achievement/Effort", "Persistence", "Dependability", "Attention to Detail"),
    "E": ("Social Orientation", "Leadership", "Initiative"),
    "A": ("Cooperation", "Concern for Others"),
    "N": ("Self-Control", "Stress Tolerance"),
}
REASONING = ("Deductive Reasoning", "Inductive Reasoning", "Mathematical Reasoning", "Written Comprehension",
             "Oral Comprehension", "Information Ordering", "Problem Sensitivity", "Originality",
             "Fluency of Ideas", "Category Flexibility")
# Each MBTI preference raises the interests it correlates with in published
# studies (e.g. Hammer & Kummerow 1996). Directions only; the effects are small.
MBTI_INTERESTS = {
    "E": {"E": 1, "S": 1}, "I": {"I": 1, "R": 0.5}, "N": {"A": 1, "I": 1}, "S": {"C": 1, "R": 1},
    "F": {"S": 1, "A": 0.5}, "T": {"I": 0.5, "R": 0.5, "E": 0.5}, "J": {"C": 1}, "P": {"A": 1},
}
WEIGHTS = {"interests": 2.0, "mbti": 0.75, "styles": 1.0, "abilities": 1.0, "values": 1.0}
# O*NET abilities behind each thinking or physical area a person can rate.
ABILITY_AREAS = {
    "verbal": ["Oral Comprehension", "Written Comprehension", "Oral Expression", "Written Expression"],
    "numbers": ["Mathematical Reasoning", "Number Facility"],
    "reasoning": ["Deductive Reasoning", "Inductive Reasoning", "Problem Sensitivity", "Information Ordering",
                  "Category Flexibility", "Fluency of Ideas", "Originality"],
    "spatial": ["Spatial Orientation", "Visualization"],
    "memory": ["Memorization"],
    "attention": ["Perceptual Speed", "Speed of Closure", "Flexibility of Closure", "Selective Attention", "Time Sharing"],
    "physical": ["Manual Dexterity", "Finger Dexterity", "Arm-Hand Steadiness", "Control Precision",
                 "Multilimb Coordination", "Static Strength", "Stamina", "Trunk Strength", "Dynamic Strength"],
}
WORK_VALUES = {"achievement": "Achievement", "independence": "Independence", "recognition": "Recognition",
               "relationships": "Relationships", "support": "Support", "conditions": "Working Conditions"}
# A person's rating of an area or value as a number.
STRENGTH = {"strength": 1.0, "average": 0.5, "weakness": 0.0}
IMPORTANCE = {"very": 1.0, "some": 0.6, "less": 0.2}


def occupation_traits(version, cache_dir, user_agent, onet_dir=None, codes=None):
    """One row per O*NET occupation: RIASEC interest levels (1-7), Big Five
    trait demand from its work styles (0-1, ranked across occupations), and
    reasoning demand (mean O*NET level of reasoning abilities, 0-7), plus each
    ability area's and work value's rank across occupations (ab_*, wv_*, 0-1).
    `codes` adds occupations O*NET doesn't rate ("All Other" catch-alls), filled
    from related ones like other gaps (fill_from_related)."""
    def load(name):
        df = read_onet(onet_file(name, version, cache_dir, user_agent, onet_dir))
        return df.assign(**{"Data Value": pd.to_numeric(df["Data Value"], errors="coerce")})
    interests = load("Interests.txt")
    interests = interests[interests["Scale ID"] == "OI"]
    letter = {name: key for key, name in RIASEC_NAMES.items()}
    riasec = interests.assign(k=interests["Element Name"].map(letter)).pivot_table(
        index="O*NET-SOC Code", columns="k", values="Data Value", aggfunc="mean")
    styles = load("Work Styles.txt")
    styles = styles[styles["Scale ID"] == "IM"].pivot_table(
        index="O*NET-SOC Code", columns="Element Name", values="Data Value", aggfunc="mean")
    big5 = pd.DataFrame({t: styles[[c for c in cols if c in styles]].mean(axis=1) for t, cols in WORK_STYLES.items()})
    big5 = big5.rank(pct=True)                       # comparable with a person's percentiles
    abilities = load("Abilities.txt")
    levels = abilities[abilities["Scale ID"] == "LV"]
    reasoning = levels[levels["Element Name"].isin(REASONING)].groupby("O*NET-SOC Code")["Data Value"].mean()
    reasoning = reasoning.rename("reasoning")
    # How much each occupation relies on each area: mean ability level, ranked across occupations.
    areas = pd.DataFrame({f"ab_{area}": levels[levels["Element Name"].isin(names)]
                          .groupby("O*NET-SOC Code")["Data Value"].mean() for area, names in ABILITY_AREAS.items()})
    areas = areas.rank(pct=True)
    out = riasec.join(big5.add_prefix("b5_"), how="outer").join(reasoning, how="outer").join(areas, how="outer")
    try:
        values = load("Work Values.txt")
    except Exception:  # an older O*NET copy without the file: values just don't count
        values = None
    if values is not None:
        values = values[values["Scale ID"] == "EX"].pivot_table(
            index="O*NET-SOC Code", columns="Element Name", values="Data Value", aggfunc="mean")
        offered = pd.DataFrame({f"wv_{key}": values[name] for key, name in WORK_VALUES.items() if name in values})
        out = out.join(offered.rank(pct=True), how="outer")
    if codes is not None:
        out = out.reindex(out.index.union(pd.Index(list(codes))))
    out = fill_from_related(out)
    out["reasoning_band"] = [reasoning_band(v, out["reasoning"].dropna()) for v in out["reasoning"]]
    out.index.name = "onet_code"
    return out.reset_index()


def fill_from_related(table):
    """Newer O*NET codes (Software Developers 15-1252, Data Scientists 15-2051...)
    often have no ratings yet. Fill each gap with the average of the occupations
    in the same broad SOC group (the first six characters, 15-125), or failing
    that the same minor group (15-12). Rows are O*NET codes."""
    out = table.copy()
    for width in (6, 5):
        group = out.index.str[:width]
        means = out.groupby(group).transform("mean")
        out = out.fillna(means)
    return out


def blend_posting_traits(traits, detailed, min_postings=10):
    """Mix what postings ask for into each occupation's Big Five demand: the
    share of its postings asking for soft skills tied to each trait (from
    posting_requirements, ranked across occupations) averaged with the O*NET
    work-style ranking, where at least min_postings postings were read."""
    cols = [f"soft_{t}" for t in BIG_FIVE]
    if detailed is None or not set(cols) <= set(detailed.columns):
        return traits
    asked = detailed[detailed.get("postings_with_text", 0) >= min_postings][["onet_code", *cols]].dropna()
    if asked.empty:
        return traits
    ranks = asked.set_index("onet_code")[cols].rank(pct=True)
    out = traits.copy()
    for t in BIG_FIVE:
        posted = out["onet_code"].map(ranks[f"soft_{t}"])
        onet = out[f"b5_{t}"]
        out[f"b5_{t}"] = onet.where(posted.isna(), (onet.fillna(posted) + posted) / 2)
    return out


def reasoning_band(level, levels):
    """'Lower', 'Moderate', 'High' or 'Very high' reasoning demand: the
    occupation's quartile among all occupations."""
    if level is None or (isinstance(level, float) and math.isnan(level)):
        return None
    share = (levels < level).mean()
    return "Lower" if share < 0.25 else "Moderate" if share < 0.5 else "High" if share < 0.75 else "Very high"


def iq_band(iq):
    """A plain description of an IQ-style score (mean 100, SD 15)."""
    if iq is None:
        return None
    return ("below average" if iq < 90 else "average" if iq < 110 else "above average" if iq < 125
            else "well above average")


# ---- Reading test results ----

_NUM = r"(\d{1,3}(?:\.\d+)?)\s*(?:%|/\s*\d+|th|st|nd|rd)?"


def _scores(text, names):
    """{key: number} for names followed by a number on the same line ("Social: 34", "Openness 72%")."""
    out = {}
    for key, pattern in names.items():
        m = re.search(rf"(?im)\b(?:{pattern})\b[^\d\n]{{0,30}}{_NUM}", text)
        if m:
            out[key] = float(m.group(1))
    return out


def riasec_from_code(code):
    """'SIA' -> scores 3, 2, 1 for the first three letters, 0 for the rest."""
    code = re.sub(r"[^RIASEC]", "", code.upper())[:3]
    if not code:
        return None
    return {k: float(3 - code.index(k)) if k in code else 0.0 for k in RIASEC}


def mbti_to_riasec(mbti):
    """Rough interest scores from a type like 'INTJ' (see the module notes)."""
    mbti = (mbti or "").upper()
    if not re.fullmatch(r"[EI][NS][TF][JP]", mbti[:4]):
        return None
    scores = dict.fromkeys(RIASEC, 1.0)
    for letter in mbti[:4]:
        for interest, weight in MBTI_INTERESTS[letter].items():
            scores[interest] += weight
    return scores


def normalize_big_five(scores):
    """Big Five scores to 0-1: percentiles (0-100) or a 1-5 scale. 'Neuroticism'
    becomes emotional stability."""
    if not scores:
        return None
    values = [v for k, v in scores.items() if k != "_neuroticism"]
    top = max(values) if values else 0
    scale = (lambda v: (v - 1) / 4) if top <= 5 else (lambda v: v / 100)
    out = {k: min(1.0, max(0.0, scale(v))) for k, v in scores.items() if k in BIG_FIVE}
    if "_neuroticism" in scores:
        out["N"] = 1 - min(1.0, max(0.0, scale(scores["_neuroticism"])))
    return out or None


def parse_results(text):
    """Pick test results out of a report's text: RIASEC scores or a Holland
    code, Big Five scores, a Myers-Briggs type and an IQ score. Anything not
    found is None."""
    if not isinstance(text, str):
        text = ""
    riasec = _scores(text, {k: name for k, name in RIASEC_NAMES.items()})
    if len(riasec) < 3:
        # Case-sensitive: a code is capital letters, so the word "is" isn't read as I-S.
        m = re.search(r"\b(?:[Hh]olland|HOLLAND|RIASEC|[Ii]nterest|[Yy]our)[ \t]*(?:[Cc]ode|[Tt]ype|[Pp]rofile)?"
                      r"(?:[ \t]+is)?[ \t:=-]*\b([RIASEC]{2,3})\b", text)
        riasec = riasec_from_code(m.group(1)) if m else None
    else:
        riasec = {k: riasec.get(k, 0.0) for k in RIASEC}
    big5 = _scores(text, {"O": r"openness(?:\s+to\s+experience)?", "C": "conscientiousness",
                          "E": "extraversion|extroversion", "A": "agreeableness",
                          "N": r"emotional\s+stability", "_neuroticism": "neuroticism"})
    big5 = normalize_big_five(big5) if len(big5) >= 3 else None
    m = re.search(r"\b([EI][NS][TF][JP])(?:-[AT])?\b", text)
    mbti = m.group(1) if m else None
    m = re.search(r"(?i)\b(?:full[\s-]*scale\s+iq|fsiq|iq(?:\s+score)?)\b[^\d\n]{0,20}(\d{2,3})\b", text)
    iq = int(m.group(1)) if m and 40 <= int(m.group(1)) <= 200 else None
    return {"riasec": riasec, "big_five": big5, "mbti": mbti, "iq": iq}


# ---- Scoring ----

def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else 0.0


def interest_fit(person, occupation):
    """0-1: the correlation of a person's six interest scores with the
    occupation's, with no fit below zero."""
    xs = [person[k] for k in RIASEC]
    ys = [occupation.get(k) for k in RIASEC]
    if any(y is None or (isinstance(y, float) and math.isnan(y)) for y in ys):
        return None
    return max(0.0, _pearson(xs, ys))


def style_fit(person, occupation):
    """0-1: how closely the person's Big Five levels match what the job's work
    styles ask for (one minus the mean gap, over the traits given)."""
    gaps = [abs(v - occupation.get(f"b5_{k}")) for k, v in person.items()
            if occupation.get(f"b5_{k}") is not None and not math.isnan(occupation.get(f"b5_{k}"))]
    return 1 - sum(gaps) / len(gaps) if gaps else None


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def ability_fit(person, occupation):
    """0-1 (0.5 neutral): whether the occupation leans on the areas a person rates
    as strengths (higher) or weaknesses (lower). person maps areas to
    'strength', 'average' or 'weakness'; areas not rated don't count. Also
    returns the areas the job relies on heavily (top quarter) that the person
    rated as strengths and as weaknesses."""
    parts, uses, leans = [], [], []
    for area, rating in person.items():
        demand = _num(occupation.get(f"ab_{area}"))
        if rating not in STRENGTH or demand is None:
            continue
        parts.append((STRENGTH[rating] - 0.5) * 2 * demand)
        if demand >= 0.75 and rating == "strength":
            uses.append(area)
        if demand >= 0.75 and rating == "weakness":
            leans.append(area)
    if not parts:
        return None, [], []
    return (sum(parts) / len(parts) + 1) / 2, uses, leans


def values_fit(person, occupation):
    """0-1: how much the occupation offers what a person values, the mean of its
    ranks on each work value weighted by how important the person says it is
    ('very', 'some' or 'less')."""
    num = den = 0.0
    for key, importance in person.items():
        offered = _num(occupation.get(f"wv_{key}"))
        if importance not in IMPORTANCE or offered is None:
            continue
        num += IMPORTANCE[importance] * offered
        den += IMPORTANCE[importance]
    return num / den if den else None


def holland_code(scores, n=3):
    """The top letters of a set of RIASEC scores, highest first ('SIC')."""
    return "".join(sorted(RIASEC, key=lambda k: -scores[k])[:n])


def trait_signals(tests, traits):
    """{onet_code: {signal: 0-1}} for the tests given, plus each occupation's
    Holland code and reasoning band for display."""
    riasec = tests.get("riasec")
    mbti_scores = None if riasec else mbti_to_riasec(tests.get("mbti"))
    big5 = tests.get("big_five")
    strengths, values = tests.get("abilities") or {}, tests.get("values") or {}
    out = {}
    raw_styles, raw_values = {}, {}
    for row in traits.to_dict("records"):
        s = {}
        if riasec:
            s["interests"] = interest_fit(riasec, row)
        elif mbti_scores:
            s["mbti"] = interest_fit(mbti_scores, row)
        if big5:
            raw_styles[row["onet_code"]] = style_fit(big5, row)
        uses, leans = [], []
        if strengths:
            s["abilities"], uses, leans = ability_fit(strengths, row)
        if values:
            raw_values[row["onet_code"]] = values_fit(values, row)
        code = holland_code(row) if all(not pd.isna(row.get(k)) for k in RIASEC) else None
        out[row["onet_code"]] = {"signals": {k: v for k, v in s.items() if v is not None}, "holland": code,
                                 "reasoning": row.get("reasoning_band"), "uses": uses, "leans_on": leans}
    # Work-style and values fit vary little in absolute terms: spread each over 0-1.
    for name, raw in (("styles", raw_styles), ("values", raw_values)):
        found = [v for v in raw.values() if v is not None]
        if found:
            lo, hi = min(found), max(found)
            for code, v in raw.items():
                if v is not None:
                    out[code]["signals"][name] = (v - lo) / (hi - lo) if hi > lo else 1.0
    return out
