import tomllib
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
