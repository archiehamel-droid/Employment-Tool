"""What each occupation requires, from O*NET, and what the postings ask for.

* Job Zone (1-5): how much education, experience and training O*NET says the
  occupation needs. 4 and 5 are occupations needing considerable or extensive
  preparation, typically a bachelor's or graduate degree plus experience.
* Skills: the most important O*NET skills for the occupation.
* Hot technologies: tools and software O*NET marks as frequently requested
  by employers, both as listed for the occupation and as found in the text of
  the postings themselves.
"""

import re

import pandas as pd

from .soc_mapping import normalize_title, onet_file, read_onet

PROFILE_FILES = ("Occupation Data.txt", "Job Zones.txt", "Skills.txt", "Technology Skills.txt")
JOB_ZONES = {
    1: "Little or no preparation",
    2: "Some preparation",
    3: "Medium preparation",
    4: "Considerable preparation",
    5: "Extensive preparation",
}
VENDORS = (
    "microsoft", "oracle", "adobe", "autodesk", "atlassian", "apache", "amazon",
    "google", "ibm", "apple", "the mathworks", "intuit", "esri", "dassault systemes",
    "trimble", "red hat", "cisco", "bentley", "mozilla", "kronos", "henry schein",
)
# Product names that are also everyday words. They are only matched with
# their vendor ("Microsoft Word"), or not at all when too short to be safe.
AMBIGUOUS = {
    "word", "access", "project", "teams", "windows", "edge", "office", "outlook",
    "spark", "go", "r", "c", "swift", "chef", "puppet", "slack", "zoom", "bash",
    "shell script", "bootstrap", "canva", "safari", "dynamics", "creative cloud",
    "sheets", "docs", "workspace", "concur", "primavera enterprise project portfolio management",
    "analytics", "cloud", "database", "server", "framework", "web services",
}
# Mentioned in postings as company social media links, not as job skills.
NOT_SKILLS = {"Facebook", "TikTok"}
# Common technologies not in O*NET's list, so titles like "Snowflake
# Developer" are recognized.
EXTRA_TECHNOLOGIES = (
    "snowflake", "databricks", "mainframe", "cobol", "golang", "terraform", "dbt",
    "kotlin", "rust", "sitecore", "workday", "guidewire", "pega", "mulesoft",
    "netsuite", "sharepoint", "dynamics 365", "power apps", "unity", "unreal",
    "flutter", "ios", "android", "aws", "azure", "gcp", "sql", "etl", "bi",
    "salesforce", "servicenow", "tableau", "sap", "oracle", "java", "python",
)


def load_profiles(version, cache_dir, user_agent, onet_dir=None):
    """Returns (profiles, technologies). profiles: one row per O*NET
    occupation with title, job zone, top skills and hot technologies.
    technologies: O*NET's technology list as (example, hot)."""
    paths = {name: onet_file(name, version, cache_dir, user_agent, onet_dir)
             for name in PROFILE_FILES}
    if any(p is None for p in paths.values()):
        return None, None
    occ = read_onet(paths["Occupation Data.txt"])
    zones = read_onet(paths["Job Zones.txt"])
    skills = read_onet(paths["Skills.txt"])
    tech = read_onet(paths["Technology Skills.txt"])

    profiles = occ[["O*NET-SOC Code", "Title"]].rename(
        columns={"O*NET-SOC Code": "onet_code", "Title": "onet_title"})
    zones = zones.rename(columns={"O*NET-SOC Code": "onet_code"})
    zones["job_zone"] = pd.to_numeric(zones["Job Zone"], errors="coerce")
    profiles = profiles.merge(zones[["onet_code", "job_zone"]], on="onet_code", how="left")
    profiles["preparation"] = profiles["job_zone"].map(JOB_ZONES)

    importance = skills[skills["Scale ID"] == "IM"].copy()
    importance["value"] = pd.to_numeric(importance["Data Value"], errors="coerce")
    top = (importance.sort_values("value", ascending=False)
           .groupby("O*NET-SOC Code")["Element Name"]
           .agg(lambda s: ", ".join(s.head(5))).rename("top_skills"))
    profiles = profiles.merge(top, left_on="onet_code", right_index=True, how="left")

    hot = tech[tech["Hot Technology"] == "Y"].copy()
    hot["name"] = hot["Example"].map(display_name)
    hot["in_demand"] = hot["In Demand"] == "Y"
    listed = (hot.sort_values(["in_demand", "name"], ascending=[False, True])
              .groupby("O*NET-SOC Code")["name"]
              .agg(lambda s: ", ".join(s.head(10))).rename("onet_hot_technologies"))
    profiles = profiles.merge(listed, left_on="onet_code", right_index=True, how="left")

    technologies = tech[["Example", "Hot Technology"]].drop_duplicates("Example")
    technologies = technologies.rename(columns={"Example": "example", "Hot Technology": "hot"})
    technologies["hot"] = technologies["hot"] == "Y"
    return profiles, technologies


