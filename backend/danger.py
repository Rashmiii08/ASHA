import re

from .schemas import Visit

# WHO IMCI Chart Booklet (2014), young infant assessment, pp. 2–3.
# A newborn is within the guideline's young infant age group.
PHRASES = {
    "unable_to_feed": r"unable to (?:breast)?feed|cannot (?:breast)?feed|not (?:able to feed|feeding well)|दूध नहीं पी",
    "convulsions": r"(?:has|had|having|with) (?:convulsions|seizures)|दौरे (?:पड़|आ)",
}


OTHER_SUBJECT = re.compile(r"\b(?:i|mother|mom|mum|father|parent|caregiver|sibling|brother|sister)\b|माँ|माता|पिता")
INFANT_SUBJECT = re.compile(r"\b(?:baby|infant|newborn)\b|शिशु|नवजात")
NON_AFFIRMATIVE = re.compile(r"\b(?:no|denies|denied|without|history of|if|screen for)\b|दौरे नहीं")
CLAUSE_BREAK = r"[;,!?।\n]|(?<!\d)\.|\.(?!\d)|\b(?:but|however|and (?=(?:the )?(?:baby|infant|newborn|mother|mom|mum|father|parent|caregiver|i)\b))\b"


def newborn_clauses(transcript):
    for clause in re.split(CLAUSE_BREAK, transcript.lower()):
        if not OTHER_SUBJECT.search(clause):
            yield clause
            continue
        infant = INFANT_SUBJECT.search(clause)
        if (infant and not OTHER_SUBJECT.search(clause[infant.start():])
                and not NON_AFFIRMATIVE.search(clause[:infant.start()])):
            yield clause[infant.start():]


def assess(visit: Visit, transcript: str, candidates: list[str]) -> set[str]:
    if visit.visit_type != "newborn":
        return set()

    symptoms = set()
    for clause in newborn_clauses(transcript):
        if NON_AFFIRMATIVE.search(clause):
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
