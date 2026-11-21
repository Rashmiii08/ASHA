import asyncio

from backend.dialogue import Dialogue, deterministic


def test_confirm_and_correct():
    saved = []
    async def store(dialogue):
        saved.append(dialogue.visit.model_dump())

    async def exercise():
        dialogue = Dialogue("en")
        await dialogue.process("yes, save it", None, store)
        assert not saved
        await dialogue.process("Beneficiary ID 4821, newborn, weight 2.7", None, store)
        await dialogue.process("No, the weight is 2.8 kg.", None, store)
        assert dialogue.visit.weight_kg == 2.8
        assert dialogue.visit.beneficiary_id == "4821"
        assert "4 8 2 1" in await dialogue.process("Weight is 2.8", None, store)
        await dialogue.process("change beneficiary id please", None, store)
        await dialogue.process("yes save it", None, store)
        assert not saved
        await dialogue.process("beneficiary id 4821", None, store)
        await dialogue.process("yes save it", None, store)
        await dialogue.process("yes save it", None, store)
        assert len(saved) == 1
    asyncio.run(exercise())


def test_structured_extraction():
    extracted = deterministic(
        "Beneficiary ID 4821. Antenatal. Weight 2.8. Vaccines due BCG, OPV-0. "
        "Next visit 2099-11-25. Temperature 37.5. BP 120 over 80."
    )
    assert extracted.patch.model_dump(mode="json") == {
        "beneficiary_id": "4821", "visit_type": "antenatal", "weight_kg": 2.8,
        "vaccinations_due": ["BCG", "OPV-0"], "next_visit_date": "2099-11-25",
        "temperature_c": 37.5, "bp_systolic": 120, "bp_diastolic": 80, "vaccination_plans": [],
    }


def test_hindi_code_mixing():
    patch = deterministic("लाभार्थी आईडी 0083, नवजात, वजन 2.8, तापमान 37.6").patch
    assert (patch.beneficiary_id, patch.visit_type, patch.weight_kg) == ("0083", "newborn", 2.8)


def test_reported_condition_clarification_does_not_save():
    saved = []

    async def store(dialogue):
        saved.append(dialogue.condition_details())

    async def exercise():
        dialogue = Dialogue("en")
        await dialogue.process("Beneficiary ID 0042. Other visit.", None, store)
        report = "I have sugar."
        reply = await dialogue.process(report, None, store)
        assert "healthcare professional previously diagnosed" in reply
        assert dialogue.condition_details() == [{"term": "Diabetes", "source": "patient_reported"}]
        reply = await dialogue.process("Yes.", None, store)
        assert "patient-reported previous diagnosis" in reply
        assert "other health concerns" in reply
        assert dialogue.notes[-2:] == [report, "Yes."]
        assert not saved
        await dialogue.confirm(store)
        assert saved == [[{"term": "Diabetes", "source": "patient_reported_previous_diagnosis"}]]

    asyncio.run(exercise())


def test_scopes_maternal_temperature_to_mother():
    async def exercise():
        dialogue = Dialogue("en")
        await dialogue.process(
            "Beneficiary ID 0067, newborn, mother temperature 38 and baby temperature 36.8.",
            None, None,
        )
        assert dialogue.visit.temperature_c == 36.8
        assert not dialogue.danger
        await dialogue.process("Mother temperature is 38.", None, None)
        assert dialogue.visit.temperature_c == 36.8
        assert not dialogue.danger

    asyncio.run(exercise())


def test_general_health_classification_and_caregiver_context_with_model():
    from backend.schemas import Extraction, Visit

    async def extract(_text, _visit):
        return Extraction(intent="provide_info", patch=Visit(visit_type="other"))

    async def exercise():
        dialogue = Dialogue("en")
        text = "This was a general health visit. The patient has sugar and high BP."
        await dialogue.process(text, extract, None)
        assert dialogue.notes == [text]
        assert dialogue.visit.visit_type == "general_health"
        assert list(dialogue.conditions) == ["Diabetes", "Hypertension"]

        dialogue = Dialogue("en")
        await dialogue.process("I am the caregiver.", None, None)
        reply = await dialogue.process("I have high BP.", None, None)
        assert "clarify" in reply.lower()
        assert not dialogue.conditions
        await dialogue.process("The patient has sugar.", None, None)
        assert list(dialogue.conditions) == ["Diabetes"]

    asyncio.run(exercise())


def test_natural_diagnosis_replies_advance_without_saving_or_reextracting():
    async def reject(*_):
        raise AssertionError("A question answer must not trigger extraction or saving")

    async def exercise():
        dialogue = Dialogue("en")
        await dialogue.process("Beneficiary ID 0042. General health visit. The patient has sugar and high BP.", None, reject)
        assert dialogue.pending_condition == "Diabetes"
        reply = await dialogue.process("Maybe.", reject, reject)
        assert "couldn’t tell" in reply and "Diabetes" in reply
        assert dialogue.pending_condition == "Diabetes"

        reply = await dialogue.process("Yes, they have.", reject, reject)
        assert "patient-reported previous diagnosis" in reply and "Hypertension" in reply
        assert "Diabetes" not in reply
        assert dialogue.pending_condition == "Hypertension"
        reply = await dialogue.process("No.", reject, reject)
        assert "patient-reported." in reply and "other health concerns" in reply
        assert dialogue.pending_condition is None
        assert dialogue.conditions == {
            "Diabetes": "patient_reported_previous_diagnosis",
            "Hypertension": "patient_reported",
        }
        reply = await dialogue.process("Not yet.", reject, reject)
        assert "Confirm & Save" in reply
        assert dialogue.state == "CONFIRMING"
        assert dialogue.visit.beneficiary_id == "0042"
        assert dialogue.visit.visit_type == "general_health"
        assert dialogue.notes[-4:] == ["Maybe.", "Yes, they have.", "No.", "Not yet."]
        # A bare yes is never a substitute for explicit save intent.
        await dialogue.process("Yes.", reject, reject)
        assert dialogue.state == "CONFIRMING"

    asyncio.run(exercise())


def test_correct_answers_diagnosis_and_yes_to_other_concerns_prompts_for_details():
    async def exercise():
        dialogue = Dialogue("en")
        await dialogue.process("Beneficiary ID 0043. General health visit. I have sugar.", None, None)
        assert "other health concerns" in await dialogue.process("Correct.", None, None)
        assert "What other health concern" in await dialogue.process("Yes.", None, None)
        assert dialogue.state == "LISTENING"
        assert dialogue.conditions["Diabetes"] == "patient_reported_previous_diagnosis"

    asyncio.run(exercise())
