"""Recent research on where each industry's jobs are heading, for the dashboard's
"What research says" notes.

    python -m labor_jobs.research fetch --out output/research_candidates.json
    python -m labor_jobs.research check --candidates output/research_candidates.json --notes notes.json \\
        --out output/dashboard/industry_research.json

fetch searches Crossref (free, journal articles with abstracts) and Semantic Scholar
(free but often rate-limited; SEMANTIC_SCHOLAR_API_KEY raises its limit) for each JOLTS
industry's labor-demand outlook, keeps recent papers
with an abstract (2019 on), and ranks them by citations, favoring newer ones. With
OPENALEX_API_KEY set it also searches OpenAlex.

The notes themselves are written by Claude from those candidates (a few plain
sentences per industry, citing 2 to 5 of them by id), then `check` makes sure every
cited paper is one of the candidates, so no source is invented, and writes the
dashboard's table: industry, summary, sources (title, year, venue, url, citations).
"""

import argparse
import json
import math
import os
import time
from datetime import date
from pathlib import Path

import requests

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_URL = "https://api.crossref.org/works"
OA_URL = "https://api.openalex.org/works"
FIELDS = "title,year,venue,citationCount,abstract,url,externalIds,tldr"

QUERIES = {
    "Total nonfarm": ["future of work labor demand United States", "labor market outlook employment projections"],
    "Mining and logging": ["mining employment outlook labor demand", "energy transition mining jobs"],
    "Construction": ["construction industry labor shortage workforce", "construction employment demand housing"],
    "Manufacturing": ["manufacturing employment automation reshoring", "manufacturing workforce demand robots"],
    "Wholesale trade": ["wholesale trade employment digitalization", "wholesale distribution workforce automation"],
    "Retail trade": ["retail employment e-commerce automation", "retail workforce future jobs"],
    "Transportation, warehousing, and utilities": ["warehouse employment automation logistics jobs",
                                                   "trucking autonomous vehicles employment"],
    "Information": ["information sector employment generative AI", "media technology jobs labor demand"],
    "Financial activities": ["financial services employment artificial intelligence",
                             "banking jobs automation workforce"],
    "Professional and business services": ["generative AI professional services employment",
                                           "knowledge workers labor demand AI exposure"],
    "Private educational services": ["education workforce teacher shortage demand", "higher education employment"],
    "Health care and social assistance": ["health care workforce demand aging population",
                                          "nursing shortage projections workforce"],
    "Arts, entertainment, and recreation": ["arts entertainment employment recovery", "creative workers generative AI"],
    "Accommodation and food services": ["restaurant hospitality labor shortage employment",
                                        "food service automation workforce"],
    "Other services": ["personal services employment repair workforce", "skilled trades labor demand"],
    "Government": ["public sector workforce employment government jobs", "state and local government staffing"],
}


# A candidate must name its industry and be about work, in its title or abstract.
TOPIC = {
    "Total nonfarm": r"labou?r market|employment|workforce|jobs",
    "Mining and logging": r"mining industry|coal min|mine workers|miners\b|logging industry|oil and gas|extractive|fracking",
    "Construction": r"construction (industry|work|labou?r|sector|trades|employment)|homebuild|building trades",
    "Manufacturing": r"manufactur|factor(y|ies)|industrial robots?|production workers",
    "Wholesale trade": r"wholesal",
    "Retail trade": r"retail|e-?commerce|cashiers?|self-checkout",
    "Transportation, warehousing, and utilities": r"warehous|logistic|truck|freight|transportation (industry|sector|workers)|"
                                                  r"utilit(y|ies) (industry|sector|workers)|delivery drivers?",
    "Information": r"information sector|media industr|newspaper|journalis|telecommunication|software industr|publishing",
    "Financial activities": r"financial (services|sector|industry)|banking|bank employ|insurance (industry|sector)|"
                            r"real estate|fintech",
    "Professional and business services": r"professional services|knowledge work|white-collar|consulting|lawyers|"
                                          r"legal (jobs|profession|services)|accountants?|business services|"
                                          r"software (developers|engineers)|programmers",
    "Private educational services": r"education|teachers?|schools?|universit|colleges?|faculty",
    "Health care and social assistance": r"health|nurs|physician|hospital|care work|home care|social assistance",
    "Arts, entertainment, and recreation": r"\barts\b|artists?|entertainment industr|creative (workers|industr|economy|work)|"
                                           r"musicians?|film industr|performing arts",
    "Accommodation and food services": r"restaurant|hospitality|food service|hotel|tourism",
    "Other services": r"repair|personal services|skilled trades|auto mechanics|beauty|hairdress|laundry",
    "Government": r"public sector|government (employ|workforce|workers|jobs)|public employ|civil service|"
                  r"state and local government",
}
# Medical workforce studies match many words ("distribution", "workforce") but belong to health care only.
MEDICAL = r"physician|pediatric|surgeon|dermatolog|oncolog|otolaryng|nursing|patients?\b"
WORK = r"employ|jobs?\b|workforce|labou?r|workers|hiring|occupation|wages?|staffing|shortage"


