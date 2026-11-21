import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid4

from pydantic import ValidationError

from .danger import assess
from .phrases import PHRASES
from .schemas import Extraction, Visit

CONFIRM = {
    "yes", "save", "save it", "yes save it", "yes save", "confirm", "हाँ", "हां",
    "हाँ सेव करें", "हां सेव करें", "सेव करें",
}


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

    @property
    def phrases(self):
        return PHRASES[self.language]

    async def process(self, text, extract, store):
        if self.state == "SAVED":
            return self.phrases["saved"]
        if re.sub(r"[^\w\s\u0900-\u097f]", "", text.casefold()).strip() in CONFIRM:
            if self.state != "CONFIRMING":
                return self.phrases["unclear"] if self.visit.beneficiary_id and self.visit.visit_type else self.phrases["missing"]
            await store(self)
            self.state = "SAVED"
            return self.phrases["saved"]

        try:
            result = await extract(text, self.visit) if extract else deterministic(text)
            patch = result.patch.model_dump(exclude_unset=True, exclude_none=True)
            merged = Visit.model_validate({**self.visit.model_dump(), **patch})
        except ValidationError:
            self.state = "LISTENING"
            return self.phrases["unclear"]

        self.visit = merged
        self.notes = [*self.notes[-39:], text]
        # Preserve affirmative symptom evidence for the visit. Temperature is
        # re-evaluated when corrected.
        signs = assess(self.visit, "\n".join(self.notes), result.danger_candidates)
        alert = self.phrases["danger"] if signs else ""
        self.danger = signs
        if not self.visit.beneficiary_id or not self.visit.visit_type:
            return " ".join(filter(None, (alert, self.phrases["missing"])))
        if not patch and not assess(self.visit, text, result.danger_candidates).difference(
            {"newborn_high_temperature", "newborn_low_temperature"}
        ):
            self.state = "LISTENING"
            return " ".join(filter(None, (alert, self.phrases["unclear"])))
        self.state = "CONFIRMING"
        fields = self.visit.model_dump(mode="json", exclude_none=True)
        fields["beneficiary_id"] = " ".join(fields["beneficiary_id"])
        fields["visit_type"] = self.phrases["types"].get(fields["visit_type"], fields["visit_type"])
        spoken = []
        for name, label in zip(Visit.model_fields, self.phrases["fields"]):
            if name in fields:
                value = fields[name]
                spoken.append(f"{label}: {', '.join(value) if isinstance(value, list) else value}.")
        return " ".join(filter(None, [alert, *spoken, self.phrases["confirm"]]))
