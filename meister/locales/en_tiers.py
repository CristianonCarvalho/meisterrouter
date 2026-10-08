"""English messages for worker tier configuration."""

MESSAGES: dict[str, str] = {
    "tiers.invalid_effort": (
        "Lane '{lane}' uses harness '{harness}'; effort '{effort}' is invalid. "
        "Accepted values: {values}"
    ),
    "tiers.unsupported_effort": (
        "Lane '{lane}' uses harness '{harness}', which does not support effort "
        "(accepted values: none); omit the effort setting."
    ),
    "tiers.unknown_enabled_lane": "workers.enabled references unknown lane '{lane}'; the entry is ignored.",
    "tiers.enabled_name_string": "workers.enabled lane names must be strings.",
}
