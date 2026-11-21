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


def reported_terms(text):
    text = text.casefold().strip()
    reports = []
    for term, expression in TERMS.items():
        pattern = rf"(?:^|[.!?;]|\band\s+)\s*(?:i (?:have|got)|i['’]ve got)\s+(?:{expression})(?=\s*(?:[.!?;]|$|\band\s+i\b))"
        if re.search(pattern, text):
            reports.append(term)
    ambiguous = not reports and bool(re.fullmatch(r"(?:my )?(?:sugar|bp|pressure)(?: problem)?[.?! ]*", text))
    return reports, ambiguous
