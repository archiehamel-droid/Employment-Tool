"""College majors: match a major as people write it ("CS", "BS in Mechanical
Engineering", "Nursing") to the federal Classification of Instructional
Programs (CIP), then to the occupations those programs lead to, using the
NCES/BLS CIP 2020 to SOC 2018 crosswalk.

Sources: https://nces.ed.gov/ipeds/cipcode/ (CIPCode2020.csv and
CIP2020_SOC2018_Crosswalk.xlsx).
"""

import re
from pathlib import Path

import pandas as pd

from .fetch import download

CIP_URL = "https://nces.ed.gov/ipeds/cipcode/Files/CIPCode2020.csv"
CROSSWALK_URL = "https://nces.ed.gov/ipeds/cipcode/Files/CIP2020_SOC2018_Crosswalk.xlsx"
# Shorthand people use for common majors.
MAJOR_ALIASES = {
    "cs": "computer science", "comp sci": "computer science", "compsci": "computer science",
    "ee": "electrical engineering", "eecs": "electrical engineering and computer science",
    "me": "mechanical engineering", "mech e": "mechanical engineering",
    "ce": "civil engineering", "chem e": "chemical engineering", "cheme": "chemical engineering",
    "bme": "biomedical engineering", "ie": "industrial engineering",
    "mis": "management information systems", "cis": "computer information systems",
    "it": "information technology", "econ": "economics", "psych": "psychology",
    "bio": "biology", "chem": "chemistry", "math": "mathematics", "stats": "statistics",
    "poli sci": "political science", "polisci": "political science",
    "comms": "communication", "pre med": "biology", "premed": "biology",
    "mba": "business administration and management", "bba": "business administration and management",
    "finance": "finance, general", "accounting": "accounting", "nursing": "registered nursing",
    "bsn": "registered nursing", "rn": "registered nursing",
    # Broad majors whose CIP names are long.
    "biology": "biology biological sciences", "biological sciences": "biology biological sciences",
    "english": "english language and literature", "english literature": "english language and literature",
    "communications": "communication", "communication studies": "speech communication and rhetoric",
    "marketing": "marketing marketing management", "journalism": "journalism",
    "chemistry": "chemistry", "physics": "physics", "sociology": "sociology",
    "philosophy": "philosophy", "anthropology": "anthropology", "music": "music",
    "nutrition": "foods nutrition and wellness studies", "architecture": "architecture",
    "education": "education", "elementary education": "elementary education and teaching",
    "art": "fine studio arts", "studio art": "fine studio arts", "fine arts": "fine studio arts",
}
DEGREE_WORDS = re.compile(
    r"(?i)\b(bachelor'?s?|master'?s?|associate'?s?|doctorate|ph\.?\s?d\.?|b\.?\s?s\.?|b\.?\s?a\.?|"
    r"m\.?\s?s\.?|m\.?\s?a\.?|b\.?\s?sc\.?|m\.?\s?sc\.?|degree|major(ed)?|minor|concentration|"
    r"of science|of arts|in)\b")


def cip_files(cache_dir, user_agent):
    return (download(CIP_URL, Path(cache_dir) / "CIPCode2020.csv", user_agent),
            download(CROSSWALK_URL, Path(cache_dir) / "CIP2020_SOC2018_Crosswalk.xlsx", user_agent))


def _clean_code(code):
    return re.sub(r'[="]', "", str(code)).strip()


def _key(text):
    text = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    return " ".join(text.split())


