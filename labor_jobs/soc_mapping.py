"""Map free-text job titles to occupations.

BLS reports employment by SOC code, while LinkedIn postings only have a title
such as "Sr. Software Engineer - Payments". The bridge is O*NET's list of
~55,000 alternate job titles, each tied to an O*NET-SOC code. O*NET codes are
SOC codes with a two-digit suffix: 15-1252.00 is the SOC occupation Software
Developers itself, while 19-1029.01 Bioinformatics Scientists is a more
specialized occupation inside the SOC group 19-1029. Titles are matched to the
O*NET code, so niche roles keep their own identity; the first seven
characters give the SOC code used to join BLS data.

Titles are normalized (lowercased, abbreviations expanded, seniority words and
qualifiers removed) and matched in tiers:
  exact       the normalized title is a known title
  technology  a technology name plus a role ("ServiceNow Developer")
  partial     dropping up to two leading words, or short trailing
              abbreviations, gives a known title
              ("assistant store manager" -> "store manager",
               "occupational therapist ot" -> "occupational therapist")
  fuzzy       rapidfuzz similarity against known titles ending in the same word
"""

import csv
import re
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from .fetch import download

try:
    from rapidfuzz import fuzz, process
except ImportError:  # fuzzy matching is optional
    process = None

ONET_URL = "https://www.onetcenter.org/dl_files/database/db_{version}_text/{name}"
# Occupation Data comes first so official occupation titles win ties.
ONET_FILES = ("Occupation Data.txt", "Alternate Titles.txt", "Sample of Reported Titles.txt")
TITLE_COLUMNS = ("Alternate Title", "Reported Job Title", "Title")

ABBREVIATIONS = {
    "sr": "senior", "jr": "junior", "mgr": "manager", "eng": "engineer",
    "engr": "engineer", "dev": "developer", "admin": "administrator",
    "asst": "assistant", "assoc": "associate", "rep": "representative",
    "tech": "technician", "coord": "coordinator", "dir": "director",
    "vp": "vice president", "svp": "vice president", "avp": "vice president",
    "rn": "registered nurse", "lpn": "licensed practical nurse",
    "cna": "nursing assistant", "cdl": "truck driver", "swe": "software engineer",
    "hr": "human resources", "acct": "accountant", "cpa": "accountant",
    "frontend": "front end", "backend": "back end", "fullstack": "full stack",
}
DROP_WORDS = {
    "senior", "junior", "lead", "principal", "staff", "i", "ii", "iii", "iv",
    "entry", "level", "remote", "hybrid", "onsite", "temporary", "temp",
    "contract", "contractor", "prn", "diem", "per",
    "the", "a", "an", "1", "2", "3", "4", "new", "grad", "experienced",
}
# Too vague on their own to identify an occupation in the partial tier.
GENERIC_WORDS = {
    "manager", "associate", "specialist", "analyst", "engineer", "technician",
    "assistant", "coordinator", "consultant", "representative", "director",
    "supervisor", "administrator", "officer", "member", "worker", "operator",
    "agent", "executive", "advisor", "partner", "trainee", "intern",
    "professional", "generalist", "clerk", "designer", "developer", "scientist",
    "planner", "owner", "president", "attendant", "helper", "aide", "lead",
}
# Leading words that can be dropped to leave a single word: "associate attorney".
MODIFIER_WORDS = {
    "associate", "assistant", "deputy", "graduate", "travel", "traveling",
    "field", "certified", "licensed", "general", "clinical", "corporate",
    "night", "weekend", "bilingual", "chief", "head",
}
MAX_DROPPED_WORDS = 2
# Words too common in occupation names to show a title belongs to a
# specialized occupation ("Site Manager" vs "Wind Energy Operations Managers").
NOT_DISTINCTIVE = GENERIC_WORDS | {
    "and", "of", "except", "other", "all", "development", "operation", "production",
    "program", "project", "service", "management", "plant", "system", "business",
    "sale", "senior", "general", "worker",
}
MAX_TRAILING_ABBREVIATIONS = 2
# Role after a technology name ("Power BI Developer") -> O*NET code.
TECH_ROLES = {
    "developer": "15-1252.00",      # Software Developers
    "engineer": "15-1252.00",
    "programmer": "15-1251.00",     # Computer Programmers
    "administrator": "15-1244.00",  # Network and Computer Systems Administrators
    "architect": "15-1299.08",      # Computer Systems Engineers/Architects
    "consultant": "15-1211.00",     # Computer Systems Analysts
    "analyst": "15-1211.00",
    "specialist": "15-1211.00",
    "tester": "15-1253.00",         # Software Quality Assurance Analysts and Testers
}
SEPARATORS = re.compile(r"\s+[-–—:|]\s+|[,|/]")