def on_topic(paper, industry):
    import re
    text = f"{paper.get('title') or ''} {paper.get('abstract') or paper.get('tldr') or ''}".lower()
    if industry != "Health care and social assistance" and re.search(MEDICAL, text):
        return False
    return bool(re.search(TOPIC.get(industry, "."), text)) and bool(re.search(WORK, text))


def _get(url, params, headers=None, tries=3):
    for i in range(tries):
        r = requests.get(url, params=params, headers=headers or {}, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(120, 5 * 2 ** i))  # the shared free tier is busy: wait it out
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"{url} kept refusing (rate limit)")


def search_s2(query, limit=25, year_from=2019):
    headers = {"x-api-key": os.environ["SEMANTIC_SCHOLAR_API_KEY"]} if os.environ.get("SEMANTIC_SCHOLAR_API_KEY") else {}
    body = _get(S2_URL, {"query": query, "limit": limit, "fields": FIELDS, "year": f"{year_from}-"}, headers)
    out = []
    for p in body.get("data") or []:
        doi = (p.get("externalIds") or {}).get("DOI")
        out.append({"id": "s2:" + p["paperId"], "title": p.get("title"), "year": p.get("year"), "venue": p.get("venue"),
                    "citations": p.get("citationCount") or 0, "abstract": p.get("abstract"),
                    "tldr": (p.get("tldr") or {}).get("text"),
                    "url": f"https://doi.org/{doi}" if doi else p.get("url")})
    return out


def search_crossref(query, limit=60, year_from=2019):
    """Crossref journal articles with an abstract (JATS markup stripped)."""
    import re
    # The dashboard is about US jobs: without this, studies of other countries crowd in.
    body = _get(CROSSREF_URL, {"query.bibliographic": f"{query} United States", "rows": limit, "sort": "relevance",
                               "filter": f"has-abstract:true,from-pub-date:{year_from}-01-01,type:journal-article",
                               "select": "DOI,title,container-title,issued,is-referenced-by-count,abstract"},
                {"User-Agent": "labor-jobs/1.0 (employment tool research notes)"})
    out = []
    for w in (body.get("message") or {}).get("items") or []:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", w.get("abstract") or "")).strip()
        text = re.sub(r"^(Abstract|ABSTRACT)\s*", "", text)
        year = ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        out.append({"id": "doi:" + w["DOI"].lower(), "title": (w.get("title") or [None])[0], "year": year,
                    "venue": (w.get("container-title") or [None])[0], "citations": w.get("is-referenced-by-count") or 0,
                    "abstract": text or None, "tldr": None, "url": f"https://doi.org/{w['DOI']}"})
    return out


def _abstract(inverted):
    if not inverted:
        return None
    words = sorted((pos, w) for w, places in inverted.items() for pos in places)
    return " ".join(w for _, w in words)


def search_openalex(query, limit=25, year_from=2019):
    key = os.environ.get("OPENALEX_API_KEY")
    if not key:
        return []
    body = _get(OA_URL, {"search": query, "per-page": limit, "filter": f"from_publication_date:{year_from}-01-01",
                         "api_key": key})
    out = []
    for w in body.get("results") or []:
        out.append({"id": "oa:" + w["id"].rsplit("/", 1)[-1], "title": w.get("title"), "year": w.get("publication_year"),
                    "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                    "citations": w.get("cited_by_count") or 0, "abstract": _abstract(w.get("abstract_inverted_index")),
                    "tldr": None, "url": w.get("doi") or w["id"]})
    return out


