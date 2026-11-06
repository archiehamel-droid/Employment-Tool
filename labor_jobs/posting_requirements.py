"""Credentials and soft skills that job postings ask for.

Postings are the most direct record of what employers screen for. This reads
each posting's text for:

* credentials: licenses, certifications and clearances ("RN license", "CDL
  Class A", "CPA", "Security+", "Secret clearance"), split into required and
  preferred using the same sentence rules as requirements.py
* soft skills: communication, attention to detail, teamwork and so on, each
  tied to the Big Five trait it reflects, so personality test results can be
  compared with what employers actually write, not only with O*NET's ratings

The same patterns read credentials from a resume. Patterns match what's
usually written; unusual phrasing is missed, and anything not found counts as
not stated.
"""

import re

from .requirements import required_sentences

# name -> (pattern, kind). Patterns are case-sensitive where the short form is
# an everyday word or common letters ("PE", "RN"), case-insensitive otherwise.
CREDENTIALS = {
    "Registered nurse (RN) license": (r"\bRN\b(?!\s*[-–]?\s*to\b)|(?i:registered\s+nurse\s+licen[sc]e|licensed\s+as\s+a\s+registered\s+nurse)", "license"),  # noqa: E501
    "LPN/LVN license": (r"\bL[PV]N\b|(?i:licensed\s+(?:practical|vocational)\s+nurse)", "license"),
    "Nurse practitioner (APRN) license": (r"\b(?:APRN|FNP-?BC|AGNP|PMHNP)\b|(?i:nurse\s+practitioner\s+licen[sc]e)", "license"),  # noqa: E501
    "Physician assistant (PA-C)": (r"\bPA-C\b|(?i:NCCPA)", "certification"),
    "Pharmacist license": (r"(?i:pharmacist\s+licen[sc]e|licensed\s+pharmacist|\bR\.?Ph\b)", "license"),
    "Nursing assistant (CNA)": (r"\bCNA\b|(?i:certified\s+nursing\s+assistant)", "certification"),
    # A bare "CMA" is just as often a Certified Management Accountant.
    "Medical assistant (CMA/RMA)": (r"\b(?:RMA|CCMA)\b|(?i:certified\s+medical\s+assistant|CMA\s*\(AAMA\))", "certification"),  # noqa: E501
    "BLS/CPR": (r"\bBLS\b|\bCPR\b|(?i:basic\s+life\s+support)", "certification"),
    "ACLS/PALS": (r"\b(?:ACLS|PALS|NRP)\b", "certification"),
    "Medical coding (CPC/CCS)": (r"\b(?:CPC|CCS|RHIT|RHIA)\b", "certification"),
    "Medical lab (ASCP)": (r"\bASCP\b|\bMLS\b|(?i:medical\s+technologist\s+certification)", "certification"),
    "Commercial driver's license (CDL)": (r"\bCDL\b|(?i:commercial\s+driver'?s?\s+licen[sc]e)", "license"),
    "Driver's license": (r"(?i:valid\s+(?:state\s+)?driver'?s?\s+licen[sc]e)", "license"),
    "Forklift certification": (r"(?i:forklift\s+(?:certified|certification|license)|certified\s+forklift)", "certification"),  # noqa: E501
    "OSHA 10/30": (r"\bOSHA\s*-?\s*(?:10|30)\b", "certification"),
    "Electrician license": (r"(?i:(?:journeyman|master)\s+electrician(?:'?s)?\s+licen[sc]e|electrical\s+licen[sc]e|licensed\s+(?:journeyman\s+|master\s+)?electrician)", "license"),  # noqa: E501
    "HVAC (EPA 608)": (r"\bEPA\s*(?:Section\s*)?608\b", "certification"),
    "Professional Engineer (PE/EIT)": (r"\b(?:P\.E\.|PE|EIT|FE)\s+(?:licen[sc]e|license|registration|certification|certified|exam)\b|(?i:professional\s+engineer(?:ing)?\s+licen[sc]e|engineer[- ]in[- ]training)", "license"),  # noqa: E501
    "CPA": (r"\bCPA\b|(?i:certified\s+public\s+accountant)", "license"),
    "CFA": (r"\bCFA\b", "certification"),
    "FINRA licenses (Series 7/63/65/66)": (r"(?i:series\s*(?:6|7|24|63|65|66)\b)", "license"),
    "Insurance producer license": (r"(?i:(?:life|health|property|casualty|insurance)\s+(?:and\s+\w+\s+)?(?:producer\s+)?licen[sc]e)", "license"),  # noqa: E501
    "Real estate license": (r"(?i:real\s+estate\s+(?:salesperson\s+|broker\s+)?licen[sc]e)", "license"),
    "Bar admission (law license)": (r"(?i:admitted\s+to\s+(?:practice|the\s+\w+\s+bar|a\s+state\s+bar)|member\s+(?:in\s+good\s+standing\s+)?of\s+(?:the\s+|a\s+)?(?:\w+\s+)?(?:state\s+)?bar|bar\s+admission|licensed\s+to\s+practice\s+law)", "license"),  # noqa: E501
    "USPTO registration (patent bar)": (r"(?i:registered\s+(?:to\s+practice\s+)?(?:before|with)\s+the\s+(?:USPTO|united\s+states\s+patent)|patent\s+bar|registered\s+patent\s+(?:agent|attorney))", "license"),  # noqa: E501
    "Teaching license": (r"(?i:teaching\s+(?:licen[sc]e|certificat(?:e|ion)|credential)|state\s+teacher\s+certification)", "license"),  # noqa: E501
    "Social work license (LCSW/LMSW)": (r"\b(?:LCSW|LMSW|LSW|LICSW)\b", "license"),
    "Counseling license (LPC/LMHC/LMFT)": (r"\b(?:LPC|LPCC|LMHC|LMFT|LCPC|LADC|CADC|CASAC)\b", "license"),
    "SHRM-CP/SCP or PHR/SPHR": (r"\b(?:SHRM-?S?CP|S?PHR)\b", "certification"),
    "PMP": (r"\bPMP\b|(?i:project\s+management\s+professional)", "certification"),
    "Scrum/Agile certification": (r"\b(?:CSM|PSM|SAFe)\b|(?i:certified\s+scrum)", "certification"),
    "Six Sigma": (r"(?i:six\s+sigma|lean\s+six)", "certification"),
    "CompTIA (A+/Network+/Security+)": (r"(?i:comptia|security\+|network\+|\bA\+\s+certif)", "certification"),
    "CISSP/CISM/CISA": (r"\b(?:CISSP|CISM|CISA|CEH|OSCP)\b", "certification"),
    "Cloud certification (AWS/Azure/GCP)": (r"(?i:aws\s+certified|azure\s+(?:administrator|solutions\s+architect|fundamentals)\s+(?:certification|associate)|google\s+cloud\s+certified|certified\s+kubernetes)", "certification"),  # noqa: E501
    "Cisco (CCNA/CCNP)": (r"\b(?:CCNA|CCNP|CCIE)\b", "certification"),
    "ITIL": (r"\bITIL\b", "certification"),
    "Food safety (ServSafe)": (r"(?i:servsafe|food\s+(?:handler'?s?|safety|manager)\s+(?:card|certificat(?:e|ion)|permit))", "certification"),  # noqa: E501
    "Security clearance": (r"(?i:(?:secret|top\s+secret|TS/SCI|ts\s*/\s*sci)\s+(?:security\s+)?clearance|active\s+(?:security\s+)?clearance|ability\s+to\s+obtain\s+(?:and\s+maintain\s+)?(?:a\s+)?(?:security\s+)?clearance)|\bTS/SCI\b", "clearance"),  # noqa: E501
    "Notary": (r"(?i:notary\s+public)", "license"),
}
# name -> (pattern, Big Five trait it reflects). Case-insensitive.
SOFT_SKILLS = {
    "Communication": (r"communication\s+skills|communicat\w+\s+(?:clearly|effectively)|written\s+and\s+(?:oral|verbal)|verbal\s+and\s+written", "E"),  # noqa: E501
    "Customer service": (r"customer[- ]service|customer[- ]facing|client[- ]facing|guest\s+service", "A"),
    "Teamwork": (r"team\s*work|team\s+player|collaborat\w+|work\s+well\s+with\s+others|cross[- ]functional", "A"),
    "Leadership": (r"leadership|lead\s+(?:a|the)\s+team|mentor\w*|supervis\w+\s+(?:staff|employees|a\s+team)", "E"),
    "Attention to detail": (r"attention\s+to\s+detail|detail[- ]oriented|accura(?:te|cy)\s+and|meticulous", "C"),
    "Organization and time management": (r"organi[sz]ational\s+skills|time[- ]management|prioritiz\w+|multi-?task\w*|meet\s+deadlines", "C"),  # noqa: E501
    "Problem solving": (r"problem[- ]solv\w+|critical\s+thinking|analytical\s+skills|troubleshoot\w*", "O"),
    "Adaptability": (r"adaptab\w+|flexib(?:le|ility)|fast[- ]paced|changing\s+priorities|ambiguity", "N"),
    "Composure under pressure": (r"under\s+pressure|stressful|high[- ]pressure|remain\s+calm|composure", "N"),
    "Creativity": (r"creativ\w+|innovat\w+|out\s+of\s+the\s+box", "O"),
    "Initiative": (r"self[- ]start\w*|self[- ]motivat\w+|take\s+initiative|proactive|independently", "C"),
    "Dependability": (r"reliab(?:le|ility)|dependab\w+|punctual|strong\s+work\s+ethic", "C"),
    "Empathy": (r"empath\w+|compassion\w*|patient[- ]centered|care\s+for\s+others", "A"),
    "Persuasion and negotiation": (r"negotiat\w+|persua\w+|influenc\w+\s+(?:stakeholders|others)|closing\s+deals", "E"),
    "Presenting": (r"presentation\s+skills|public\s+speaking|present\s+to\s+(?:executives|leadership|clients)", "E"),
}
_CRED = {name: re.compile(pattern) for name, (pattern, _) in CREDENTIALS.items()}
_SOFT = {name: re.compile(pattern, re.I) for name, (pattern, _) in SOFT_SKILLS.items()}