def _singular(token):
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def normalize_title(text):
    """Canonical form of a title, applied to both postings and reference titles."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text.lower())
    text = re.sub(r"\b(full|part)[\s-]*time\b", " ", text)
    tokens = []
    for token in re.sub(r"[^a-z0-9+#]+", " ", text).split():
        tokens.extend(ABBREVIATIONS.get(token, token).split())
    kept = dict.fromkeys(_singular(t) for t in tokens if t not in DROP_WORDS)
    return " ".join(kept)


def title_variants(text):
    """The full title, then the part before the first separator."""
    if not isinstance(text, str):
        return []
    stripped = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    variants = [normalize_title(stripped), normalize_title(SEPARATORS.split(stripped)[0])]
    # A lone "manager" or "specialist" says nothing about the occupation.
    return list(dict.fromkeys(v for v in variants if v and v not in GENERIC_WORDS))


def onet_file(name, version, cache_dir, user_agent, onet_dir=None):
    """Path to an O*NET database file, downloading it if needed. Returns None
    if onet_dir is given and doesn't contain the file."""
    if onet_dir:
        path = Path(onet_dir) / name
        return path if path.exists() else None
    url = ONET_URL.format(version=version, name=quote(name))
    return download(url, Path(cache_dir) / f"onet_{version}" / name, user_agent)


def read_onet(path):
    return pd.read_csv(path, sep="\t", dtype=str, quoting=csv.QUOTE_NONE)


def load_onet_titles(version, cache_dir, user_agent, onet_dir=None):
    """Reference titles from O*NET as a frame of (title, onet_code)."""
    frames = []
    for name in ONET_FILES:
        path = onet_file(name, version, cache_dir, user_agent, onet_dir)
        if path is None:
            if name == "Sample of Reported Titles.txt":
                continue  # optional extra titles
            raise FileNotFoundError(f"{name} not found in {onet_dir}")
        df = read_onet(path)
        col = next(c for c in TITLE_COLUMNS if c in df)
        frames.append(pd.DataFrame({"title": df[col], "onet_code": df["O*NET-SOC Code"]}))
    return pd.concat(frames, ignore_index=True)


def to_onet_code(code):
    """Accept SOC (15-1252) or O*NET (15-1252.00) codes."""
    code = str(code).strip()
    return code + ".00" if re.fullmatch(r"\d{2}-\d{4}", code) else code


def expand_parentheses(titles):
    """O*NET writes some titles as "AI Engineer (Artificial Intelligence
    Engineer)". Add the spelled-out form in brackets as a title of its own
    when it has at least two words."""
    rows = []
    for title, code in titles:
        rows.append((title, code))
        if isinstance(title, str):
            rows += [(inner, code) for inner in re.findall(r"\(([^)]*)\)", title)
                     if len(inner.split()) >= 2]
    return rows


EXCLUDED = "-"
BUILTIN_OVERRIDES = Path(__file__).with_name("title_overrides.csv")


