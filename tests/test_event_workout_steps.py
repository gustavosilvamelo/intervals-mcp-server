"""
Tests for the include_workout_steps option of get_event_by_id (BL-026, part A).

The workout_doc below mirrors a real planned session: warm-up, 3 x (12 min at 90% FTP with
cadence targets + 5 min recovery) and a cool-down, with percent-of-FTP targets resolved to watts.
"""

import asyncio
import json

from intervals_mcp_server.tools.events import get_event_by_id
from intervals_mcp_server.utils.formatting import format_workout_steps

EVENT = {
    "id": "152349453",
    "date": "2026-11-17",
    "name": "SS 3x12",
    "description": "- 10m 60%\n3x\n- 12m 90% ftp 90rpm\n- 5m 50%",
    "workout_doc": {
        "duration": 4200,
        "ftp": 190,
        "steps": [
            {"duration": 600, "warmup": True, "power": {"value": 60, "units": "%ftp"}},
            {
                "reps": 3,
                "steps": [
                    {
                        "duration": 720,
                        "power": {"value": 90, "units": "%ftp"},
                        "_power": {"value": 171, "units": "w"},
                        "cadence": {"value": 90, "units": "rpm"},
                    },
                    {"duration": 300, "power": {"value": 50, "units": "%ftp"}},
                ],
            },
            {"duration": 300, "cooldown": True, "power": {"value": 50, "units": "%ftp"}},
        ],
    },
}


def _patch(monkeypatch, event, calls):
    async def fake_request(*_args, **kwargs):
        calls.append(kwargs)
        return event

    monkeypatch.setattr("intervals_mcp_server.tools.events.make_intervals_request", fake_request)


def _steps_json(text):
    block = text.split("Workout steps (structured, one entry per executed step):\n", 1)[1]
    return json.loads(block)


def test_default_output_has_no_steps_and_no_extra_params(monkeypatch):
    """Without the option the request and the text stay exactly as before."""
    calls: list[dict] = []
    _patch(monkeypatch, EVENT, calls)
    result = asyncio.run(get_event_by_id("152349453", athlete_id="1"))
    assert "Event Details:" in result
    assert "Workout steps" not in result
    assert calls[0].get("params") is None


def test_option_returns_expanded_steps_with_targets(monkeypatch):
    """Repeat blocks are expanded and targets (power, cadence, watts) are kept."""
    calls: list[dict] = []
    _patch(monkeypatch, EVENT, calls)
    result = asyncio.run(get_event_by_id("152349453", athlete_id="1", include_workout_steps=True))
    assert calls[0]["params"] == {"resolve": "true"}
    assert result.startswith("Event Details:")  # the usual details come first, unchanged
    payload = _steps_json(result)
    steps = payload["steps"]
    assert len(steps) == 1 + 3 * 2 + 1  # warm-up + 3 x (work + recovery) + cool-down
    work = [s for s in steps if s.get("duration_s") == 720]
    assert [s["rep"] for s in work] == [1, 2, 3]
    assert all(s["reps"] == 3 for s in work)
    assert work[0]["power"] == {"value": 90, "units": "%ftp"}
    assert work[0]["power_resolved"] == {"value": 171, "units": "w"}
    assert work[0]["cadence"] == {"value": 90, "units": "rpm"}
    assert steps[0]["warmup"] is True and steps[-1]["cooldown"] is True
    assert [s["n"] for s in steps] == list(range(1, 9))
    assert payload["workout"]["ftp"] == 190


def test_ranges_and_nested_repeats():
    """Ranges keep start/end and nested repeats multiply."""
    doc = {
        "steps": [
            {
                "reps": 2,
                "steps": [
                    {
                        "reps": 2,
                        "steps": [
                            {
                                "duration": 60,
                                "cadence": {"start": 85, "end": 95, "units": "rpm"},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    steps = json.loads(format_workout_steps(doc).split("\n", 1)[1])["steps"]
    assert len(steps) == 4
    assert steps[0]["cadence"] == {"start": 85, "end": 95, "units": "rpm"}


def test_event_without_steps(monkeypatch):
    """An event with no structured steps says so instead of failing."""
    calls: list[dict] = []
    _patch(monkeypatch, {"id": "e1", "date": "2026-11-17", "name": "Livre"}, calls)
    result = asyncio.run(get_event_by_id("e1", athlete_id="1", include_workout_steps=True))
    assert "Workout steps: none" in result
    assert format_workout_steps(None) == ""
    assert format_workout_steps({"steps": []}) == ""


def test_api_error_is_unchanged(monkeypatch):
    """Errors from the API are returned the same way with the option on."""
    calls: list[dict] = []
    _patch(monkeypatch, {"error": True, "message": "boom"}, calls)
    result = asyncio.run(get_event_by_id("e1", athlete_id="1", include_workout_steps=True))
    assert result == "Error fetching event details: boom"