def _strip(name):
    name = re.sub(r"\s+software$", "", name.strip())
    return name


def _acronym(name):
    """Trailing acronym in names like "Structured query language SQL", when
    its letters are the initials of the words before it (so "Microsoft
    Power BI" is not split), or the first letter matches the first word."""
    words = name.split()
    if len(words) >= 3 and re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", words[-1]):
        acronym = words[-1].lower()
        initials = iter(w[0].lower() for w in words[:-1] if w.lower() not in {"for", "of", "and"})
        # "Hypertext markup language HTML" spells H-T-M-L across fewer words.
        if acronym[0] == words[0][0].lower() or all(ch in initials for ch in acronym if ch.isalpha()):
            return words[-1], " ".join(words[:-1])
    return None, name


def _without_vendor(name):
    lower = name.lower()
    for vendor in VENDORS:
        if lower.startswith(vendor + " "):
            return name[len(vendor) + 1:]
    return None


def display_name(example):
    """Short everyday name: "Amazon Web Services AWS software" -> "AWS",
    "Microsoft Excel" -> "Excel", "Microsoft Word" stays as is."""
    name = re.sub(r"\s*\([^)]*\)", "", _strip(example))
    acronym, rest = _acronym(name)
    if acronym:
        return acronym
    short = _without_vendor(name)
    return short if short and short.lower() not in AMBIGUOUS else name


def aliases(example):
    """Ways a technology is written in posting text."""
    name = _strip(example)
    out = {name}
    out.update(re.findall(r"\(([^)]*)\)", name))
    name = re.sub(r"\s*\([^)]*\)", "", name)
    out.add(name)
    acronym, rest = _acronym(name)
    if acronym:
        out.update({acronym, rest})
    for candidate in list(out):
        short = _without_vendor(candidate)
        if short:
            out.add(short)
    return {a for a in out if a and a.lower() not in AMBIGUOUS and len(a) >= 2}


def title_technologies(technologies, occupation_titles):
    """Normalized technology names for recognizing titles like "Power BI
    Developer". Single words that appear in official occupation titles
    ("project", "data") are left out so they aren't mistaken for products."""
    title_words = {w for t in occupation_titles for w in normalize_title(t).split()}
    names = set()
    for example in list(technologies["example"]) + list(EXTRA_TECHNOLOGIES):
        for alias in aliases(example):
            key = normalize_title(alias)
            if not key or (" " not in key and (key in title_words or len(key) < 3)):
                continue
            names.add(key)
    return names


def mention_pattern(technologies):
    """One regex finding O*NET hot technologies in posting text, plus a map
    from each matched alias to its display name. Case-sensitive: product
    names are capitalized in postings, which avoids most everyday words."""
    lookup = {}
    for example in technologies.loc[technologies["hot"], "example"]:
        if display_name(example) in NOT_SKILLS:
            continue
        for alias in aliases(example):
            lookup.setdefault(alias, display_name(example))
    # Longest first so "Microsoft SQL Server" wins over "SQL".
    alternation = "|".join(re.escape(a) for a in sorted(lookup, key=len, reverse=True))
    return re.compile(rf"(?<![\w+#.])(?:{alternation})(?![\w+#])"), lookup


def technologies_in_postings(descriptions, technologies):
    """Set of hot technologies mentioned in each posting's description."""
    pattern, lookup = mention_pattern(technologies)
    return descriptions.fillna("").map(lambda text: {lookup[m] for m in pattern.findall(text)})


def top_mentions(postings, mentions, key, min_share=0.05, limit=8):
    """Per occupation, technologies mentioned in at least min_share of its
    postings, most common first, as "Python 42%, SQL 35%"."""
    exploded = (pd.DataFrame({key: postings[key], "tech": mentions})
                .dropna(subset=[key]).explode("tech"))
    totals = postings.groupby(key).size()
    counts = exploded.dropna(subset=["tech"]).groupby([key, "tech"]).size().rename("n").reset_index()
    counts["share"] = counts["n"] / counts[key].map(totals)
    counts = counts[counts["share"] >= min_share].sort_values([key, "share"], ascending=[True, False])
    return counts.groupby(key).apply(
        lambda g: ", ".join(f"{t} {s:.0%}" for t, s in zip(g["tech"].head(limit), g["share"].head(limit))),
        include_groups=False).rename("posting_technologies")
