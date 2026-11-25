"""Tests for the update_wellness_data MCP tool and RPE/feel via update_activity."""

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("ATHLETE_ID", "i1")

from intervals_mcp_server.server import update_wellness_data  # noqa: E402
from intervals_mcp_server.tools.activities import update_activity  # noqa: E402


class FakeRequest:
    """Records calls and answers GET with `existing`, PUT with an empty dict."""

    def __init__(self, existing=None):
        self.existing = existing if existing is not None else {"error": True, "message": "404"}
        self.calls: list[dict] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("method") == "PUT":
            return {}
        return self.existing

    @property
    def puts(self):
        return [c for c in self.calls if c.get("method") == "PUT"]


def _patch(monkeypatch, fake):
    monkeypatch.setattr("intervals_mcp_server.tools.wellness.make_intervals_request", fake)
    monkeypatch.setattr("intervals_mcp_server.tools.activities.make_intervals_request", fake)


def test_writes_all_fields_on_empty_day(monkeypatch):
    fake = FakeRequest()
    _patch(monkeypatch, fake)
    out = asyncio.run(
        update_wellness_data(
            date="2026-11-24",
            weight=70.5,
            sleep_secs=27000,
            resting_hr=50,
            hrv=65.0,
            fatigue=2,
            soreness=2,
            stress=1,
            mood=3,
            motivation=3,
            injury=1,
        )
    )
    assert "Successfully updated" in out
    assert len(fake.puts) == 1
    body = fake.puts[0]["data"]
    assert body["weight"] == 70.5 and body["sleepSecs"] == 27000
    assert body["restingHR"] == 50 and body["hrv"] == 65.0
    assert body["fatigue"] == 2 and body["injury"] == 1
    assert fake.puts[0]["url"] == "/athlete/i1/wellness/2026-11-24"


def test_does_not_overwrite_filled_value(monkeypatch):
    fake = FakeRequest(existing={"id": "2026-11-24", "sleepSecs": 25000, "weight": None})
    _patch(monkeypatch, fake)
    out = asyncio.run(update_wellness_data(date="2026-11-24", sleep_secs=30000, weight=71.0))
    body = fake.puts[0]["data"]
    assert "sleepSecs" not in body
    assert body["weight"] == 71.0
    assert "sleepSecs" in out and "Kept existing" in out


def test_all_filled_writes_nothing(monkeypatch):
    fake = FakeRequest(existing={"sleepSecs": 25000})
    _patch(monkeypatch, fake)
    out = asyncio.run(update_wellness_data(date="2026-11-24", sleep_secs=30000))
    assert fake.puts == []
    assert "Nothing written" in out


def test_overwrite_true_replaces(monkeypatch):
    fake = FakeRequest(existing={"sleepSecs": 25000})
    _patch(monkeypatch, fake)
    asyncio.run(update_wellness_data(date="2026-11-24", sleep_secs=30000, overwrite=True))
    assert fake.puts[0]["data"] == {"sleepSecs": 30000}
    assert [c for c in fake.calls if c.get("method") != "PUT"] == []


def test_validation(monkeypatch):
    fake = FakeRequest()
    _patch(monkeypatch, fake)
    assert "YYYY-MM-DD" in asyncio.run(update_wellness_data(date="24/11/2026", weight=70))
    assert "At least one" in asyncio.run(update_wellness_data(date="2026-11-24"))
    assert fake.calls == []


def test_api_error_is_reported(monkeypatch):
    async def failing(*args, **kwargs):
        if kwargs.get("method") == "PUT":
            return {"error": True, "message": "boom"}
        return {}

    _patch(monkeypatch, failing)
    out = asyncio.run(update_wellness_data(date="2026-11-24", weight=70))
    assert out.startswith("Error updating wellness data: boom")


def test_update_activity_accepts_rpe_and_feel(monkeypatch):
    fake = FakeRequest()
    _patch(monkeypatch, fake)
    out = asyncio.run(update_activity(activity_id="i123", fields={"icu_rpe": 7, "feel": 2}))
    assert "Successfully" in out
    assert fake.puts[0]["data"] == {"icu_rpe": 7, "feel": 2}
    assert fake.puts[0]["url"] == "/activity/i123"
