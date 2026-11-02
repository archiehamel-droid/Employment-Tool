"""Compare what a resume says with what each occupation does.

Each O*NET occupation is described by its title, description, task
statements ("Titrate vasoactive drips", "Prepare financial statements") and
alternate titles. Words and two-word phrases are weighted by TF-IDF, so
words shared by many occupations ("customer", "manage") count little and
specific ones ("conduit", "subpoena", "ventilator") count a lot. A resume is
weighted the same way, and its similarity to an occupation is the cosine of
the two.

This catches what job titles miss: a resume headed "Associate, Patent
Prosecution Group" or "Revenue Cycle Specialist II" has no known title, but
its duties read like a lawyer's or a medical coder's.

The dashboard's resume check runs the same comparison in the browser, from
the vectors written by export_rows; dashboard/resume.js mirrors tokens().
"""

import math
import re
from collections import Counter

from .soc_mapping import onet_file, read_onet

STOP = set("""a about above across after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our
ours out over own same she should so some such than that the their theirs them then there these they this those through
to too under until up very was we were what when where which while who whom why will with would you your yours etc
including include includes various related such may must per new use using used within without based other others work
working job jobs year years month months present inc llc co company university college school high summer intern
experience responsible duties team""".split())
SUFFIXES = ("ization", "ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "s")
# Words in more than this share of occupations say nothing about any one.
MAX_DOC_SHARE = 0.5
TOP_TERMS = 120


def stem(word):
    """A light suffix strip, the same in Python and the browser."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
    return word


def tokens(text):
    """Stemmed words of three or more letters, then adjacent pairs ("patent_application")."""
    words = [stem(w) for w in re.findall(r"[a-z][a-z+#]{2,}", text.lower()) if w not in STOP]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def occupation_documents(version, cache_dir, user_agent, onet_dir=None):
    """{O*NET code: text} from occupation titles, descriptions, tasks and alternate titles."""
    def load(name):
        return read_onet(onet_file(name, version, cache_dir, user_agent, onet_dir))
    occupations = load("Occupation Data.txt")
    docs = {code: [title, title, description] for code, title, description in
            zip(occupations["O*NET-SOC Code"], occupations["Title"], occupations["Description"])}
    for name, column in (("Task Statements.txt", "Task"), ("Alternate Titles.txt", "Alternate Title")):
        df = load(name)
        for code, text in zip(df["O*NET-SOC Code"], df[column]):
            docs.setdefault(code, []).append(text)
    return {code: " . ".join(parts) for code, parts in docs.items()}


class TextModel:
    def __init__(self, documents, top_terms=TOP_TERMS):
        counts = {code: Counter(tokens(text)) for code, text in documents.items()}
        df = Counter(t for c in counts.values() for t in c)
        n = len(counts)
        self.idf = {t: math.log(n / k) for t, k in df.items() if k <= n * MAX_DOC_SHARE}
        self.vectors = {}
        for code, c in counts.items():
            v = {t: (1 + math.log(k)) * self.idf[t] for t, k in c.items() if t in self.idf}
            top = dict(sorted(v.items(), key=lambda kv: -kv[1])[:top_terms])
            norm = math.sqrt(sum(x * x for x in top.values())) or 1.0
            self.vectors[code] = {t: x / norm for t, x in top.items()}

    def similarity(self, text):
        """{O*NET code: cosine similarity with the text}."""
        counts = Counter(tokens(text))
        v = {t: (1 + math.log(k)) * self.idf[t] for t, k in counts.items() if t in self.idf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {code: sum(w * v.get(t, 0.0) for t, w in vec.items()) / norm
                for code, vec in self.vectors.items()}

    def export_rows(self, codes=None):
        """Rows for the browser: each occupation's terms with weight times IDF,
        so the page needs no separate IDF table ("term:0.123 term:0.045").
        The resume's own length cancels out when scores are scaled to the best
        occupation, so the page skips normalizing it."""
        rows = []
        for code, vec in self.vectors.items():
            if codes is not None and code not in codes:
                continue
            terms = " ".join(f"{t}:{w * self.idf[t]:.3g}" for t, w in vec.items())
            rows.append({"o": code, "v": terms})
        return rows
