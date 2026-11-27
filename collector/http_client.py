"""Polite HTTP client: fixed pace per host, backoff, and a hard stop on rate limits."""
import os
import time

import requests


class RateLimited(Exception):
    """Raised when a host tells us to slow down and we should stop for the day."""


class PoliteClient:
    def __init__(self, seconds_between_calls, max_retries=5, stop_on_429=False):
        self.gap = seconds_between_calls
        self.max_retries = max_retries
        self.stop_on_429 = stop_on_429
        self._last = 0.0
        self.calls = 0
        self.errors = 0
        self.error_kinds = {}     # e.g. {"429": 3, "ConnectionError": 1}
        contact = os.environ.get("CONTACT_EMAIL", "contact-not-set")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "AI-Brand-Response-Steam/0.1 (academic research; "
            f"github.com/samuelwbertram/AI-Brand-Response-Steam; {contact})"
        )
        # Skip Steam age gates so mature games return data.
        self.session.cookies.set("birthtime", "631152001", domain=".steampowered.com")
        self.session.cookies.set("wants_mature_content", "1", domain=".steampowered.com")
        self.session.cookies.set("mature_content", "1", domain=".steamcommunity.com")

    def _err(self, kind):
        self.errors += 1
        self.error_kinds[str(kind)] = self.error_kinds.get(str(kind), 0) + 1

    def _wait(self):
        delay = self.gap - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 30)
        backoff = 10
        for attempt in range(self.max_retries + 1):
            self._wait()
            self.calls += 1
            try:
                r = self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                self._err(type(e).__name__)
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 429 or r.status_code == 403:
                self._err(r.status_code)
                if self.stop_on_429:
                    raise RateLimited(f"{r.status_code} from {url.split('?')[0]}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 600)
                continue
            if r.status_code >= 500:
                self._err(r.status_code)
                time.sleep(backoff)
                backoff *= 2
                continue
            return r
        raise RateLimited(f"gave up after {self.max_retries} retries: {url.split('?')[0]}")
