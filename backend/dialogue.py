import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid4

from pydantic import ValidationError

from .danger import OTHER_SUBJECT, assess, newborn_clauses
from .phrases import PHRASES
from .schemas import Extraction, Visit, VisitReview
from .terminology import CAREGIVER, DIAGNOSIS_QUESTIONS, PATIENT_SPEAKER, TERMS, reported_terms

CONFIRM = {
    "save", "save it", "yes save it", "yes save", "confirm",
    "हाँ सेव करें", "हां सेव करें", "सेव करें",
}


YES_ANSWERS = {
    "yes", "yes they have", "yes they did", "correct", "that's correct",
    "they have", "they did", "हाँ", "हां",
}
NO_ANSWERS = {
    "no", "not yet", "no not yet", "no they haven't", "no they have not",
    "they haven't", "they have not", "no they didn't", "never", "नहीं", "नही",
}
UNCLEAR_ANSWERS = {
    "maybe", "not sure", "i don't know", "i am not sure", "i'm not sure", "unsure",
    "i think so", "yes and no", "yes but i'm not sure",
}
OTHER_CONCERNS_QUESTION = "Do you have any other health concerns?"


def spoken_command(text):
    words = re.sub(r"[^\w\s\u0900-\u097f']", " ", text.casefold().replace("’", "'"))
    return " ".join(words.split())


GENERAL_HEALTH = re.compile(r"\bgeneral health visit\b", re.I)


