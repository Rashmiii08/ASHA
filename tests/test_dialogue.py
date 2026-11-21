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
