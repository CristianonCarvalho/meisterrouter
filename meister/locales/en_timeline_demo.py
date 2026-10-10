"""English message catalog for the synthetic timeline demo command."""

MESSAGES: dict[str, str] = {
    "timeline_demo.check_arrows": "Dependency arrows are drawn as elbows between lanes",
    "timeline_demo.check_axis": "The time axis is in minutes",
    "timeline_demo.check_ghost": "No ghost phase: the interrupted attempt shows no invented segment",
    "timeline_demo.check_lane_header": "Each lane header shows its via (tier) and harness",
    "timeline_demo.check_sound": "Press s to toggle the sound",
    "timeline_demo.check_tooltip": "Hovering a bar shows phase and total durations",
    "timeline_demo.checklist_heading": "What to check:",
    "timeline_demo.description": "Serve the timeline web view over a synthetic log (no model, no cost).",
    "timeline_demo.header": "Timeline demo: synthetic log, mode={mode}, speed={speed}x",
    "timeline_demo.host_help": "Host to bind (default: 127.0.0.1).",
    "timeline_demo.mode_help": "static: finished history; live: events arrive over time (default: live).",
    "timeline_demo.port_help": "Port to bind (default: 5052).",
    "timeline_demo.speed_invalid": "--speed must be a positive number (got {value})",
    "timeline_demo.speed_help": "Live speed multiplier; offsets and durations are divided by it (default: 30).",
    "timeline_demo.stop_hint": "Press Ctrl+C to stop; the temporary log is removed.",
    "timeline_demo.stopped": "Timeline demo stopped; temporary log removed.",
    "timeline_demo.url": "Open: {url}",
}
