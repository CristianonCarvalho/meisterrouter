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
    "tiers.models.enable_help": "Enable a configured lane (may be repeated).",
    "tiers.models.disable_help": "Disable a configured lane (may be repeated).",
    "tiers.models.effort": "EFFORT",
    "tiers.last_enabled_lane": (
        "Cannot disable the last enabled lane; at least one lane must remain enabled."
    ),
    "tiers.unknown_lane": "Unknown lane(s): {lanes}. Valid lane names: {valid}",
    "tiers.conflicting_toggle": "Cannot enable and disable the same lane: {lanes}",
    "tiers.toggle_error": "Could not update enabled lanes: {error}",
    "tiers.invalid_configuration": "Cannot update lanes because the configuration is invalid:",
    "tiers.configuration_issue": "{path}: {message}",
}
