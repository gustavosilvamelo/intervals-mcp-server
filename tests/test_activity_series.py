"""
Tests for get_activity_series: full series, windows/laps, summaries and events.

A synthetic 1 Hz ride is built in the same shape the Intervals.icu API returns
(list of {type, data} streams + activity with icu_intervals / icu_hr_zones),
and ``make_intervals_request`` is monkeypatched.
"""

import asyncio
import json
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("ATHLETE_ID", "i1")

from intervals_mcp_server.tools import activity_series  # noqa: E402
from intervals_mcp_server.tools.activity_series import get_activity_series  # noqa: E402

ZONES = [141, 156, 163, 174, 178, 185, 220]


def _ride() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """3600 s ride: 1200 s flat Z2, 600 s climb at 6% low cadence and high HR,
    120 s stop, 900 s descent coasting, 780 s flat with HR drifting up."""
    t, hr, cad, alt, dist, spd = [], [], [], [], [], []
    d, a = 0.0, 900.0
    for s in range(3600):
        if s < 1200:  # flat Z2
            v, h, c, g = 8.0, 148, 88, 0.0
        elif s < 1800:  # climb
            v, h, c, g = 3.0, 170, 62, 6.0
        elif s < 1920:  # stop
            v, h, c, g = 0.0, 120, 0, 0.0
        elif s < 2820:  # descent, coasting
            v, h, c, g = 11.0, 125, 0, -2.0
        else:  # flat, HR drifting
            v, h, c, g = 8.0, 150 + (s - 2820) // 60, 90, 0.0
        d += v
        a += v * g / 100
        t.append(s)
        hr.append(h)
        cad.append(c)
        alt.append(round(a, 1))
        dist.append(round(d, 1))
        spd.append(v)
    streams = [
        {"type": "time", "data": t},
        {"type": "heartrate", "data": hr},
        {"type": "cadence", "data": cad},
        {"type": "altitude", "data": alt},
        {"type": "distance", "data": dist},
        {"type": "velocity_smooth", "data": spd},
    ]
    activity = {
        "id": "i1",
        "name": "Treino teste",
        "type": "Ride",
        "start_date_local": "2026-10-18T07:00:00",
        "icu_hr_zones": ZONES,
        "icu_intervals": [
            {"label": "Plano", "type": "WORK", "start_time": 0, "end_time": 1200},
            {"label": "Subida", "type": "WORK", "start_time": 1200, "end_time": 1800},
            {"label": "Volta", "type": "RECOVERY", "start_index": 1800, "end_index": 3599},
        ],
    }
    return streams, activity


def _run(monkeypatch, **kwargs) -> dict[str, Any]:
    streams, activity = _ride()
    calls: list[tuple[str, Any]] = []

    async def fake(url: str, api_key: str = "", params=None, method="GET", data=None):
        calls.append((url, params))
        return streams if url.endswith("/streams.json") else activity

    monkeypatch.setattr(activity_series, "make_intervals_request", fake)
    out = json.loads(asyncio.run(get_activity_series(activity_id="i1", **kwargs)))
    out["_calls"] = calls
    return out


def test_routes_and_full_series(monkeypatch):
    out = _run(monkeypatch, resolution_s=1, max_points=10000)
    urls = [u for u, _ in out["_calls"]]
    assert "/activity/i1/streams.json" in urls and "/activity/i1" in urls
    assert out["source"]["points"] == 3600
    assert len(out["series"]["data"]) == 3600
    assert out["series"]["columns"][:2] == ["t_s", "hr_bpm"]
    assert out["window"]["resolution_adjusted"] is False


def test_max_points_raises_resolution(monkeypatch):
    out = _run(monkeypatch, resolution_s=1, max_points=500)
    assert out["window"]["resolution_adjusted"] is True
    assert out["window"]["resolution_s"] == 8
    assert len(out["series"]["data"]) <= 500