def credentials_in(text):
    """Credential names mentioned anywhere in a text, sorted (for resumes)."""
    if not isinstance(text, str):
        return []
    return sorted(name for name, pattern in _CRED.items() if pattern.search(text))


def posting_requirements(text):
    """(required credentials, preferred credentials, soft skills) for a posting's
    text, each a sorted list. A credential named only in preferred sentences or
    sections is preferred."""
    if not isinstance(text, str) or not text.strip():
        return [], [], []
    required_text = "\n".join(required_sentences(text))
    mentioned = credentials_in(text)
    required = [c for c in mentioned if _CRED[c].search(required_text)]
    preferred = [c for c in mentioned if c not in required]
    soft = sorted(name for name, pattern in _SOFT.items() if pattern.search(text))
    return required, preferred, soft


def occupation_summary(postings, min_postings=10, top=8):
    """Per O*NET code, over postings whose text was read: how many there were,
    the credentials most often required and preferred, the soft skills most
    often asked for (with shares), and the share asking for skills linked to
    each Big Five trait. Columns hold "name 42%; name 30%" strings."""
    has = postings[postings["text_read"]]
    rows = []
    for code, group in has.groupby("onet_code"):
        n = len(group)
        if n < min_postings:
            continue
        row = {"onet_code": code, "postings_with_text": n}
        for column, out in (("credentials_required", "credentials_required"),
                            ("credentials_preferred", "credentials_preferred"), ("soft_skills", "soft_skills")):
            counts = group[column].fillna("").str.split("; ").explode()
            counts = counts[counts != ""].value_counts()
            row[out] = "; ".join(f"{name} {100 * k / n:.0f}%" for name, k in counts.head(top).items())
        soft = group["soft_skills"].fillna("").str.split("; ")
        for trait in "OCEAN":
            names = {name for name, (_, t) in SOFT_SKILLS.items() if t == trait}
            row[f"soft_{trait}"] = round(float(soft.map(lambda s: bool(names & set(s))).mean()), 3)
        rows.append(row)
    return rows