def rank(papers, per_industry=6, today=None):
    """Papers with an abstract, best first: citations on a log scale, newer ones favored."""
    year = (today or date.today()).year
    seen, keep = set(), []
    for p in papers:
        key = (p.get("title") or "").strip().lower()
        if not key or key in seen or not (p.get("abstract") or p.get("tldr")) or len(p.get("abstract") or p.get("tldr")) < 200:
            continue
        seen.add(key)
        age = max(0, year - (p.get("year") or year))
        p["score"] = round(math.log1p(p.get("citations") or 0) - 0.25 * age, 3)
        keep.append(p)
    # Rarely cited papers only when there aren't enough cited ones.
    cited = [p for p in keep if (p.get("citations") or 0) >= 3]
    if len(cited) >= 3:
        keep = cited
    return sorted(keep, key=lambda p: p["score"], reverse=True)[:per_industry]


def fetch(out, industries=None, per_industry=6, pause=2):
    """Candidates per industry, saved after each one. A rerun keeps industries that
    already have 3 or more and retries the rest; a query that keeps failing is skipped."""
    out = Path(out)
    saved = json.loads(out.read_text()) if out.exists() else {}
    result = saved.get("industries", {})
    for industry in industries or QUERIES:
        if len(result.get(industry, [])) >= 3:
            continue
        papers = []
        for q in QUERIES[industry]:
            for search in (search_crossref, search_s2, search_openalex):
                try:
                    papers += search(q)
                except (RuntimeError, requests.RequestException) as exc:
                    print(f"  skipped {search.__name__} '{q}': {exc}")
                time.sleep(pause)
        result[industry] = rank([p for p in papers if on_topic(p, industry)], per_industry)
        print(f"  {industry}: {len(result[industry])} papers", flush=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fetched": date.today().isoformat(), "industries": result}, indent=1))
    return result


def check(candidates, notes, max_chars=900):
    """Dashboard rows from notes, refusing any source that isn't a candidate for that
    industry, notes without 2 to 5 sources, and overlong summaries."""
    cand = candidates["industries"]
    rows, problems = [], []
    for n in notes:
        industry = n.get("industry")
        papers = {p["id"]: p for p in cand.get(industry, [])}
        ids = n.get("sources") or []
        bad = [i for i in ids if i not in papers]
        if industry not in cand:
            problems.append(f"{industry}: not an industry with candidates")
        elif bad:
            problems.append(f"{industry}: sources not among its candidates: {bad}")
        elif not 2 <= len(ids) <= 5:
            problems.append(f"{industry}: needs 2 to 5 sources, has {len(ids)}")
        elif not n.get("summary") or len(n["summary"]) > max_chars:
            problems.append(f"{industry}: summary missing or over {max_chars} characters")
        else:
            rows.append({"industry": industry, "summary": n["summary"].strip(), "updated": candidates["fetched"],
                         "sources": [{k: papers[i].get(k) for k in ("title", "year", "venue", "url", "citations")}
                                     for i in ids]})
    return rows, problems


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.research", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", default="output/research_candidates.json")
    f.add_argument("--per-industry", type=int, default=6)
    c = sub.add_parser("check")
    c.add_argument("--candidates", default="output/research_candidates.json")
    c.add_argument("--notes", required=True, help="JSON list of {industry, summary, sources: [candidate ids]}")
    c.add_argument("--out", default="output/dashboard/industry_research.json")
    c.add_argument("--keep", help="last month's table: its notes stay for industries with no new note")
    args = p.parse_args(argv)
    if args.command == "fetch":
        fetch(args.out, per_industry=args.per_industry)
    else:
        rows, problems = check(json.loads(Path(args.candidates).read_text()), json.loads(Path(args.notes).read_text()))
        if args.keep and Path(args.keep).exists():
            have = {r["industry"] for r in rows}
            rows += [r for r in json.loads(Path(args.keep).read_text()) if r["industry"] not in have]
        for line in problems:
            print("PROBLEM", line)
        Path(args.out).write_text(json.dumps(rows))
        print(f"{len(rows)} notes written to {args.out}" + (f"; {len(problems)} refused" if problems else ""))
        raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
