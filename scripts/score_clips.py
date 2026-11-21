"""Score deterministic extraction and referral behavior on synthetic transcripts."""

import argparse
import asyncio
import json

from backend.dialogue import Dialogue

FIXTURES = [
    ("en", "Beneficiary ID DEMO-7, newborn, weight 2.7, temperature 37.6.",
     {"beneficiary_id": "DEMO-7", "visit_type": "newborn", "weight_kg": 2.7, "temperature_c": 37.6},
     {"newborn_high_temperature"}, "No, weight is 2.8", 2.8, "Yes, save it."),
    ("hi", "लाभार्थी आईडी DEMO-8, नवजात, वजन 2.6, तापमान 35.4।",
     {"beneficiary_id": "DEMO-8", "visit_type": "newborn", "weight_kg": 2.6, "temperature_c": 35.4},
     {"newborn_low_temperature"}, "वजन 2.7", 2.7, "हाँ सेव करें"),
    ("en", "Beneficiary ID DEMO-9, antenatal, weight 56.2, blood pressure 120/80.",
     {"beneficiary_id": "DEMO-9", "visit_type": "antenatal", "weight_kg": 56.2, "bp_systolic": 120, "bp_diastolic": 80},
     set(), "Weight is 57.0", 57.0, "Yes, save it."),
    ("en", "Beneficiary ID DEMO-10, newborn, weight 2.9. Baby cannot feed.",
     {"beneficiary_id": "DEMO-10", "visit_type": "newborn", "weight_kg": 2.9},
     {"unable_to_feed"}, "Weight is 3.0", 3.0, "Yes, save it."),
]


async def score():
    total_fields = correct_fields = total_signs = corrections = confirms = 0
    misses, extras = [], []
    for language, transcript, expected, danger, correction, weight, confirm in FIXTURES:
        saved = []

        async def store(dialogue):
            saved.append(dialogue.visit.weight_kg)

        dialogue = Dialogue(language)
        await dialogue.process(transcript, None, store)
        result = dialogue.visit.model_dump(mode="json")
        total_fields += len(expected)
        correct_fields += sum(result[field] == value for field, value in expected.items())
        total_signs += len(danger)
        misses.extend((expected["beneficiary_id"], sign) for sign in sorted(danger - dialogue.danger))
        extras.extend((expected["beneficiary_id"], sign) for sign in sorted(dialogue.danger - danger))
        await dialogue.process(correction, None, store)
        corrections += dialogue.visit.weight_kg == weight
        await dialogue.process(confirm, None, store)
        confirms += saved == [weight]
    return {
        "fixtures": len(FIXTURES),
        "field_accuracy": correct_fields / total_fields,
        "fields_correct": correct_fields,
        "fields_expected": total_fields,
        "danger_recall": (total_signs - len(misses)) / total_signs,
        "missed_danger_signs": misses,
        "unexpected_danger_signs": extras,
        "successful_confirmations": confirms,
        "successful_corrections": corrections,
        "time_to_first_audio_ms": None,
        "timing_note": "Synthetic transcript fixtures do not include provider audio.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(asyncio.run(score()), indent=2, ensure_ascii=False))
