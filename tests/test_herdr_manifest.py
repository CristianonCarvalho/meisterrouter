try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path


def test_herdr_plugin_manifest_structure():
    manifest_path = Path("herdr-plugin.toml")
    assert manifest_path.exists(), "herdr-plugin.toml must exist at repo root"

    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)

    assert data["id"] == "dev.meisterrouter.orchestrator"
    assert data["name"] == "MeisterRouter"
    assert data["min_herdr_version"] == "0.7.0"
    assert "startup" in data and len(data["startup"]) > 0
    assert "actions" in data and len(data["actions"]) >= 3
    assert any(a["id"] == "auto-orchestrate" for a in data["actions"])
    assert "panes" in data and any(p["id"] == "dashboard" for p in data["panes"])

    wrapper_path = Path("bin/herdr-meister.sh")
    assert wrapper_path.exists(), "bin/herdr-meister.sh must exist"

    all_commands = []
    for item in data.get("startup", []):
        all_commands.append(item.get("command", []))
    for item in data.get("actions", []):
        all_commands.append(item.get("command", []))
    for item in data.get("panes", []):
        all_commands.append(item.get("command", []))

    for cmd in all_commands:
        assert cmd[:2] == ["sh", "bin/herdr-meister.sh"], f"Command must start with wrapper: {cmd}"

    assert data["startup"][0]["command"] == ["sh", "bin/herdr-meister.sh", "daemon", "--start"]

    actions_by_id = {a["id"]: a["command"] for a in data["actions"]}
    assert actions_by_id["classify-task"] == ["sh", "bin/herdr-meister.sh", "herdr-action", "classify"]
    assert actions_by_id["verify-gate"] == ["sh", "bin/herdr-meister.sh", "herdr-action", "verify"]
    assert actions_by_id["auto-orchestrate"] == ["sh", "bin/herdr-meister.sh", "herdr-action", "orchestrate"]

    panes_by_id = {p["id"]: p["command"] for p in data["panes"]}
    assert panes_by_id["dashboard"] == ["sh", "bin/herdr-meister.sh", "dashboard", "--tui"]
