"""Deterministic validation for spoken identifiers and follow-up requests."""

import re

from .scheduling import local_today, spoken_date
from .schemas import VaccinationPlan

ID_MARKER = r"(?:beneficiary id|लाभार्थी आईडी)"
ID_VALUE = re.compile(rf"{ID_MARKER}\s*(?:is\b|है|[:=])?\s*([^\s,;!?।]+(?:\s+\d+)*)(?![\w-])", re.I)
VISIT_DATE = re.compile(r"\b(?:next visit|follow[ -]?up|अगली विज़िट)\s*(?:is\s+)?(.+)", re.I)
VACCINATION = re.compile(
    r"^(?:the\s+)?(?:(?:next)\s+)?(?:(?P<name>[a-z][a-z0-9-]*(?:\s+[a-z0-9-]+){0,3})\s+)?"
    r"(?:vaccines?|vaccinations?|immuni[sz]ations?)\s+(?:is\s+|was\s+)?"
    r"(?P<status>due\b|given\b|administered\b|in\b|after\b|on\b|next\b|tomorrow\b)(?P<tail>.*)$",
    re.I,
)
DATE_TAIL = r"\s+(?:and\s+)?(?=(?:beneficiary id|next visit|follow[ -]?up|weight|temperature|bp)\b)"


def input_fields(text, *, today=None, waiting=None):
    today = today or local_today()
    patch, issues = {}, {}
    identifier = ID_VALUE.search(text)
    bare_id = text.strip().rstrip(".!") if waiting == "beneficiary_id" and re.fullmatch(r"[\w-]+[.!]?", text.strip()) else None
    if identifier or bare_id or re.search(ID_MARKER, text, re.I):
        value = identifier[1].rstrip(".") if identifier else bare_id
        if value and re.fullmatch(r"[0-9]{4}", value):
            patch["beneficiary_id"] = value
        else:
            issues["beneficiary_id"] = "Please repeat the beneficiary ID. It must contain exactly four numeric digits."
    plans = []
    clear_vaccinations = False
    clauses = re.split(r"[;!?।\n]|\.(?!\d)|,\s*(?!\d{4}\b)", text)
    for clause in clauses:
        clause = clause.strip()
        if re.fullmatch(r"no (?:next visit|follow[ -]?up)(?: (?:is )?(?:required|needed|scheduled))?", clause, re.I):
            patch["next_visit_date"] = None
            issues.pop("next_visit_date", None)
            continue
        if re.fullmatch(r"no (?:vaccines?|vaccinations?|immuni[sz]ations?) (?:are )?due", clause, re.I):
            plans.clear()
            clear_vaccinations = True
            issues.pop("vaccination_plans", None)
            patch["vaccinations_due"] = []
            continue
        date_match = VISIT_DATE.search(clause)
        if date_match:
            try:
                patch["next_visit_date"] = spoken_date(re.split(DATE_TAIL, date_match[1], maxsplit=1, flags=re.I)[0], today=today)
                issues.pop("next_visit_date", None)
            except ValueError:
                issues["next_visit_date"] = "Please give a clear next-visit date that is today or later."
        vaccine = VACCINATION.search(clause.strip())
        if not vaccine:
            if re.search(r"\b(?:vaccine|vaccination|immuni[sz]ation)\b.*\b(?:due|given|administered)\b", clause, re.I):
                issues["vaccination_plans"] = "Please clarify the vaccine name, status, and due date if known."
            continue
        name = vaccine["name"]
        if name and name.casefold() in {"this", "that", "some", "unknown", "a", "the"}:
            name = None
        status = vaccine["status"].lower()
        tail = vaccine["tail"].strip()
        if status in {"given", "administered"}:
            if name:
                plans.append(VaccinationPlan(name=name.upper(), status="given"))
            else:
                issues["vaccination_plans"] = "Which vaccine was given?"
            continue
        when = tail if status == "due" else f"{status} {tail}".strip()
        try:
            due = spoken_date(when, today=today) if when else None
            plans.append(VaccinationPlan(name=name.upper() if name else None, status="due", due_date=due))
        except ValueError:
            issues["vaccination_plans"] = "Please clarify the vaccination due date. It must be today or later."
    if waiting == "next_visit_date" and "next_visit_date" not in patch and "next_visit_date" not in issues:
        try:
            patch["next_visit_date"] = spoken_date(text.strip().rstrip(".!?"), today=today)
        except ValueError:
            pass
    if clear_vaccinations and not plans:
        patch["vaccination_plans"] = []
    elif plans:
        patch["vaccination_plans"] = plans
    if waiting == "weight_kg" or re.search(r"\b(?:kg|kilograms?)\b", text, re.I):
        weight = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:kg|kilograms?)?[.!]?\s*", text, re.I)
        if weight:
            patch["weight_kg"] = float(weight[1])
    return patch, issues
