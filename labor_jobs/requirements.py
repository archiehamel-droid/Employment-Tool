"""Read stated job requirements from posting text, and years of experience
from a resume, for checking a resume against specific postings.

These are patterns over free text, so they find what is written in the
usual ways ("5+ years of experience", "Bachelor's degree required") and
miss unusual phrasing. Anything not found is reported as not stated rather
than guessed.
"""

import re
from datetime import date

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_N = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
YEARS = re.compile(
    rf"(?i)\b{_N}\s*(?:\+|\s*plus)?\s*(?:(?:-|–|to)\s*{_N}\s*\+?)?\s*(?:years?|yrs?)\b"
    r"(?:\s+of)?(?:[\s,/-]+[\w+#.]+){0,5}?[\s,/-]+(?:experience|exp\b|work)")
PREFERRED = re.compile(r"(?i)\b(prefer|preferred|ideally|nice to have|bonus|plus\b|desired)")


PREFERRED_HEADING = re.compile(r"(?i)\b(preferred|nice to have|bonus|desired|ideal candidate|plus)\b")
REQUIRED_HEADING = re.compile(r"(?i)\b(basic|minimum|required|requirements|qualifications|must have|"
                              r"what you('ll)? (need|bring)|who you are|responsibilities|about)\b")
ALTERNATIVE = re.compile(r"(?i)^\W*or\b|\bequivalent\b")


def _num(token):
    return int(token) if token.isdigit() else NUMBER_WORDS[token.lower()]


def required_sentences(text):
    """Sentences of a posting that state requirements: those under a
    "Preferred qualifications" style heading, or saying "preferred"
    themselves, are left out."""
    preferred = False
    for line in text.splitlines():
        # A heading is a short line without sentence punctuation.
        short = len(line.split()) <= 8 and not re.search(r"[.;]", line)
        if short and PREFERRED_HEADING.search(line):
            preferred = True
            continue
        if short and REQUIRED_HEADING.search(line):
            preferred = False
        if preferred:
            continue
        for sentence in re.split(r"(?<=[.;!?])\s+", line):
            if not PREFERRED.search(sentence):
                yield sentence


def years_required(text):
    """Minimum years of experience a posting requires, or None if it states
    none. Preferred qualifications are skipped. Separate requirements take
    the largest ("3+ years of Python, 5+ years experience" -> 5); alternative
    paths ("... or 8+ years of equivalent experience") count only when
    nothing else is stated, and then the smallest."""
    if not isinstance(text, str):
        return None
    required, alternatives = [], []
    for sentence in required_sentences(text):
        for m in YEARS.finditer(sentence):
            years = _num(m.group(1))
            if 0 < years <= 20:
                (alternatives if ALTERNATIVE.search(sentence) else required).append(years)
    if required:
        return max(required)
    return min(alternatives) if alternatives else None


DEGREE_LEVELS = [  # (pattern, level), highest first; same scale as majors.education_level
    (r"(?i)\b(ph\.?\s?d|doctorate|doctoral)\b", 5),
    (r"(?i)\b(master'?s|m\.s\.|msc|mba)\b", 4),
    (r"(?i)\b(bachelor'?s|b\.s\.|b\.a\.|bs/ba|ba/bs|4[- ]year degree|undergraduate degree|college degree)\b", 3),
    (r"(?i)\b(associate'?s degree|2[- ]year degree)\b", 2),
    (r"(?i)\b(high school diploma|ged)\b", 0),
]
# "or equivalent experience", "or 7+ years of equivalent professional experience";
# not "high school diploma or equivalency certificate".
EQUIVALENT = re.compile(r"(?i)\bor\b[^.;]{0,60}?\b(equivalent|comparable)\b[^.;]{0,40}?\b(experience|combination)\b")
DEGREE_NAMES = {5: "Doctorate", 4: "Master's", 3: "Bachelor's", 2: "Associate's", 0: "High school"}


def degree_required(text):
    """(level, flexible) for the lowest degree a posting requires, or (None,
    False). flexible is True when it accepts equivalent experience instead.
    A degree mentioned only as preferred is not a requirement."""
    if not isinstance(text, str):
        return None, False
    levels, flexible = [], False
    for sentence in required_sentences(text):
        for pattern, level in DEGREE_LEVELS:
            if re.search(pattern, sentence):
                levels.append(level)
                flexible = flexible or bool(EQUIVALENT.search(sentence))
                break
    return (min(levels), flexible) if levels else (None, False)


SENIORITY = [  # title words -> typical minimum years, for postings that state none;
    # checked in order, so "Associate Director" is a director and "Senior Associate" senior
    (r"(?i)\b(intern|internship|co-?op)\b", 0, "Internship"),
    (r"(?i)\b(principal|distinguished|staff)\b", 8, "Staff or principal"),
    (r"(?i)\b(director|head of|vice president|vp)\b", 8, "Director or above"),
    (r"(?i)\b(senior|sr\.?|lead|manager)\b", 5, "Senior"),
    (r"(?i)\b(entry[- ]level|junior|jr\.?|associate|graduate|new grad|trainee|apprentice)\b", 0, "Entry level"),
]


def seniority(title):
    """(typical minimum years, label) implied by a posting title, or (None, None)."""
    if not isinstance(title, str):
        return None, None
    for pattern, years, label in SENIORITY:
        if re.search(pattern, title):
            return years, label
    return None, None


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+|(\d{1,2})/)?((?:19|20)\d{2})"
RANGE = re.compile(rf"(?i){_DATE}\s*(?:-|–|—|to)\s*(?:{_DATE}|(present|current|now|today))")
SCHOOL = re.compile(r"(?i)\b(university|college|school|academy|institute|b\.s\.|b\.a\.|bachelor|master|"
                    r"degree|gpa|coursework|ph\.?d)\b")


def _month(mon, num, year):
    if mon:
        return int(year) * 12 + MONTHS[mon[:3].lower()] - 1
    if num:
        return int(year) * 12 + min(max(int(num), 1), 12) - 1
    return int(year) * 12  # a bare year counts from January


def resume_years(text, today=None):
    """Years of work experience from the date ranges on a resume ("Jan 2020 -
    Present", "2018 - 2021"), with overlapping jobs counted once and lines
    about schooling left out. None when the resume has no date ranges."""
    if not isinstance(text, str):
        return None
    today = today or date.today()
    now = today.year * 12 + today.month - 1
    spans = []
    for line in text.splitlines():
        if SCHOOL.search(line):
            continue
        for m in RANGE.finditer(line):
            start = _month(m.group(1), m.group(2), m.group(3))
            if m.group(7):
                end = now
            else:
                end = _month(m.group(4), m.group(5), m.group(6))
                if not (m.group(4) or m.group(5)):
                    end += 11  # "2018 - 2021" runs to the end of 2021
            if start <= end <= now + 1:
                spans.append((start, min(end, now)))
    if not spans:
        return None
    months, cur_start, cur_end = 0, None, None
    for start, end in sorted(spans):
        if cur_end is None or start > cur_end + 1:
            if cur_end is not None:
                months += cur_end - cur_start + 1
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    months += cur_end - cur_start + 1
    return round(months / 12, 1)
