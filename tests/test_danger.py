import pytest

from backend.danger import assess
from backend.schemas import Visit


@pytest.mark.parametrize(
    "category,temperature,text,expected",
    [
        ("newborn", 37.5, "", {"newborn_high_temperature"}),
        ("newborn", 35.4, "", {"newborn_low_temperature"}),
        ("newborn", 35.5, "no convulsions", set()),
        ("newborn", None, "Baby cannot feed", {"unable_to_feed"}),
        ("newborn", None, "शिशु को दौरे पड़ रहे हैं", {"convulsions"}),
        ("antenatal", 37.5, "", set()),
    ],
)
def test_scope_and_thresholds(category, temperature, text, expected):
    assert assess(Visit(visit_type=category, temperature_c=temperature), text, ["convulsions"]) == expected
