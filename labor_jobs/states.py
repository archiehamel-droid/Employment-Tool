"""US state names, postal abbreviations and FIPS codes."""

STATES = [
    ("AL", "Alabama", 1), ("AK", "Alaska", 2), ("AZ", "Arizona", 4),
    ("AR", "Arkansas", 5), ("CA", "California", 6), ("CO", "Colorado", 8),
    ("CT", "Connecticut", 9), ("DE", "Delaware", 10),
    ("DC", "District of Columbia", 11), ("FL", "Florida", 12),
    ("GA", "Georgia", 13), ("HI", "Hawaii", 15), ("ID", "Idaho", 16),
    ("IL", "Illinois", 17), ("IN", "Indiana", 18), ("IA", "Iowa", 19),
    ("KS", "Kansas", 20), ("KY", "Kentucky", 21), ("LA", "Louisiana", 22),
    ("ME", "Maine", 23), ("MD", "Maryland", 24), ("MA", "Massachusetts", 25),
    ("MI", "Michigan", 26), ("MN", "Minnesota", 27), ("MS", "Mississippi", 28),
    ("MO", "Missouri", 29), ("MT", "Montana", 30), ("NE", "Nebraska", 31),
    ("NV", "Nevada", 32), ("NH", "New Hampshire", 33), ("NJ", "New Jersey", 34),
    ("NM", "New Mexico", 35), ("NY", "New York", 36),
    ("NC", "North Carolina", 37), ("ND", "North Dakota", 38), ("OH", "Ohio", 39),
    ("OK", "Oklahoma", 40), ("OR", "Oregon", 41), ("PA", "Pennsylvania", 42),
    ("RI", "Rhode Island", 44), ("SC", "South Carolina", 45),
    ("SD", "South Dakota", 46), ("TN", "Tennessee", 47), ("TX", "Texas", 48),
    ("UT", "Utah", 49), ("VT", "Vermont", 50), ("VA", "Virginia", 51),
    ("WA", "Washington", 53), ("WV", "West Virginia", 54),
    ("WI", "Wisconsin", 55), ("WY", "Wyoming", 56),
]

ABBRS = {abbr for abbr, _, _ in STATES}
NAME_TO_ABBR = {name.lower(): abbr for abbr, name, _ in STATES}
FIPS_TO_ABBR = {fips: abbr for abbr, _, fips in STATES}

# Total employment in thousands, BLS OEWS May 2024. Used to weight samples
# across states.
EMPLOYMENT_THOUSANDS = {
    "AL": 2091, "AK": 321, "AZ": 3197, "AR": 1289, "CA": 18058, "CO": 2891, "CT": 1682, "DE": 476,
    "DC": 709, "FL": 9820, "GA": 4856, "HI": 621, "ID": 845, "IL": 6065, "IN": 3187, "IA": 1562,
    "KS": 1431, "KY": 1994, "LA": 1912, "ME": 635, "MD": 2746, "MA": 3643, "MI": 4391, "MN": 2920,
    "MS": 1160, "MO": 2918, "MT": 510, "NE": 1016, "NV": 1529, "NH": 683, "NJ": 4250, "NM": 861,
    "NY": 9542, "NC": 4898, "ND": 424, "OH": 5526, "OK": 1692, "OR": 1966, "PA": 6014, "RI": 494,
    "SC": 2272, "SD": 453, "TN": 3274, "TX": 13847, "UT": 1710, "VT": 304, "VA": 4065, "WA": 3540,
    "WV": 701, "WI": 2923, "WY": 278,
}
