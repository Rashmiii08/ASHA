from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from typing_extensions import Annotated


Language = Literal["en", "hi"]
VisitType = Literal["antenatal", "postnatal", "newborn", "immunisation", "general_health", "other"]
DangerSign = Literal["unable_to_feed", "convulsions", "newborn_high_temperature", "newborn_low_temperature"]
Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{4}$")]
NEWBORN_WEIGHT_MAX_KG = 10
Vaccine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class VaccinationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Vaccine | None = None
    status: Literal["due", "given"]
    due_date: date | None = None

    @model_validator(mode="after")
    def check_plan(self):
        if self.status == "given" and (not self.name or self.due_date):
            raise ValueError("A given vaccination requires a name and no due date")
        if not self.name and not self.due_date:
            raise ValueError("An unnamed vaccination requires a due date")
        if self.name and self.name.casefold() in {"in", "after", "next", "on", "due"}:
            raise ValueError("Enter a vaccine name or leave it empty")
        return self


class Visit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    beneficiary_id: Identifier | None = None
    visit_type: VisitType | None = None
    weight_kg: float | None = Field(default=None, gt=0, le=350)
    vaccinations_due: list[Vaccine] | None = Field(default=None, max_length=16)
    next_visit_date: date | None = None
    vaccination_plans: list[VaccinationPlan] = Field(default_factory=list, max_length=16)
    temperature_c: float | None = Field(default=None, ge=25, le=46)
    bp_systolic: int | None = Field(default=None, ge=30, le=300)
    bp_diastolic: int | None = Field(default=None, ge=15, le=200)


    @field_validator("vaccinations_due")
    @classmethod
    def check_vaccine_names(cls, names):
        if names and any(name.casefold() in {"in", "after", "next", "on", "due"} for name in names):
            raise ValueError("Enter a vaccine name or use the vaccination due-date field")
        return names

    @model_validator(mode="after")
    def check_weight(self):
        if self.visit_type == "newborn" and self.weight_kg is not None and self.weight_kg > NEWBORN_WEIGHT_MAX_KG:
            raise ValueError("This weight is unusually high for a newborn. Confirm the weight in kilograms.")
        return self


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
    retained_contextual_conditions: list[str] | None = Field(default=None, max_length=16)
    added_conditions: list[str] = Field(default_factory=list, max_length=16)
    diagnosis_answers: dict[str, bool] = Field(default_factory=dict, max_length=16)
    exclude_unclear_report: bool = False
    transcript_correction: str | None = Field(default=None, max_length=3000)


class VisitLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    latitude: float = Field(strict=True, ge=-90, le=90)
    longitude: float = Field(strict=True, ge=-180, le=180)
    accuracy_m: float = Field(strict=True, ge=0)
    captured_at: AwareDatetime

    @field_validator("captured_at", mode="before")
    @classmethod
    def require_timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Expected an ISO 8601 capture timestamp")
        return value


class StoredVisitLocation(VisitLocation):
    address: str | None = Field(default=None, max_length=300)


class VisitLocationCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["set_visit_location"]
    request_id: int = Field(strict=True, ge=1)
    location: VisitLocation
    address_consent: bool = Field(default=False, strict=True)
