import re

from .schemas import Visit

# WHO IMCI Chart Booklet (2014), young infant assessment, pp. 2–3.
# A newborn is within the guideline's young infant age group.
PHRASES = {
    "unable_to_feed": r"unable to (?:breast)?feed|cannot (?:breast)?feed|not (?:able to feed|feeding well)|दूध नहीं पी",
    "convulsions": r"(?:has|had|having|with) (?:convulsions|seizures)|दौरे (?:पड़|आ)",
}


def assess(visit: Visit, transcript: str, candidates: list[str]) -> set[str]:
    if visit.visit_type != "newborn":
        return set()

    symptoms = set()
    for clause in re.split(r"[.;,!?।\n]|\b(?:but|however)\b", transcript.lower()):
        if re.search(r"\b(?:no|denies|without|history of|if|screen for)\b|दौरे नहीं", clause):
            continue
        for sign, pattern in PHRASES.items():
            if re.search(pattern, clause):
                symptoms.add(sign)

    # Candidate labels are accepted only with deterministic evidence.
    signs = symptoms.intersection(candidates)
    signs.update(symptoms)
    if visit.temperature_c is not None:
        if visit.temperature_c >= 37.5:
            signs.add("newborn_high_temperature")
        if visit.temperature_c < 35.5:
            signs.add("newborn_low_temperature")
    return signs
