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
        await dialogue.process("Beneficiary ID DEMO-4821, newborn, weight 2.7", None, store)
        await dialogue.process("No, the weight is 2.8 kg.", None, store)
        assert dialogue.visit.weight_kg == 2.8
        assert dialogue.visit.beneficiary_id == "DEMO-4821"
        assert "D E M O - 4 8 2 1" in await dialogue.process("Weight is 2.8", None, store)
        await dialogue.process("change beneficiary id please", None, store)
        await dialogue.process("yes save it", None, store)
        assert not saved
        await dialogue.process("beneficiary id DEMO-4821", None, store)
        await dialogue.process("yes save it", None, store)
        await dialogue.process("yes save it", None, store)
        assert len(saved) == 1
    asyncio.run(exercise())


def test_structured_extraction():
    extracted = deterministic(
        "Beneficiary ID 4821. Antenatal. Weight 2.8. Vaccines due BCG, OPV-0. "
        "Next visit 2026-11-25. Temperature 37.5. BP 120 over 80."
    )
    assert extracted.patch.model_dump(mode="json") == {
        "beneficiary_id": "4821", "visit_type": "antenatal", "weight_kg": 2.8,
        "vaccinations_due": ["BCG", "OPV-0"], "next_visit_date": "2026-11-25",
        "temperature_c": 37.5, "bp_systolic": 120, "bp_diastolic": 80,
    }


def test_hindi_code_mixing():
    patch = deterministic("लाभार्थी आईडी DEMO-83, नवजात, वजन 2.8, तापमान 37.6").patch
    assert (patch.beneficiary_id, patch.visit_type, patch.weight_kg) == ("DEMO-83", "newborn", 2.8)


def test_reported_condition_clarification_does_not_save():
    saved = []

    async def store(dialogue):
        saved.append(dialogue.condition_details())

    async def exercise():
        dialogue = Dialogue("en")
        await dialogue.process("Beneficiary ID DEMO-42. Other visit.", None, store)
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
            "Beneficiary ID DEMO-67, newborn, mother temperature 38 and baby temperature 36.8.",
            None, None,
        )
        assert dialogue.visit.temperature_c == 36.8
        assert not dialogue.danger
        await dialogue.process("Mother temperature is 38.", None, None)
        assert dialogue.visit.temperature_c == 36.8
        assert not dialogue.danger

    asyncio.run(exercise())