def test_summary_values(monkeypatch):
    s = _run(monkeypatch, include_series=False)["summary"]
    assert s["moving_s"] == 3480  # 120 s stop excluded
    assert s["hr_max"] == 170
    assert s["cadence_min"] == 62 and s["cadence_max"] == 90
    assert 20 < s["coasting_pct"] < 30  # 900 of 3480 s
    assert s["elev_gain_m"] >= 100  # 600 s * 3 m/s * 6%
    assert s["grade_max_pct"] >= 5.5
    assert sum(s["time_in_hr_zones_s"]) == 3480


def test_laps_by_time_and_index(monkeypatch):
    laps = _run(monkeypatch, include_series=False)["laps"]
    assert [lp["label"] for lp in laps] == ["Plano", "Subida", "Volta"]
    climb = laps[1]
    assert climb["hr_avg"] == 170 and climb["cadence_avg"] == 62
    assert climb["cadence_below_70_s"] == 600
    assert 5.5 <= climb["grade_avg_pct"] <= 6.5
    assert laps[2]["elapsed_s"] == 1800


def test_lap_window_and_last_lap(monkeypatch):
    out = _run(monkeypatch, lap=2, resolution_s=1)
    assert out["window"]["lap"]["label"] == "Subida"
    assert out["window"]["start_s"] == 1200 and out["window"]["end_s"] == 1800
    assert len(out["series"]["data"]) == 600
    last = _run(monkeypatch, lap=-1, include_series=False)
    assert last["window"]["lap"]["label"] == "Volta"


def test_time_window(monkeypatch):
    out = _run(monkeypatch, start_s=100, end_s=160, resolution_s=1)
    assert out["series"]["data"][0][0] == 100
    assert len(out["series"]["data"]) == 60


def test_events(monkeypatch):
    ev = _run(monkeypatch, include_series=False)["events"]
    kinds = {e["type"] for e in ev}
    assert {"hr_above_z3", "low_cadence", "coasting", "stop", "climb"} <= kinds
    climb = next(e for e in ev if e["type"] == "climb")
    assert 1150 <= climb["start_s"] <= 1250 and climb["elev_gain_m"] >= 100
    stop = next(e for e in ev if e["type"] == "stop")
    assert stop["duration_s"] == 120


def test_no_streams(monkeypatch):
    async def fake(url, api_key="", params=None, method="GET", data=None):
        return [] if url.endswith("/streams.json") else {}

    monkeypatch.setattr(activity_series, "make_intervals_request", fake)
    out = json.loads(asyncio.run(get_activity_series(activity_id="x")))
    assert "error" in out


def test_bad_lap(monkeypatch):
    out = _run(monkeypatch, lap=9)
    assert "Lap 9 not found" in out["error"]


def test_default_resolution_buckets(monkeypatch):
    out = _run(monkeypatch)  # resolution_s defaults to 10
    assert out["window"]["resolution_s"] == 10
    assert len(out["series"]["data"]) == 360
    assert [row[0] for row in out["series"]["data"][:3]] == [0, 10, 20]


def test_include_series_false_omits_series(monkeypatch):
    out = _run(monkeypatch, include_series=False)
    assert "series" not in out
    assert {"summary", "laps", "events"} <= out.keys()


def test_include_laps_and_events_false(monkeypatch):
    out = _run(monkeypatch, include_laps=False, include_events=False, include_series=False)
    assert "laps" not in out and "events" not in out
    assert out["summary"]["moving_s"] == 3480


def test_elapsed_includes_auto_pause(monkeypatch):
    streams, activity = _ride()
    # simulate a 300 s auto-pause: drop samples 2000..2299 from every stream
    for st in streams:
        st["data"] = st["data"][:2000] + st["data"][2300:]

    async def fake(url, api_key="", params=None, method="GET", data=None):
        return streams if url.endswith("/streams.json") else activity

    monkeypatch.setattr(activity_series, "make_intervals_request", fake)
    out = json.loads(asyncio.run(get_activity_series(activity_id="i1", include_series=False)))
    assert out["summary"]["elapsed_s"] == 3600
    assert out["summary"]["recorded_s"] == 3300  # the >10 s gap counts as 1 s
    assert out["source"]["duration_s"] == 3600
