"""English message catalog for dashboard configuration."""

MESSAGES: dict[str, str] = {
    "dashboard.expected_object": "must be an object",
    "dashboard.heading": "Dashboard:",
    "dashboard.idle_exit_minutes_help": "Stop the dashboard after this many idle minutes.",
    "dashboard.idle_exit_minutes_label": "Idle Exit Minutes",
    "dashboard.no_open_help": "Do not start or open the dashboard for this orchestration run.",
    "dashboard.open_invalid": "must be one of: auto, app, tab, never (got {value})",
    "dashboard.open_label": "Open",
    "dashboard.positive_integer": "{field} must be a positive integer (got {value})",
    "dashboard.timeline": "Timeline: {url}",
    "dashboard.window_height_label": "Window Height",
    "dashboard.window_width_label": "Window Width",
}
