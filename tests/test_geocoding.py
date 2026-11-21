import asyncio

import pytest

from backend import geocoding


def test_geocoding_uses_rounded_locality_and_caches(monkeypatch):
    calls = []

    def fetch(url, key):
        calls.append((url, key))
        return {"display_name": "Synthetic Village, Karnataka"}

    monkeypatch.setattr(geocoding, "fetch_address", fetch)

    async def run():
        service = geocoding.ReverseGeocoder("https://geocoder.example/reverse")
        assert await service.lookup(12.345678, 76.54321) == "Synthetic Village, Karnataka"
        assert await service.lookup(12.349, 76.544) == "Synthetic Village, Karnataka"
        assert await service.lookup(14, 76) is None

    asyncio.run(run())
    assert calls == [("https://geocoder.example/reverse", (12.35, 76.54))]


def test_geocoder_failure_is_nonfatal_and_public_service_is_rejected(monkeypatch):
    def unavailable(*_):
        raise OSError("synthetic provider failure")

    monkeypatch.setattr(geocoding, "fetch_address", unavailable)
    with pytest.raises(ValueError):
        geocoding.ReverseGeocoder("https://nominatim.openstreetmap.org/reverse")
    assert asyncio.run(geocoding.ReverseGeocoder("https://geocoder.example/reverse").lookup(12, 76)) is None
