"""
Second-by-second activity series for Intervals.icu (get_activity_series).

``get_activity_streams`` downloads the full time series but only prints the
first/last five values of each stream. This tool returns the actual data as
compact JSON, plus the analysis that needs the raw samples:

* the series itself (resampled to ``resolution_s``; 1 = raw), optionally cut
  to a time window or to one lap/interval;
* summary of the selected window (HR, cadence, coasting, speed, grade,
  elevation, HR drift, time in HR zones);
* the same stats for every lap/interval of the activity;
* attention events detected on the raw 1 Hz data (high-HR blocks, low
  cadence, long coasting, stops, sustained climbs).

Everything is computed on the server, so a 3-4 hour ride (10k+ samples) never
has to travel whole through the conversation unless explicitly asked for.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from mcp.types import ToolAnnotations

from intervals_mcp_server.api.client import make_intervals_request
from intervals_mcp_server.mcp_instance import mcp  # noqa: F401

# Streams requested from the API (missing ones are simply absent in the reply).
STREAM_TYPES = "time,heartrate,cadence,altitude,distance,velocity_smooth,watts"

# Thresholds used for "moving", grades and attention events.
MOVING_SPEED_MS = 1.0  # below this the rider is considered stopped
MAX_DT_S = 10  # a gap longer than this between samples counts as a pause
GRADE_WINDOW_M = 50.0  # distance used to compute the road grade
ELEV_HYSTERESIS_M = 2.0  # ignore altitude noise smaller than this
LOW_CADENCE_RPM = 70
CLIMB_GRADE_PCT = 4.0
CLIMB_MIN_S = 60
CLIMB_MIN_M = 200
DEFAULT_MIN_EVENT_S = 30
COAST_MIN_S = 60
STOP_MIN_S = 60


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _r(value: float | None, nd: int = 1) -> float | int | None:
    """Round for output; ints when nd == 0; keep None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if nd == 0:
        return int(round(value))
    return round(value, nd)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    """Time-weighted mean of (value, weight) pairs."""
    tot = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tot if tot else None


def _streams_to_columns(streams: Any) -> dict[str, list[Any]]:
    """Turn the API list of {type, data} into {type: data}."""
    cols: dict[str, list[Any]] = {}
    if isinstance(streams, list):
        for s in streams:
            if isinstance(s, dict) and isinstance(s.get("data"), list):
                cols[str(s.get("type"))] = s["data"]
    return cols


