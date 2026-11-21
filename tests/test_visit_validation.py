import asyncio
from datetime import date

import pytest
from pydantic import ValidationError

from backend.dialogue import Dialogue, deterministic
from backend.scheduling import spoken_date
from backend.schemas import Visit
from backend.terminology import family_reports, reported_terms
from backend.visit_input import input_fields


@pytest.mark.parametrize("identifier", ["102222", "123", "ABCD", "1234.5", "1234 5678", " 1234", "१२३४"])
def test_rejects_invalid_identifier_without_truncation(identifier):
    with pytest.raises(ValidationError):
        Visit(beneficiary_id=identifier)
    if identifier == " 1234":
        return  # Spoken whitespace separates the marker from the identifier.
    issues = {}
    assert deterministic(f"Beneficiary ID {identifier}.", issues=issues).patch.beneficiary_id is None
    assert "exactly four numeric digits" in issues["beneficiary_id"]


def test_valid_identifiers_preserve_leading_zeros_and_weights_are_visit_specific():
    assert deterministic("Beneficiary ID 0123.").patch.beneficiary_id == "0123"
    with pytest.raises(ValidationError):
        Visit(visit_type="newborn", weight_kg=36)
    assert Visit(visit_type="newborn", weight_kg=3.5).weight_kg == 3.5
    for category, weight in zip(("general_health", "antenatal", "postnatal"), (36, 65, 80)):
        assert Visit(visit_type=category, weight_kg=weight).weight_kg == weight


@pytest.mark.parametrize(("text", "expected"), [
    ("Next visit tomorrow.", date(2026, 2, 1)),
    ("Next visit in 3 days.", date(2026, 2, 3)),
    ("Next visit after 2 weeks.", date(2026, 2, 14)),
    ("Next visit in 1 month.", date(2026, 2, 28)),
    ("Next visit after 6 months.", date(2026, 7, 31)),
    ("Next visit on December 15.", date(2026, 12, 15)),
    ("Follow up next Monday.", date(2026, 2, 2)),
])
def test_spoken_schedules(text, expected):
    fields, issues = input_fields(text, today=date(2026, 1, 31))
    assert not issues and fields["next_visit_date"] == expected


@pytest.mark.parametrize("phrase", ["December 15", "03/04", "in a little while", "2026-01-01"])
def test_ambiguous_and_past_dates_fail(phrase):
    with pytest.raises(ValueError):
        spoken_date(phrase, today=date(2026, 12, 20))


@pytest.mark.parametrize(("text", "name", "due"), [
    ("BCG vaccine is due next week.", "BCG", date(2026, 2, 7)),
    ("Polio vaccination is due in 2 months.", "POLIO", date(2026, 3, 31)),
    ("Vaccination is due after 6 weeks.", None, date(2026, 3, 14)),
    ("The next immunisation is in 3 days.", None, date(2026, 2, 3)),
])
def test_vaccination_dates_are_not_vaccine_names(text, name, due):
    fields, issues = input_fields(text, today=date(2026, 1, 31))
    plan, = fields["vaccination_plans"]
    assert not issues and (plan.name, plan.status, plan.due_date) == (name, "due", due)


def test_family_attribution_and_confirmed_diagnosis_is_not_inferred():
    assert reported_terms("The patient has diabetes and hypertension.") == (["Diabetes", "Hypertension"], False)
    assert family_reports("The newborn has high sugar and the mother has high BP.") == [
        ("newborn", "High blood sugar"), ("mother", "Hypertension"),
    ]


def test_dialogue_retains_collective_draft_and_accepts_weight_correction():
    async def exercise():
        dialogue = Dialogue("en")
        assert "type of visit" in await dialogue.process("Beneficiary ID 1234.", None, None)
        assert "baby's weight" in await dialogue.process("Newborn visit.", None, None)
        assert "unusually high" in await dialogue.process("Weight is 36 kg.", None, None)
        assert dialogue.visit.weight_kg is None
        assert dialogue.save_blockers()
        reply = await dialogue.process("3.6 kg.", None, None)
        assert "3.6" in reply and not dialogue.save_blockers()
        assert "follow-up recorded" in await dialogue.process("Polio vaccination is due in 2 weeks.", None, None)
        assert "Next visit scheduled" in await dialogue.process("Next visit in 1 month.", None, None)
        assert dialogue.visit.beneficiary_id == "1234"
        assert dialogue.visit.visit_type == "newborn"

    asyncio.run(exercise())
