"""Local browser test factory with synthetic address responses."""

from backend.geocoding import ReverseGeocoder
from backend.live import create_live_app


async def synthetic_address(self, latitude, longitude):
    assert self.url == "https://geocoder.example/reverse"
    return "Synthetic Village, Karnataka"


def create_app():
    ReverseGeocoder.lookup = synthetic_address
    return create_live_app()