def load_title_overrides(*paths):
    """Title -> SOC code corrections from CSVs with title,soc_code columns.
    A soc_code of "-" means never match the title. The built-in list comes
    first so later files win."""
    overrides = {}
    for path in (BUILTIN_OVERRIDES, *paths):
        df = pd.read_csv(path, dtype=str)
        overrides.update((normalize_title(t), soc if soc == EXCLUDED else to_onet_code(soc))
                         for t, soc in zip(df["title"], df["soc_code"]))
    return overrides


class TitleMatcher:
    def __init__(self, reference, overrides=None, fuzzy_cutoff=101, employment=None,
                 technologies=None, occupation_names=None):
        """reference: frame of (title, onet_code); SOC codes are accepted too.
        employment: {soc_code: jobs}, used to pick the likelier occupation for
        a title listed under several. technologies: normalized technology
        names for the technology tier. occupation_names: {onet_code: name};
        when given, a title only maps to a specialized occupation if it shares
        a distinctive word with the occupation's name."""
        self.overrides = overrides or {}
        self.technologies = set(technologies or ())
        code_col = "onet_code" if "onet_code" in reference else "soc_code"
        pairs = expand_parentheses(zip(reference["title"], reference[code_col]))
        ref = pd.DataFrame(pairs, columns=["title", "onet_code"]).dropna()
        ref = pd.DataFrame({"key": ref["title"].map(normalize_title),
                            "onet_code": ref["onet_code"].map(to_onet_code)})
        ref = ref[ref["key"] != ""]
        ref["soc_code"] = ref["onet_code"].str[:7]

        # O*NET's specialized occupations (Solar Sales Representatives) also
        # list generic titles ("Sales Representative"). Within a SOC
        # occupation, a title listed under the general .00 code stays there.
        ref["general"] = ref["onet_code"].str.endswith(".00")
        listed_generally = ref.groupby(["key", "soc_code"])["general"].transform("any")
        ref = ref[ref["general"] | ~listed_generally]

        # The SOC occupation the title is listed under most often, then the
        # one with more workers.
        socs = ref.groupby(["key", "soc_code"]).size().rename("n").reset_index()
        socs["emp"] = socs["soc_code"].map(employment or {}).fillna(0)
        socs = socs.sort_values(["key", "n", "emp"], ascending=[True, False, False]) \
                   .drop_duplicates("key")[["key", "soc_code"]]
        # Within it, a title listed under one O*NET occupation goes there; one
        # spread over several specialized occupations stays at the SOC level.
        codes = ref.merge(socs, on=["key", "soc_code"]).groupby("key").agg(
            onet_code=("onet_code", "first"), soc_code=("soc_code", "first"),
            distinct=("onet_code", "nunique"))
        keep = codes["distinct"] == 1
        if occupation_names:
            words = {code: set(normalize_title(name).split()) - NOT_DISTINCTIVE
                     for code, name in occupation_names.items()}
            specialized = ~codes["onet_code"].str.endswith(".00")
            shares_word = [bool(words.get(code, set()) & set(key.split()))
                           for key, code in zip(codes.index, codes["onet_code"])]
            keep &= ~specialized | pd.Series(shares_word, index=codes.index)
        codes["onet_code"] = codes["onet_code"].where(keep, codes["soc_code"] + ".00")
        self.lookup = dict(zip(codes.index, codes["onet_code"]))
        # Overrides also apply to the partial and fuzzy tiers.
        for key, soc in self.overrides.items():
            if soc == EXCLUDED:
                self.lookup.pop(key, None)
            else:
                self.lookup[key] = soc
        self.choices = list(self.lookup)
        self.fuzzy_cutoff = fuzzy_cutoff

    def map_titles(self, titles):
        """Return soc_code, match_method and match_score aligned with titles."""
        unique = pd.Series(titles.dropna().unique())
        results = {}
        pending = []
        for raw in unique:
            variants = title_variants(raw)
            hit = self._exact(variants)
            if hit:
                results[raw] = hit
            else:
                pending.append((raw, variants))

        # Technology before partial, so "Ruby on Rails Engineer" isn't cut
        # down to "rails engineer" (a railroad job).
        for tier in (self._technology, self._partial):
            still = []
            for raw, variants in pending:
                hit = tier(variants)
                if hit:
                    results[raw] = hit
                else:
                    still.append((raw, variants))
            pending = still

        if pending and process is not None and self.fuzzy_cutoff <= 100:
            # Try full titles first, then the head before any separator.
            for position in (0, 1):
                queries = [(raw, v[position]) for raw, v in pending if len(v) > position]
                for raw, (choice, score) in self._fuzzy(queries).items():
                    results[raw] = (self.lookup[choice], "fuzzy", score)
                pending = [(raw, v) for raw, v in pending if raw not in results]

        mapped = titles.map(lambda t: results.get(t, (None, "unmatched", 0.0)))
        df = pd.DataFrame(mapped.tolist(), index=titles.index,
                          columns=["onet_code", "match_method", "match_score"])
        df.insert(1, "soc_code", df["onet_code"].str[:7])
        return df

    def _exact(self, variants):
        for v in variants:
            if v in self.overrides:
                if self.overrides[v] == EXCLUDED:
                    return None, "excluded", 0.0
                return self.overrides[v], "override", 100.0
        for v in variants:
            if v in self.lookup:
                return self.lookup[v], "exact", 100.0
        return None

    def _partial(self, variants):
        # The head before a separator first: "Physical Therapist - Home Health"
        # should not fall back to "home health".
        for v in reversed(variants):
            words = v.split()
            for trail in range(MAX_TRAILING_ABBREVIATIONS + 1):
                # Only short trailing tokens like "ot" or "pt" can be dropped.
                if trail and (len(words) - trail < 2 or len(words[-trail]) > 3):
                    break
                core = words[:len(words) - trail]
                if trail and " ".join(core) in self.lookup:
                    return self.lookup[" ".join(core)], "partial", 100.0
                for start in range(1, min(len(core), MAX_DROPPED_WORDS + 1)):
                    suffix = core[start:]
                    if len(suffix) == 1 and (suffix[0] in GENERIC_WORDS
                                             or not MODIFIER_WORDS.issuperset(core[:start])):
                        break
                    key = " ".join(suffix)
                    if key in self.lookup:
                        return self.lookup[key], "partial", 100.0
        return None

    def _technology(self, variants):
        for v in reversed(variants):
            words = v.split()
            if len(words) >= 2 and words[-1] in TECH_ROLES and self._all_technology(words[:-1]):
                return TECH_ROLES[words[-1]], "technology", 100.0
        return None

    def _all_technology(self, words):
        """True if the words are entirely technology names ("python c++")."""
        i = 0
        while i < len(words):
            for n in range(min(4, len(words) - i), 0, -1):
                if " ".join(words[i:i + n]) in self.technologies:
                    i += n
                    break
            else:
                return False
        return True

    def _fuzzy(self, queries, batch_size=2000):
        """Best reference title for each query scoring at least the cutoff,
        only considering titles that end in the same word (the job's head noun),
        so "sales trainee" cannot match "sales training specialist"."""
        by_last = {}
        for choice in self.choices:
            by_last.setdefault(choice.rsplit(" ", 1)[-1], []).append(choice)
        groups = {}
        for raw, query in queries:
            groups.setdefault(query.rsplit(" ", 1)[-1], []).append((raw, query))

        best = {}
        for last, group in groups.items():
            choices = by_last.get(last)
            if not choices:
                continue
            for i in range(0, len(group), batch_size):
                batch = group[i:i + batch_size]
                scores = process.cdist([q for _, q in batch], choices,
                                       scorer=fuzz.token_sort_ratio,
                                       score_cutoff=self.fuzzy_cutoff, workers=-1)
                top = scores.argmax(axis=1)
                for (raw, _), row, col in zip(batch, scores, top):
                    if row[col] >= self.fuzzy_cutoff:
                        best[raw] = (choices[col], float(row[col]))
        return best
