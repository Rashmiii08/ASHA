"""Opt-in locality lookup through an operator-authorized private Nominatim endpoint."""

import asyncio
import json
import time
from collections import OrderedDict
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_address(url, key):
    target = f"{url}?{urlencode({'format': 'jsonv2', 'lat': key[0], 'lon': key[1], 'zoom': 14})}"
    request = Request(target, headers={"User-Agent": "ASHA-Voice-Field-Register/1.0", "Accept": "application/json"})
    with build_opener(NoRedirect).open(request, timeout=2.5) as response:
        return json.loads(response.read(65536))


class ReverseGeocoder:
    def __init__(self, url: str):
        target = urlsplit(url)
        host = target.hostname or ""
        if (target.scheme != "https" or not host or target.username or target.password or target.query
                or target.fragment or host == "openstreetmap.org" or host.endswith(".openstreetmap.org")):
            raise ValueError("Configure a private HTTPS reverse-geocoding endpoint.")
        self.url = url
        self.cache = OrderedDict()
        self.next_request = 0.0
        self.lock = asyncio.Lock()

    async def lookup(self, latitude: float, longitude: float) -> str | None:
        # Request locality rather than a house address, using approximately kilometre-scale input.
        key = (round(latitude, 2), round(longitude, 2))
        now = time.monotonic()
        cached = self.cache.get(key)
        if cached and now < cached[0]:
            self.cache.move_to_end(key)
            return cached[1]
        if self.lock.locked() or now < self.next_request:
            return None
        async with self.lock:
            self.next_request = now + 1.0
            address = None
            try:
                async with asyncio.timeout(3):
                    value = await asyncio.to_thread(fetch_address, self.url, key)
                label = value.get("display_name") if isinstance(value, dict) else None
                if isinstance(label, str):
                    address = " ".join(label.split())[:300] or None
            except (URLError, OSError, ValueError, TimeoutError):
                pass
            self.cache[key] = (time.monotonic() + (86400 if address else 60), address)
            self.cache.move_to_end(key)
            if len(self.cache) > 512:
                self.cache.popitem(last=False)
            return address