class MajorMatcher:
    def __init__(self, cip_csv, crosswalk_xlsx):
        cip = pd.read_csv(cip_csv, dtype=str)
        cip["code"] = cip["CIPCode"].map(_clean_code)
        cip = cip[cip["code"].str.fullmatch(r"\d{2}\.\d{4}")]
        # Families 32-37 are basic skills and leisure courses, 53 high school
        # diplomas: not majors.
        cip = cip[~cip["code"].str[:2].isin(["32", "33", "34", "35", "36", "37", "53"])]
        self.titles = dict(zip(cip["code"], cip["CIPTitle"].str.rstrip(".")))
        names = []  # (key, code), official title first, then example program names
        for code, title, examples in zip(cip["code"], cip["CIPTitle"], cip["Examples"].fillna("")):
            names.append((_key(title.rstrip(".")), code))
            for ex in re.split(r"[;]|\s-\s", re.sub(r"(?i)^examples?:\s*", "", examples)):
                if 2 <= len(ex.split()) <= 8:
                    names.append((_key(ex), code))
        self.names = names
        self.exact = {}
        for key, code in names:
            self.exact.setdefault(key, code)

        xw = pd.read_excel(crosswalk_xlsx, sheet_name="CIP-SOC", dtype=str)
        xw = xw[xw["SOC2018Code"].str.fullmatch(r"\d{2}-\d{4}", na=False)]
        xw["code"] = xw["CIP2020Code"].map(_clean_code)
        self.occupations = xw.groupby("code")["SOC2018Code"].apply(list).to_dict()

    def match(self, text, limit=3, min_score=85):
        """CIP codes for a major, best first, as [(code, title, score)]."""
        query = _key(DEGREE_WORDS.sub(" ", str(text)))
        query = MAJOR_ALIASES.get(query, query)
        if not query:
            return []
        # "Biology" means the general program, not Wildlife Biology.
        for key in (query, f"{query} general"):
            if key in self.exact:
                code = self.exact[key]
                return [(code, self.titles[code], 100.0)]
        from rapidfuzz import fuzz, process

        def scorer(a, b, **kwargs):
            # Containing every query word counts most, but extra words cost a
            # little, and general programs win ties.
            bonus = 5 if b.endswith(" general") else 0
            return min(100, 0.7 * fuzz.token_set_ratio(a, b) + 0.3 * fuzz.token_sort_ratio(a, b) + bonus)

        keys = [k for k, _ in self.names]
        hits = process.extract(query, keys, scorer=scorer, limit=limit * 10,
                               score_cutoff=min_score * 0.7)
        out, seen = [], set()
        hits.sort(key=lambda h: -h[1])
        for key, score, idx in hits:
            code = self.names[idx][1]
            if code not in seen:
                seen.add(code)
                out.append((code, self.titles[code], float(score)))
            if len(out) == limit:
                break
        return out

    def occupations_for(self, codes):
        """SOC codes the programs lead to, without postsecondary teaching
        (listed for nearly every major)."""
        socs = []
        for code in codes:
            socs += [s for s in self.occupations.get(code, []) if not s.startswith("25-1")]
        return list(dict.fromkeys(socs))


# Degree names. Short forms only count with dots ("M.S.") or followed by
# "in" ("MS in"), so "MS Office" or "Boston, MA" aren't read as degrees.
DEGREE = (r"(?:bachelor'?s?|master'?s?|associate'?s?|doctor(?:ate)?|ph\.\s?d\.?|phd|"
          r"[bm]\.\s?(?:s|a|sc|eng)\.?|[bm](?:s|a|sc|eng)(?=\s+in\b)|bsn|msn|meng|beng)")


def majors_in_text(text):
    """Field-of-study phrases in resume text, like "B.S. in Computer
    Science" -> "Computer Science"."""
    found = []
    pattern = re.compile(
        rf"(?i)(?<![A-Za-z]){DEGREE}(?![A-Za-z])(?:\s+(?:of|degree))?(?:\s+(?:science|arts))?"
        r"\s*(?:in|of|,|:|-|\u2013|\u2014)\s*([A-Z][A-Za-z&/ ]{2,60})")
    for m in pattern.finditer(text):
        field = re.split(r"\s{2,}|\s(?:from|at|with|minor|GPA|University|College)\b", m.group(1))[0]
        field = re.sub(r"(?i)^applied (?:science|arts) in\s+", "", field)
        found.append(field.strip(" ,.-"))
    for m in re.finditer(r"(?im)^\s*major\s*[:\-]\s*(.+)$", text):
        found.append(m.group(1).strip())
    if re.search(r"\bMBA\b", text):
        found.append("MBA")
    return list(dict.fromkeys(f for f in found if f))


EDUCATION_LEVELS = [  # (pattern, level, label), highest first; same short-form rule as DEGREE
    (r"(?i)\bph\.?\s?d\b|\bdoctor(ate|al)\b|\b(m\.d\.|j\.d\.|dds|dvm|pharmd|dnp)(?![A-Za-z])",
     5, "Doctoral or professional degree"),
    (r"(?i)\bmaster'?s?\b|(?<![A-Za-z])m\.\s?(s|a|sc|eng)\.?(?![A-Za-z])|\bm(s|a|sc)(?=\s+in\b)|"
     r"\bmba\b|\bmeng\b|\bmph\b|\bmsw\b|\bmsn\b", 4, "Master's degree"),
    (r"(?i)\bbachelor'?s?\b|(?<![A-Za-z])b\.\s?(s|a|sc|eng)\.?(?![A-Za-z])|\bb(s|a|sc)(?=\s+in\b)|"
     r"\bbsn\b|\bbeng\b", 3, "Bachelor's degree"),
    (r"(?i)\bassociate'?s?\s+(of|degree|in)\b|(?<![A-Za-z])a\.a\.s?\.?", 2, "Associate's degree"),
]
# BLS "typical education needed for entry" -> the same scale.
BLS_EDUCATION = {
    "Doctoral or professional degree": 5, "Master's degree": 4, "Bachelor's degree": 3,
    "Associate's degree": 2, "Postsecondary nondegree award": 1, "Some college, no degree": 1,
    "High school diploma or equivalent": 0, "No formal educational credential": 0,
}


def education_level(text):
    """Highest degree mentioned, as (level 0-5, label)."""
    for pattern, level, label in EDUCATION_LEVELS:
        if re.search(pattern, text):
            return level, label
    return 0, "No degree found"