def build_samples(cols: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Normalise raw streams into one record per sample.

    Each sample has: t (s from start), dt (s it represents), hr, cad, alt,
    dist, spd (m/s), w, moving (bool), grade (%), filled in afterwards.
    """
    times = cols.get("time") or []
    n = len(times)
    if not n:
        return []

    def col(name: str) -> list[Any]:
        data = cols.get(name) or []
        return data if len(data) == n else [None] * n

    hr, cad, alt, dist, spd, watts = (
        col("heartrate"),
        col("cadence"),
        col("altitude"),
        col("distance"),
        col("velocity_smooth"),
        col("watts"),
    )

    samples: list[dict[str, Any]] = []
    for i in range(n):
        t = _num(times[i]) or 0.0
        nxt = _num(times[i + 1]) if i + 1 < n else None
        dt = (nxt - t) if nxt is not None else 1.0
        paused = dt > MAX_DT_S
        dt = 1.0 if paused else max(dt, 0.0)
        h = _num(hr[i])
        s = _num(spd[i])
        d = _num(dist[i])
        # derive speed from distance when velocity_smooth is missing
        if s is None and d is not None and nxt is not None and i + 1 < n:
            d2 = _num(dist[i + 1])
            if d2 is not None and nxt > t:
                s = (d2 - d) / (nxt - t)
        samples.append(
            {
                "t": t,
                "dt": dt,
                "hr": h if h and h > 0 else None,
                "cad": _num(cad[i]),
                "alt": _num(alt[i]),
                "dist": d,
                "spd": s,
                "w": _num(watts[i]),
                "moving": bool(s is not None and s >= MOVING_SPEED_MS and not paused),
                "grade": None,
            }
        )
    _fill_grades(samples)
    return samples


def _fill_grades(samples: list[dict[str, Any]]) -> None:
    """Grade (%) over the next GRADE_WINDOW_M metres (two-pointer, O(n))."""
    n = len(samples)
    j = 0
    for i in range(n):
        di, ai = samples[i]["dist"], samples[i]["alt"]
        if di is None or ai is None or not samples[i]["moving"]:
            continue
        j = max(j, i)
        while j < n - 1 and (
            samples[j]["dist"] is None or samples[j]["dist"] - di < GRADE_WINDOW_M
        ):
            j += 1
        dj, aj = samples[j]["dist"], samples[j]["alt"]
        if dj is None or aj is None or dj - di < GRADE_WINDOW_M * 0.8:
            continue
        g = (aj - ai) / (dj - di) * 100.0
        if abs(g) <= 30:  # discard GPS/altimeter glitches
            samples[i]["grade"] = g


def _elevation(alts: list[float]) -> tuple[float, float]:
    """Gain and loss with hysteresis to ignore altimeter noise."""
    gain = loss = 0.0
    ref: float | None = None
    for a in alts:
        if ref is None:
            ref = a
            continue
        if a - ref >= ELEV_HYSTERESIS_M:
            gain += a - ref
            ref = a
        elif ref - a >= ELEV_HYSTERESIS_M:
            loss += ref - a
            ref = a
    return gain, loss


def _zone_index(hr: float, zones: list[float]) -> int:
    """zones = upper bound of each zone (Intervals' icu_hr_zones)."""
    for idx, top in enumerate(zones):
        if hr <= top:
            return idx
    return len(zones) - 1


def segment_stats(
    samples: list[dict[str, Any]], hr_zones: list[float] | None = None
) -> dict[str, Any]:
    """Stats for a list of 1 Hz samples (a lap, a window or the whole ride)."""
    if not samples:
        return {}
    # wall-clock span (includes auto-pauses); recorded = time actually sampled
    elapsed = samples[-1]["t"] + samples[-1]["dt"] - samples[0]["t"]
    recorded = sum(s["dt"] for s in samples)
    mov = [s for s in samples if s["moving"]]
    moving_s = sum(s["dt"] for s in mov)

    hr_pairs = [(s["hr"], s["dt"]) for s in mov if s["hr"] is not None]
    hr_all = [s["hr"] for s in samples if s["hr"] is not None]
    pedal = [s for s in mov if s["cad"] is not None and s["cad"] > 0]
    cad_known = [s for s in mov if s["cad"] is not None]
    coast_s = sum(s["dt"] for s in cad_known if s["cad"] == 0)
    cad_vals = [s["cad"] for s in pedal]

    dists = [s["dist"] for s in samples if s["dist"] is not None]
    dist_m = (max(dists) - min(dists)) if dists else None
    alts = [s["alt"] for s in samples if s["alt"] is not None]
    gain, loss = _elevation(alts) if alts else (None, None)
    grades = [(s["grade"], s["dt"]) for s in mov if s["grade"] is not None]

    out: dict[str, Any] = {
        "start_s": _r(samples[0]["t"], 0),
        "end_s": _r(samples[-1]["t"] + samples[-1]["dt"], 0),
        "elapsed_s": _r(elapsed, 0),
        "recorded_s": _r(recorded, 0),
        "moving_s": _r(moving_s, 0),
        "distance_m": _r(dist_m, 0),
        "speed_avg_kmh": _r(dist_m / moving_s * 3.6, 1) if dist_m and moving_s else None,
        "hr_avg": _r(_wmean(hr_pairs), 0),
        "hr_min": _r(min(hr_all), 0) if hr_all else None,
        "hr_max": _r(max(hr_all), 0) if hr_all else None,
        "hr_drift_bpm": _r(_hr_drift(mov), 1),
        "cadence_avg": _r(_wmean([(s["cad"], s["dt"]) for s in pedal]), 0),
        "cadence_min": _r(min(cad_vals), 0) if cad_vals else None,
        "cadence_max": _r(max(cad_vals), 0) if cad_vals else None,
        "cadence_below_70_s": _r(sum(s["dt"] for s in pedal if s["cad"] < LOW_CADENCE_RPM), 0)
        if pedal
        else None,
        "coasting_pct": _r(coast_s / moving_s * 100, 1) if cad_known and moving_s else None,
        "elev_gain_m": _r(gain, 0),
        "elev_loss_m": _r(loss, 0),
        "grade_avg_pct": _r(_wmean(grades), 1),
        "grade_max_pct": _r(max(g for g, _ in grades), 1) if grades else None,
        "time_by_grade_s": _grade_buckets(grades) if grades else None,
    }
    watts = [(s["w"], s["dt"]) for s in mov if s["w"] is not None]
    if watts:
        out["watts_avg"] = _r(_wmean(watts), 0)
    if hr_zones and hr_pairs:
        tz = [0.0] * len(hr_zones)
        for h, dt in hr_pairs:
            tz[_zone_index(h, hr_zones)] += dt
        out["time_in_hr_zones_s"] = [_r(v, 0) for v in tz]
    return out


def _hr_drift(mov: list[dict[str, Any]]) -> float | None:
    """Mean HR of the 2nd half of moving time minus the 1st half (>= 10 min)."""
    pts = [(s["hr"], s["dt"]) for s in mov if s["hr"] is not None]
    total = sum(dt for _, dt in pts)
    if total < 600:
        return None
    half, acc = total / 2, 0.0
    first: list[tuple[float, float]] = []
    second: list[tuple[float, float]] = []
    for h, dt in pts:
        (first if acc < half else second).append((h, dt))
        acc += dt
    a, b = _wmean(first), _wmean(second)
    return (b - a) if a is not None and b is not None else None


def _grade_buckets(grades: list[tuple[float, float]]) -> dict[str, int]:
    edges = [
        (-99, -4, "desc_<-4"),
        (-4, -1, "desc_-4_-1"),
        (-1, 1, "plano_-1_1"),
        (1, 4, "sub_1_4"),
        (4, 7, "sub_4_7"),
        (7, 99, "sub_>7"),
    ]
    out = {label: 0.0 for *_, label in edges}
    for g, dt in grades:
        for lo, hi, label in edges:
            if lo <= g < hi:
                out[label] += dt
                break
    return {k: int(round(v)) for k, v in out.items()}


# --------------------------------------------------------------------------- #
# attention events
# --------------------------------------------------------------------------- #
def _runs(samples: list[dict[str, Any]], cond) -> list[tuple[int, int]]:  # type: ignore[no-untyped-def]
    """Index ranges [i, j] where cond(sample) holds continuously."""
    runs, start = [], None
    for i, s in enumerate(samples):
        if cond(s):
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(samples) - 1))
    return runs


