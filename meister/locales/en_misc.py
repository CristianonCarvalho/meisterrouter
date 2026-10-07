"""English message catalog: leftover messages outside the five translated areas."""

MESSAGES: dict[str, str] = {
    "misc.timeline.no_conclusion": "no conclusion",
    "misc.plan_adapter.generated_not_covered": (
        "warning: task {name}: the command {command} may generate {generated}, "
        "which is not covered by Files: or scope.tolerated_files"
    ),
    "misc.models.no_lane_key": "No lane key to estimate the cost",
}
