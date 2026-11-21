"""Conservative mappings for explicitly reported concerns."""

import re

TERMS = {
    "Diabetes": r"sugar(?: problem| disease)?|diabetes",
    "Hypertension": r"high (?:bp|blood pressure)",
    "Low blood pressure": r"low (?:bp|blood pressure)",
    "Fever": r"(?:a )?fever",
    "Difficulty breathing": r"difficulty (?:in )?breathing|trouble breathing",
    "Seizures": r"fits|seizures",
}
DIAGNOSIS_QUESTIONS = {"Diabetes", "Hypertension", "Low blood pressure", "Seizures"}


PATIENT = r"(?:the )?(?:patient|beneficiary)"
REPORT = re.compile(
    rf"^(?:(?P<patient>{PATIENT}) (?:has|reports?|says (?:they have|he has|she has))"
    r"|(?P<first>i (?:have|got)|i['’]ve got)"
    r"|(?P<uncertain>(?:he|she|they|the person) (?:has|have)))\s+(?P<concerns>.+)$"
)
SUBJECT_BOUNDARY = rf"\b(?:and|but)\s+(?=(?:{PATIENT}|i|he|she|they|(?:the )?(?:caregiver|mother|father))\b)"
PATIENT_SPEAKER = re.compile(r"\b(?:i am|i['’]m)\s+(?:the )?(?:patient|beneficiary)\b", re.I)
CAREGIVER = re.compile(r"\b(?:i am|i['’]m|speaking as)\s+(?:the |a )?(?:caregiver|mother|father|parent)\b", re.I)


def reported_terms(text, *, first_person_patient=True):
    text = text.casefold().strip()
    mixed_speakers = bool(
        CAREGIVER.search(text) or (re.search(rf"\b{PATIENT}\b", text) and not PATIENT_SPEAKER.search(text))
    )
    reports = []
    ambiguous = False
    for clause in re.split(rf"[.!?;]|{SUBJECT_BOUNDARY}", text):
        clause = clause.strip(" ,")
        match = REPORT.fullmatch(clause)
        if not match:
            ambiguous |= bool(re.fullmatch(r"(?:my )?(?:sugar|bp|pressure)(?: problem)?", clause))
            continue
        items = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", match["concerns"])
        terms = [next((term for term, expression in TERMS.items() if re.fullmatch(expression, item.strip())), None)
                 for item in items]
        if all(terms):
            if match["uncertain"] or (match["first"] and (mixed_speakers or not first_person_patient)):
                ambiguous = True
            else:
                reports.extend(terms)
        elif match["patient"] and any(re.fullmatch(r"sugar|bp|pressure", item.strip()) for item in items):
            ambiguous = True
    return list(dict.fromkeys(reports)), ambiguous
