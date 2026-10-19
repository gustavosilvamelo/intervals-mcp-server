"""
MCP tools registry for Intervals.icu MCP Server.

This module registers all available MCP tools with the FastMCP server instance.
"""

from fastmcp import FastMCP

# Import all tools for re-export
# Note: Tools register themselves via @mcp.tool() decorators when imported
from intervals_mcp_server.tools.training_summary import get_training_summary  # noqa: F401
from intervals_mcp_server.tools.activities import (  # noqa: F401
    add_activity_message,
    bulk_create_manual_activities,
    create_manual_activity,
    delete_activity,
    get_activities,
    get_activity_details,
    get_activity_histogram,
    get_activity_intervals,
    get_activity_messages,
    get_activity_streams,
    update_activity,
)
from intervals_mcp_server.tools.activity_analysis import (  # noqa: F401
    get_activity_best_efforts,
    get_activity_curve,
    get_activity_hr_load_model,
    get_activity_interval_stats,
    get_activity_map,
    get_activity_power_spike_model,
    get_activity_power_vs_hr,
    get_activity_segments,
    get_activity_time_at_hr,
    get_activity_weather_summary,
)
from intervals_mcp_server.tools.activity_series import get_activity_series  # noqa: F401
from intervals_mcp_server.tools.activity_intervals import (  # noqa: F401
    delete_activity_intervals,
    split_activity_interval,
    update_activity_interval,
    update_activity_intervals,
)
from intervals_mcp_server.tools.activity_search import (  # noqa: F401
    get_activities_around,
    get_activities_by_ids,
    get_activity_tags,
    interval_search,
    search_activities,
)
from intervals_mcp_server.tools.events import (  # noqa: F401
    add_or_update_event,
    delete_event,
    delete_events_by_date_range,
    get_event_by_id,
    get_events,
    get_races,
)
from intervals_mcp_server.tools.athlete import get_athlete_zones  # noqa: F401
from intervals_mcp_server.tools.training_plan import (  # noqa: F401
    apply_plan_changes,
    apply_plan_to_calendar,
    change_athlete_plans_bulk,
    change_training_plan,
    get_training_plan,
)
from intervals_mcp_server.tools.wellness import get_wellness_data  # noqa: F401
from intervals_mcp_server.tools.workout_library import (  # noqa: F401
    get_workout_folders,
    list_workouts,
    get_workout,
    create_workout,
    update_workout,
    schedule_workout,
)


def register_tools(mcp_instance: FastMCP) -> None:
    """
    Register all MCP tools with the FastMCP server instance.

    This function imports all tool modules, which causes their @mcp.tool()
    decorators to register the tools. The tools need access to the mcp instance,
    so they will be imported after the mcp instance is created.

    Args:
        mcp_instance (FastMCP): The FastMCP server instance to register tools with.
    """
    # Tools are registered via decorators when modules are imported above
    # The mcp_instance parameter is kept for future use if needed
    _ = mcp_instance


__all__ = [
    "register_tools",
    # Activities (read + CRUD)
    "get_activities",
    "get_activity_details",
    "get_activity_histogram",
    "get_activity_intervals",
    "get_activity_messages",
    "get_activity_streams",
    "add_activity_message",
    "update_activity",
    "delete_activity",
    "create_manual_activity",
    "bulk_create_manual_activities",
    # Activity search/discovery
    "search_activities",
    "interval_search",
    "get_activities_around",
    "get_activities_by_ids",
    "get_activity_tags",
    # Activity analysis
    "get_activity_curve",
    "get_activity_best_efforts",
    "get_activity_segments",
    "get_activity_time_at_hr",
    "get_activity_weather_summary",
    "get_activity_interval_stats",
    "get_activity_map",
    "get_activity_power_vs_hr",
    "get_activity_hr_load_model",
    "get_activity_power_spike_model",
    # Activity series (second by second)
    "get_activity_series",
    # Activity interval editing
    "update_activity_intervals",
    "update_activity_interval",
    "delete_activity_intervals",
    "split_activity_interval",
    # Events
    "get_events",
    "get_races",
    "get_event_by_id",
    "delete_event",
    "delete_events_by_date_range",
    "add_or_update_event",
    # Other
    "get_wellness_data",
    "get_athlete_zones",
    "get_training_summary",
    # Training plans
    "get_training_plan",
    "change_training_plan",
    "apply_plan_changes",
    "apply_plan_to_calendar",
    "change_athlete_plans_bulk",
    # Workout library
    "get_workout_folders",
    "list_workouts",
    "get_workout",
    "create_workout",
    "update_workout",
    "schedule_workout",
]
