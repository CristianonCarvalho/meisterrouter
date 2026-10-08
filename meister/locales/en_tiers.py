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
}
