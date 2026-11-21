import pytest

from backend.terminology import reported_terms


@pytest.mark.parametrize(("text", "term"), [
    ("I have sugar.", "Diabetes"),
    ("I have sugar problem.", "Diabetes"),
    ("I have high BP.", "Hypertension"),
    ("I have low BP.", "Low blood pressure"),
    ("I have fever.", "Fever"),
    ("I have difficulty breathing.", "Difficulty breathing"),
    ("I have fits.", "Seizures"),
])
def test_explicit_reports(text, term):
    assert reported_terms(text) == ([term], False)


@pytest.mark.parametrize("text", [
    "I have sugar in my tea.", "I have sugar and salt at home.",
    "I do not have sugar.", "My mother has diabetes.", "Sugar level is normal.",
])
def test_unrelated_or_negated_sugar_does_not_imply_diabetes(text):
    assert reported_terms(text) == ([], False)


def test_ambiguous_reference_requires_clarification():
    assert reported_terms("Sugar?") == ([], True)


def test_patient_statement_with_coordinated_concerns():
    text = "This was a general health visit. The patient has sugar and high BP."
    assert reported_terms(text) == (["Diabetes", "Hypertension"], False)
    assert reported_terms("The patient has sugar and the caregiver has high BP.") == (["Diabetes"], False)
    assert reported_terms("I am the patient. I have high BP.") == (["Hypertension"], False)
    assert reported_terms("I am the caregiver. I have high BP.") == ([], True)
    assert reported_terms("She has sugar.") == ([], True)
    assert reported_terms("The patient does not have sugar or high BP.") == ([], False)
