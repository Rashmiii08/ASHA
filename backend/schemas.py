from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated


Language = Literal["en", "hi"]
VisitType = Literal["antenatal", "postnatal", "newborn", "immunisation", "general_health", "other"]
DangerSign = Literal["unable_to_feed", "convulsions", "newborn_high_temperature", "newborn_low_temperature"]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Vaccine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class Visit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    beneficiary_id: Identifier | None = None
    visit_type: VisitType | None = None
    weight_kg: float | None = Field(default=None, gt=0, le=350)
    vaccinations_due: list[Vaccine] | None = Field(default=None, max_length=16)
    next_visit_date: date | None = None
    temperature_c: float | None = Field(default=None, ge=25, le=46)
    bp_systolic: int | None = Field(default=None, ge=30, le=300)
    bp_diastolic: int | None = Field(default=None, ge=15, le=200)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["provide_info", "correct", "confirm", "clarify"]
    patch: Visit
    danger_candidates: list[DangerSign] = Field(default_factory=list)


class VisitReview(Visit):
    model_config = ConfigDict(extra="forbid")

    type: Literal["edit_visit", "confirm_visit"]
    revision: int = Field(strict=True, ge=0)
    beneficiary_id: Identifier | None
    visit_type: VisitType | None
    retained_conditions: list[str] = Field(max_length=16)
    added_conditions: list[str] = Field(default_factory=list, max_length=16)
    diagnosis_answers: dict[str, bool] = Field(default_factory=dict, max_length=16)
    exclude_unclear_report: bool = False
    transcript_correction: str | None = Field(default=None, max_length=3000)
