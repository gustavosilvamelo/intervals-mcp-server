"""
Wellness-related MCP tools for Intervals.icu.

This module contains tools for retrieving and recording athlete wellness data.
"""

from typing import Any

from datetime import datetime

from mcp.types import ToolAnnotations

from intervals_mcp_server.api.client import make_intervals_request
from intervals_mcp_server.config import get_config
from intervals_mcp_server.utils.formatting import (
    WELLNESS_FIELDS,
    format_wellness_entry,
)
from intervals_mcp_server.utils.validation import resolve_athlete_id, resolve_date_params

# Import mcp instance from shared module for tool registration
from intervals_mcp_server.mcp_instance import mcp  # noqa: F401

config = get_config()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Wellness Data", read_only_hint=True, destructive_hint=False
    )
)
async def get_wellness_data(
    athlete_id: str = "",
    api_key: str = "",
    start_date: str = "",
    end_date: str = "",
    fields: list[str] | None = None,
    cadence: int = 0,
    include_all_fields: bool = False,
) -> str:
    """Get wellness data for an athlete from Intervals.icu.

    By default returns standard wellness fields (training metrics, vitals, sleep,
    subjective scores, etc.). Set include_all_fields=True to also include any
    additional or custom fields configured by the user in Intervals.icu.

    Args:
        athlete_id: The Intervals.icu athlete ID (optional, will use ATHLETE_ID from .env if not provided)
        api_key: The Intervals.icu API key (optional, will use API_KEY from .env if not provided)
        start_date: Start date in YYYY-MM-DD format (optional, defaults to 30 days ago)
        end_date: End date in YYYY-MM-DD format (optional, defaults to today)
        fields: List of wellness sections to include (optional, defaults to all).
            Valid values: "training", "sport_info", "vital_signs", "sleep",
            "menstrual", "subjective", "nutrition", "activity".
        cadence: Return every Nth day of data (optional). For example, cadence=7
            returns one entry per week. Use 0 (default) to return all entries
            without cadence filtering. Must be a positive integer when provided.
        include_all_fields: If True, include additional and custom fields beyond the standard set (optional, defaults to False)
    """
    athlete_id_to_use, error_msg = resolve_athlete_id(athlete_id, config.athlete_id)
    if error_msg:
        return error_msg

    start_date, end_date = resolve_date_params(start_date, end_date)

    # Validate fields parameter
    fields_set: set[str] | None = None
    if fields:
        invalid = set(fields) - WELLNESS_FIELDS
        if invalid:
            return (
                f"Invalid field(s): {', '.join(sorted(invalid))}. "
                f"Valid fields: {', '.join(sorted(WELLNESS_FIELDS))}"
            )
        fields_set = set(fields)

    # Validate cadence parameter
    if cadence and cadence < 1:
        return "Cadence must be a positive integer (1 or greater) when provided. Use 0 to disable cadence filtering."

    # Call the Intervals.icu API
    params = {"oldest": start_date, "newest": end_date}

    result = await make_intervals_request(
        url=f"/athlete/{athlete_id_to_use}/wellness", api_key=api_key, params=params
    )

    if isinstance(result, dict) and "error" in result:
        return f"Error fetching wellness data: {result.get('message')}"

    if not result:
        return (
            f"No wellness data found for athlete {athlete_id_to_use} in the specified date range."
        )

    # Collect entries into a flat list
    entries: list[dict] = []
    if isinstance(result, dict):
        for date_str, data in result.items():
            if isinstance(data, dict):
                if "date" not in data:
                    data["date"] = date_str
                entries.append(data)
    elif isinstance(result, list):
        entries = [e for e in result if isinstance(e, dict)]

    # Apply cadence filtering (keep every Nth entry)
    if cadence and cadence > 1:
        entries = entries[::cadence]

    wellness_summary = "Wellness Data:\n\n"
    for entry in entries:
        wellness_summary += (
            format_wellness_entry(entry, fields=fields_set, include_all_fields=include_all_fields)
            + "\n\n"
        )

    return wellness_summary


