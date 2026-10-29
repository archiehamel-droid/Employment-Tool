"""Builds the job-title matcher from its reference data, shared by the main
report and the resume/major matcher."""

import pandas as pd

from . import skills
from .soc_mapping import TitleMatcher


def build_matcher(oews_national, onet_titles, profiles, technologies, overrides,
                  fuzzy_cutoff=101, employer_titles=None):
    oews_titles = oews_national.loc[oews_national["o_group"] == "detailed", ["occ_title", "soc_code"]]
    reference = pd.concat([
        pd.DataFrame({"title": oews_titles["occ_title"], "onet_code": oews_titles["soc_code"]}),
        onet_titles])
    employment = dict(zip(oews_national["soc_code"], oews_national["tot_emp"]))
    tech_names, names = None, None
    if technologies is not None:
        tech_names = skills.title_technologies(
            technologies, pd.concat([profiles["onet_title"], oews_titles["occ_title"]]))
    if profiles is not None:
        names = dict(zip(profiles["onet_code"], profiles["onet_title"]))
    return TitleMatcher(reference, overrides, fuzzy_cutoff, employment, tech_names, names,
                        employer_titles)