def deterministic(text: str) -> Extraction:
    text = text.lower()
    patch = {}
    identifier = re.search(r"(?:beneficiary id|लाभार्थी आईडी)\s*(?:is|है|[:=])?\s*([a-z0-9][a-z0-9_-]{0,79})(?![a-z0-9_-])", text)
    if identifier and any(char.isdigit() for char in identifier[1]):
        patch["beneficiary_id"] = identifier[1].upper()
    categories = {
        "antenatal": r"\bantenatal\b|गर्भावस्था",
        "postnatal": r"\bpostnatal\b|प्रसव के बाद",
        "newborn": r"\bnewborn\b|नवजात",
        "immunisation": r"\bimmuni[sz]ation\b|टीकाकरण",
        "general_health": GENERAL_HEALTH,
        "other": r"\bother visit\b|अन्य विज़िट",
    }
    for category, pattern in categories.items():
        if re.search(pattern, text):
            patch["visit_type"] = category
            break
    for key, label in (("weight_kg", "weight|वज़न|वजन"), ("temperature_c", "temperature|तापमान")):
        match = re.search(rf"(?:{label})\s*(?:is|है|[:=])?\s*(\d+(?:\.\d+)?)", text)
        if match:
            patch[key] = float(match[1])
    pressure = re.search(r"(?:\bbp\b|blood pressure|रक्तचाप)\s*(?:is|है|[:=])?\s*(\d+)\s*(?:/|over)\s*(\d+)", text)
    if pressure:
        patch["bp_systolic"], patch["bp_diastolic"] = map(int, pressure.groups())
    due = re.search(r"(?:vaccinations? due|vaccines? due|बाकी टीके)\s*(?:are|हैं|[:=])?\s*([a-z0-9-]+(?:\s*(?:,|and)\s*[a-z0-9-]+)*)", text)
    if due:
        patch["vaccinations_due"] = [x.strip().upper() for x in re.split(r",|\band\b", due[1])]
    next_visit = re.search(r"(?:next visit|अगली विज़िट)\s*(?:is|on|है|[:=])?\s*(\d{4}-\d{2}-\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text)
    if next_visit:
        day = next_visit[1]
        weekdays = "monday tuesday wednesday thursday friday saturday sunday".split()
        if day in weekdays:
            today = date.today()
            day = today + timedelta(days=(weekdays.index(day) - today.weekday()) % 7 or 7)
        patch["next_visit_date"] = day
    return Extraction(intent="provide_info", patch=Visit.model_validate(patch))


@dataclass
class Dialogue:
    language: str
    draft_id: str = field(default_factory=lambda: str(uuid4()))
    visit: Visit = field(default_factory=Visit)
    state: str = "LISTENING"
    notes: list[str] = field(default_factory=list)
    danger: set[str] = field(default_factory=set)
    conditions: dict[str, str] = field(default_factory=dict)
    pending_condition: str | None = None
    remaining_questions: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    caregiver_speaking: bool = False
    awaiting_other_concerns: bool = False
    transcript_correction: str | None = None

    def pending_diagnoses(self):
        return [term for term in [self.pending_condition, *self.remaining_questions] if term is not None]

    def save_blockers(self):
        blockers = []
        if not self.visit.beneficiary_id:
            blockers.append("Enter a beneficiary ID.")
        if not self.visit.visit_type:
            blockers.append("Select a visit type.")
        if self.pending_diagnoses():
            blockers.append("Answer the diagnosis questions or remove the incorrect concerns.")
        if self.needs_clarification:
            blockers.append("Clarify the uncertain report or explicitly exclude it from this visit.")
        return blockers

    def apply_review(self, review: VisitReview):
        selected = set(review.retained_conditions)
        pending = self.pending_diagnoses()
        if (not selected <= self.conditions.keys() or not set(review.added_conditions) <= TERMS.keys()
                or not review.diagnosis_answers.keys() <= selected.intersection(pending)):
            raise ValueError("Unknown reported concern or clarification")
        changes = review.model_dump(include=set(Visit.model_fields), exclude_unset=True)
        self.visit = Visit.model_validate({**self.visit.model_dump(), **changes})
        self.conditions = {term: source for term, source in self.conditions.items() if term in selected}
        for term in review.added_conditions:
            self.conditions.setdefault(term, "patient_reported")
        for term, diagnosed in review.diagnosis_answers.items():
            if diagnosed:
                self.conditions[term] = "patient_reported_previous_diagnosis"
        unanswered = [term for term in pending if term in selected and term not in review.diagnosis_answers]
        self.pending_condition = unanswered[0] if unanswered else None
        self.remaining_questions = unanswered[1:]
        if review.exclude_unclear_report:
            self.needs_clarification = False
        if "transcript_correction" in review.model_fields_set:
            self.transcript_correction = (review.transcript_correction or "").strip() or None
        self.awaiting_other_concerns = False
        original = "\n".join(self.notes)
        self.danger = assess(self.visit, original, [])
        if self.transcript_correction:
            self.danger |= assess(self.visit, self.transcript_correction, [])
        self.state = "LISTENING" if self.save_blockers() else "CONFIRMING"

    def diagnosis_question(self):
        return f"You mentioned {self.pending_condition}. Has a healthcare professional previously diagnosed this condition?"

    def condition_details(self):
        return [{"term": term, "source": source} for term, source in self.conditions.items()]

    async def confirm(self, store):
        if self.state == "SAVED":
            return self.phrases["saved"]
        if self.state != "CONFIRMING" or self.save_blockers():
            return self.phrases["missing"] if not (self.visit.beneficiary_id and self.visit.visit_type) else self.phrases["unclear"]
        await store(self)
        self.state = "SAVED"
        return self.phrases["saved"]

    @property
    def phrases(self):
        return PHRASES[self.language]

    async def process(self, text, extract, store, *, allow_save=True):
        if self.state == "SAVED":
            return self.phrases["saved"]
        command = spoken_command(text)
        yes = command in YES_ANSWERS
        no = command in NO_ANSWERS
        alert = self.phrases["danger"] if self.danger else ""
        if self.pending_condition and not self.needs_clarification and (yes or no):
            self.notes.append(text)
            if yes:
                self.conditions[self.pending_condition] = "patient_reported_previous_diagnosis"
                reply = "I’ll record that as a patient-reported previous diagnosis."
            else:
                reply = "I’ll record it as patient-reported."
            self.pending_condition = self.remaining_questions.pop(0) if self.remaining_questions else None
            if self.pending_condition:
                return " ".join(filter(None, (alert, reply, self.diagnosis_question())))
            self.state = "CONFIRMING" if self.visit.beneficiary_id and self.visit.visit_type else "LISTENING"
            self.awaiting_other_concerns = self.state == "CONFIRMING"
            followup = OTHER_CONCERNS_QUESTION if self.awaiting_other_concerns else self.phrases["missing"]
            return " ".join(filter(None, (alert, reply, followup)))
        if self.awaiting_other_concerns and (yes or no):
            self.notes.append(text)
            self.awaiting_other_concerns = False
            if yes:
                self.state = "LISTENING"
                reply = "Understood. What other health concern would you like to report?"
            else:
                reply = "Understood. Review the visit and select Confirm & Save when ready."
            return " ".join(filter(None, (alert, reply)))
        if command in UNCLEAR_ANSWERS and (self.pending_condition or self.awaiting_other_concerns):
            self.notes.append(text)
            question = self.diagnosis_question() if self.pending_condition else OTHER_CONCERNS_QUESTION
            return " ".join(filter(None, (alert, "I couldn’t tell from that answer.", question)))
        if yes or no:
            self.notes.append(text)
            return " ".join(filter(None, (alert, self.phrases["unclear"])))
        if command in CONFIRM:
            if allow_save:
                return await self.confirm(store)
            return "Review the visit and select Confirm & Save when ready."

        self.notes.append(text)
        self.awaiting_other_concerns = False
        if CAREGIVER.search(text):
            self.caregiver_speaking = True
        elif PATIENT_SPEAKER.search(text):
            self.caregiver_speaking = False
        reported, ambiguous = reported_terms(
            text, first_person_patient=(
                not self.caregiver_speaking and self.visit.visit_type != "newborn"
                and not re.search(r"\bnewborn\b", text, re.I)
            ),
        )
        if ambiguous or reported:
            self.needs_clarification = ambiguous
        new_terms = [term for term in reported if term not in self.conditions]
        for term in new_terms:
            self.conditions[term] = "patient_reported"
        try:
            result = await extract(text, self.visit) if extract else deterministic(text)
            patch = result.patch.model_dump(exclude_unset=True, exclude_none=True)
            if GENERAL_HEALTH.search(text) and patch.get("visit_type") in (None, "other", "general_health"):
                patch["visit_type"] = "general_health"
            if patch.get("visit_type", self.visit.visit_type) == "newborn" and "temperature_c" in patch:
                if OTHER_SUBJECT.search(text.lower()):
                    scoped = deterministic("; ".join(newborn_clauses(text))).patch.temperature_c
                    if scoped is None:
                        patch.pop("temperature_c")
                    else:
                        patch["temperature_c"] = scoped
            merged = Visit.model_validate({**self.visit.model_dump(), **patch})
        except ValidationError:
            self.state = "LISTENING"
            self.danger = assess(self.visit, "\n".join(self.notes), [])
            return " ".join(filter(None, (self.phrases["danger"] if self.danger else "", self.phrases["unclear"])))

        self.visit = merged
        # Preserve affirmative symptom evidence for the visit. Temperature is
        # re-evaluated when corrected.
        signs = assess(self.visit, "\n".join(self.notes), result.danger_candidates)
        alert = self.phrases["danger"] if signs else ""
        self.danger = signs
        self.remaining_questions.extend(term for term in new_terms if term in DIAGNOSIS_QUESTIONS)
        if self.pending_condition is None and self.remaining_questions:
            self.pending_condition = self.remaining_questions.pop(0)
        if self.needs_clarification:
            self.state = "LISTENING"
            return " ".join(filter(None, (alert, "Can you clarify which concern applies to the patient and what you mean?")))
        if self.pending_condition:
            self.state = "LISTENING"
            return " ".join(filter(None, (alert, self.diagnosis_question())))
        if not self.visit.beneficiary_id or not self.visit.visit_type:
            return " ".join(filter(None, (alert, self.phrases["missing"])))
        if not patch and not reported and not assess(self.visit, text, result.danger_candidates).difference(
            {"newborn_high_temperature", "newborn_low_temperature"}
        ):
            self.state = "LISTENING"
            return " ".join(filter(None, (alert, self.phrases["unclear"])))
        self.state = "CONFIRMING"
        fields = self.visit.model_dump(mode="json", exclude_none=True)
        fields["beneficiary_id"] = " ".join(fields["beneficiary_id"])
        fields["visit_type"] = self.phrases["types"].get(fields["visit_type"], fields["visit_type"].replace("_", " "))
        spoken = []
        for name, label in zip(Visit.model_fields, self.phrases["fields"]):
            if name in fields:
                value = fields[name]
                spoken.append(f"{label}: {', '.join(value) if isinstance(value, list) else value}.")
        self.awaiting_other_concerns = bool(reported)
        followup = OTHER_CONCERNS_QUESTION if self.awaiting_other_concerns else self.phrases["confirm"]
        return " ".join(filter(None, [alert, *spoken, followup]))