# Wellness fields the app may write, mapped from tool argument to API field name.
_WELLNESS_WRITABLE: dict[str, str] = {
    "weight": "weight",
    "resting_hr": "restingHR",
    "hrv": "hrv",
    "sleep_secs": "sleepSecs",
    "sleep_quality": "sleepQuality",
    "fatigue": "fatigue",
    "soreness": "soreness",
    "stress": "stress",
    "mood": "mood",
    "motivation": "motivation",
    "injury": "injury",
    "comments": "comments",
}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Wellness Data", read_only_hint=False, destructive_hint=False
    )
)
async def update_wellness_data(  # pylint: disable=too-many-arguments,too-many-locals,too-many-positional-arguments
    date: str,
    athlete_id: str = "",
    api_key: str = "",
    weight: float | None = None,
    resting_hr: int | None = None,
    hrv: float | None = None,
    sleep_secs: int | None = None,
    sleep_quality: int | None = None,
    fatigue: int | None = None,
    soreness: int | None = None,
    stress: int | None = None,
    mood: int | None = None,
    motivation: int | None = None,
    injury: int | None = None,
    comments: str | None = None,
    overwrite: bool = False,
) -> str:
    """Record wellness values for one day in Intervals.icu.

    Only the provided fields are written. By default existing values are never
    overwritten: the current entry is read first and any field that already has a
    value (for example sleep sent by a watch) is skipped and reported. Set
    overwrite=True only when the athlete explicitly asks to replace a value.

    Args:
        date: Day to update, in YYYY-MM-DD format
        athlete_id: The Intervals.icu athlete ID (optional, will use ATHLETE_ID from .env if not provided)
        api_key: The Intervals.icu API key (optional, will use API_KEY from .env if not provided)
        weight: Body weight in kg
        resting_hr: Resting heart rate in bpm
        hrv: Heart rate variability (rMSSD)
        sleep_secs: Sleep duration in seconds
        sleep_quality: Sleep quality score (Intervals.icu scale, 1-4)
        fatigue: Fatigue score (1-4)
        soreness: Muscle soreness score (1-4)
        stress: Stress score (1-4)
        mood: Mood score (1-4)
        motivation: Motivation score (1-4)
        injury: Injury score (1-4)
        comments: Free-text comment for the day
        overwrite: If True, replace values that are already filled (default False)
    """
    athlete_id_to_use, error_msg = resolve_athlete_id(athlete_id, config.athlete_id)
    if error_msg:
        return error_msg

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return "Error: date must be in YYYY-MM-DD format."

    provided = {
        "weight": weight,
        "resting_hr": resting_hr,
        "hrv": hrv,
        "sleep_secs": sleep_secs,
        "sleep_quality": sleep_quality,
        "fatigue": fatigue,
        "soreness": soreness,
        "stress": stress,
        "mood": mood,
        "motivation": motivation,
        "injury": injury,
        "comments": comments,
    }
    wanted = {_WELLNESS_WRITABLE[k]: v for k, v in provided.items() if v is not None}
    if not wanted:
        return "Error: At least one wellness field must be provided."

    url = f"/athlete/{athlete_id_to_use}/wellness/{date}"
    skipped: list[str] = []
    to_write = wanted

    if not overwrite:
        existing = await make_intervals_request(url=url, api_key=api_key)
        # A day with no entry yet returns an error (404) or an empty body: nothing to protect.
        current: dict[str, Any] = (
            existing if isinstance(existing, dict) and "error" not in existing else {}
        )
        skipped = sorted(k for k in wanted if current.get(k) is not None)
        to_write = {k: v for k, v in wanted.items() if k not in skipped}

    if not to_write:
        return f"Nothing written for {date}: already filled and kept as is: {', '.join(skipped)}."

    result = await make_intervals_request(url=url, api_key=api_key, method="PUT", data=to_write)
    if isinstance(result, dict) and "error" in result:
        return f"Error updating wellness data: {result.get('message', 'Unknown error')}"

    message = f"Successfully updated wellness for {date}: {', '.join(sorted(to_write))}."
    if skipped:
        message += f" Kept existing values (not overwritten): {', '.join(skipped)}."
    return message