def _dur(samples: list[dict[str, Any]], i: int, j: int) -> float:
    return sum(s["dt"] for s in samples[i : j + 1])


def detect_events(
    samples: list[dict[str, Any]],
    hr_zones: list[float] | None,
    min_event_s: int = DEFAULT_MIN_EVENT_S,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def add(kind: str, i: int, j: int, extra: dict[str, Any]) -> None:
        seg = samples[i : j + 1]
        hrs = [s["hr"] for s in seg if s["hr"] is not None]
        ev = {
            "type": kind,
            "start_s": _r(seg[0]["t"], 0),
            "end_s": _r(seg[-1]["t"] + seg[-1]["dt"], 0),
            "duration_s": _r(_dur(samples, i, j), 0),
            "hr_avg": _r(_mean(hrs), 0),
            "hr_max": _r(max(hrs), 0) if hrs else None,
        }
        ev.update(extra)
        events.append(ev)

    # 1) HR above Z3 (zone index >= 3 -> Z4 and up) while moving
    if hr_zones and len(hr_zones) >= 4:
        z3_top = hr_zones[2]
        for i, j in _runs(
            samples, lambda s: s["moving"] and s["hr"] is not None and s["hr"] > z3_top
        ):
            if _dur(samples, i, j) >= min_event_s:
                add("hr_above_z3", i, j, {"threshold_bpm": _r(z3_top, 0)})

    # 2) low cadence while pedalling (grinding)
    for i, j in _runs(
        samples,
        lambda s: s["moving"] and s["cad"] is not None and 0 < s["cad"] < LOW_CADENCE_RPM,
    ):
        if _dur(samples, i, j) >= min_event_s:
            seg = samples[i : j + 1]
            gr = [s["grade"] for s in seg if s["grade"] is not None]
            add(
                "low_cadence",
                i,
                j,
                {
                    "cadence_avg": _r(_mean([s["cad"] for s in seg]), 0),
                    "grade_avg_pct": _r(_mean(gr), 1),
                },
            )

    # 3) long coasting (moving, cadence 0)
    for i, j in _runs(samples, lambda s: s["moving"] and s["cad"] == 0):
        if _dur(samples, i, j) >= COAST_MIN_S:
            add("coasting", i, j, {})

    # 4) stops
    for i, j in _runs(samples, lambda s: not s["moving"]):
        if _dur(samples, i, j) >= STOP_MIN_S:
            add("stop", i, j, {})

    # 5) sustained climbs (tolerates short flatter bits of up to 20 s)
    climbing = [s["grade"] is not None and s["grade"] >= CLIMB_GRADE_PCT for s in samples]
    i, n = 0, len(samples)
    while i < n:
        if not climbing[i]:
            i += 1
            continue
        j, gap = i, 0
        k = i
        while k < n:
            if climbing[k]:
                j, gap = k, 0
            else:
                gap += samples[k]["dt"]
                if gap > 20:
                    break
            k += 1
        seg = samples[i : j + 1]
        d = [s["dist"] for s in seg if s["dist"] is not None]
        a = [s["alt"] for s in seg if s["alt"] is not None]
        length = (max(d) - min(d)) if d else 0.0
        if _dur(samples, i, j) >= CLIMB_MIN_S and length >= CLIMB_MIN_M and a:
            gain = a[-1] - a[0]
            cads = [s["cad"] for s in seg if s["cad"]]
            add(
                "climb",
                i,
                j,
                {
                    "distance_m": _r(length, 0),
                    "elev_gain_m": _r(gain, 0),
                    "grade_avg_pct": _r(gain / length * 100 if length else None, 1),
                    "cadence_avg": _r(_mean(cads), 0),
                },
            )
        i = j + 1

    events.sort(key=lambda e: (e["start_s"] or 0, e["type"]))
    return events


# --------------------------------------------------------------------------- #
# resampling
# --------------------------------------------------------------------------- #
def resample(samples: list[dict[str, Any]], resolution_s: int) -> dict[str, Any]:
    """Columnar series, one row per ``resolution_s`` bucket."""
    has = {
        "hr": any(s["hr"] is not None for s in samples),
        "cad": any(s["cad"] is not None for s in samples),
        "alt": any(s["alt"] is not None for s in samples),
        "dist": any(s["dist"] is not None for s in samples),
        "spd": any(s["spd"] is not None for s in samples),
        "w": any(s["w"] is not None for s in samples),
    }
    names = {
        "hr": "hr_bpm",
        "cad": "cadence_rpm",
        "alt": "altitude_m",
        "dist": "distance_m",
        "spd": "speed_kmh",
        "w": "watts",
    }
    columns = ["t_s"] + [names[k] for k in names if has[k]] + ["grade_pct", "moving"]
    rows: list[list[Any]] = []
    if not samples:
        return {"columns": columns, "data": rows}

    t0 = samples[0]["t"]
    bucket: list[dict[str, Any]] = []
    cur = None

    def flush() -> None:
        if not bucket:
            return
        row: list[Any] = [_r(bucket[0]["t"], 0)]
        for k in names:
            if not has[k]:
                continue
            vals = [s[k] for s in bucket if s[k] is not None]
            if k == "dist":
                row.append(_r(max(vals), 0) if vals else None)
            elif k == "spd":
                row.append(_r(sum(vals) / len(vals) * 3.6, 1) if vals else None)
            elif k == "alt":
                row.append(_r(_mean(vals), 1) if vals else None)
            else:
                row.append(_r(_mean(vals), 0) if vals else None)
        gr = [s["grade"] for s in bucket if s["grade"] is not None]
        row.append(_r(_mean(gr), 1) if gr else None)
        row.append(1 if sum(1 for s in bucket if s["moving"]) * 2 >= len(bucket) else 0)
        rows.append(row)

    for s in samples:
        key = int((s["t"] - t0) // resolution_s)
        if key != cur:
            flush()
            bucket, cur = [], key
        bucket.append(s)
    flush()
    return {"columns": columns, "data": rows}


def _slice(
    samples: list[dict[str, Any]], start_s: float | None, end_s: float | None
) -> list[dict[str, Any]]:
    return [
        s
        for s in samples
        if (start_s is None or s["t"] >= start_s) and (end_s is None or s["t"] < end_s)
    ]


def _laps(activity: dict[str, Any], samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lap boundaries (seconds) from icu_intervals, falling back to indexes."""
    raw = activity.get("icu_intervals") or []
    laps = []
    for n, iv in enumerate(raw, start=1):
        if not isinstance(iv, dict):
            continue
        st, en = _num(iv.get("start_time")), _num(iv.get("end_time"))
        if (st is None or en is None) and samples:
            si, ei = iv.get("start_index"), iv.get("end_index")
            if isinstance(si, int) and isinstance(ei, int) and 0 <= si < len(samples):
                st = samples[si]["t"]
                en = samples[min(ei, len(samples) - 1)]["t"] + 1
        if st is None or en is None or en <= st:
            continue
        laps.append(
            {
                "index": n,
                "label": iv.get("label") or iv.get("type") or f"Trecho {n}",
                "type": iv.get("type"),
                "start_s": st,
                "end_s": en,
            }
        )
    return laps


# --------------------------------------------------------------------------- #
# MCP tool
# --------------------------------------------------------------------------- #
@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Activity Series (second by second)", read_only_hint=True, destructive_hint=False
    )
)
async def get_activity_series(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    activity_id: str,
    resolution_s: int = 10,
    start_s: int = -1,
    end_s: int = -1,
    lap: int = 0,
    include_series: bool = True,
    include_laps: bool = True,
    include_events: bool = True,
    max_points: int = 2000,
    min_event_s: int = DEFAULT_MIN_EVENT_S,
    api_key: str = "",
) -> str:
    """Full time series of an activity (second by second) plus analysis, as JSON.

    Use this instead of get_activity_streams when you need the actual data
    (get_activity_streams only previews the first/last 5 values).

    Returns JSON with:
      - "source": raw points and duration of the activity;
      - "window": the time range and resolution actually returned;
      - "summary": stats of the window (elapsed_s = wall-clock incl. pauses,
        recorded_s = sampled time, HR avg/min/max, HR drift, cadence
        avg/min/max, time below 70 rpm, % coasting, speed, distance,
        elevation gain/loss, grade avg/max, time by grade, time in HR zones);
      - "laps": the same stats for every lap/interval (whole activity);
      - "events": attention points found on the raw data — hr_above_z3,
        low_cadence (<70 rpm), coasting (>=60 s), stop (>=60 s), climb
        (>=4% for >=60 s and >=200 m);
      - "series": {"columns": [...], "data": [[...], ...]} resampled rows.

    Tips to keep replies small:
      - whole ride for charts: resolution_s 5-10 (default 10);
      - true 1 s detail: resolution_s=1 together with a window
        (start_s/end_s, in seconds from the start) or lap=N;
      - include_series=false returns only summary, laps and events.
      If the window would exceed max_points rows, the resolution is raised
      automatically and window.resolution_adjusted is true.

    Args:
        activity_id: The Intervals.icu activity ID (e.g. "i198657558").
        resolution_s: Seconds per returned row (1 = raw). Default 10.
        start_s: Window start, seconds from the activity start (-1 = beginning).
        end_s: Window end, seconds from the activity start (-1 = end).
        lap: 1-based lap/interval to zoom into; -1 = last lap; 0 = no lap filter.
             Overrides start_s/end_s.
        include_series: Return the resampled series rows. Default True.
        include_laps: Return per-lap stats. Default True.
        include_events: Return attention events. Default True.
        max_points: Upper limit of series rows. Default 2000.
        min_event_s: Minimum duration for hr_above_z3 / low_cadence events. Default 30.
        api_key: The Intervals.icu API key (optional, uses API_KEY from .env).
    """
    streams_res, act_res = await asyncio.gather(
        make_intervals_request(
            url=f"/activity/{activity_id}/streams.json",
            api_key=api_key,
            params={"types": STREAM_TYPES},
        ),
        make_intervals_request(
            url=f"/activity/{activity_id}",
            api_key=api_key,
            params={"intervals": "true"},
        ),
    )

    if isinstance(streams_res, dict) and streams_res.get("error"):
        return json.dumps(
            {"error": f"Error fetching streams: {streams_res.get('message', 'unknown')}"}
        )
    activity: dict[str, Any] = (
        act_res if isinstance(act_res, dict) and not act_res.get("error") else {}
    )

    samples = build_samples(_streams_to_columns(streams_res))
    if not samples:
        return json.dumps(
            {
                "activity_id": activity_id,
                "error": "No stream data for this activity (e.g. manual entry or file without samples).",
            }
        )

    hr_zones = [z for z in (_num(v) for v in (activity.get("icu_hr_zones") or [])) if z is not None]
    laps = _laps(activity, samples)

    # window selection
    w_start: float | None = None if start_s < 0 else float(start_s)
    w_end: float | None = None if end_s < 0 else float(end_s)
    lap_info = None
    if lap and laps:
        matches = [lp for lp in laps if lp["index"] == lap]
        chosen = laps[-1] if lap < 0 else (matches[0] if matches else None)
        if chosen is None:
            return json.dumps(
                {
                    "activity_id": activity_id,
                    "error": f"Lap {lap} not found; activity has {len(laps)} laps.",
                }
            )
        w_start, w_end, lap_info = chosen["start_s"], chosen["end_s"], chosen
    window = _slice(samples, w_start, w_end)
    if not window:
        return json.dumps(
            {"activity_id": activity_id, "error": "Empty window for the given start_s/end_s."}
        )

    resolution = max(1, int(resolution_s))
    span = window[-1]["t"] - window[0]["t"] + 1
    adjusted = False
    if include_series and max_points > 0 and span / resolution > max_points:
        resolution = int(math.ceil(span / max_points))
        adjusted = True

    out: dict[str, Any] = {
        "activity_id": activity_id,
        "name": activity.get("name"),
        "start_date_local": activity.get("start_date_local"),
        "type": activity.get("type"),
        "hr_zones_upper_bpm": hr_zones or None,
        "source": {
            "points": len(samples),
            "duration_s": _r(samples[-1]["t"] + samples[-1]["dt"] - samples[0]["t"], 0),
        },
        "window": {
            "start_s": _r(window[0]["t"], 0),
            "end_s": _r(window[-1]["t"] + window[-1]["dt"], 0),
            "lap": {"index": lap_info["index"], "label": lap_info["label"]} if lap_info else None,
            "resolution_s": resolution,
            "resolution_adjusted": adjusted,
        },
        "summary": segment_stats(window, hr_zones),
    }
    if include_laps:
        out["laps"] = [
            {
                "index": lp["index"],
                "label": lp["label"],
                "type": lp["type"],
                **segment_stats(_slice(samples, lp["start_s"], lp["end_s"]), hr_zones),
            }
            for lp in laps
        ]
    if include_events:
        out["events"] = detect_events(window, hr_zones, min_event_s)
    if include_series:
        series = resample(window, resolution)
        out["window"]["points"] = len(series["data"])
        out["series"] = series

    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))
