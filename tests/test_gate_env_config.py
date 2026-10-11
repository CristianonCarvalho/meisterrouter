from meister.config import MeisterConfig, _parse_config_dict, validate_config


def _issues(cfg, path_prefix):
    return [issue for issue in validate_config(cfg) if issue.path.startswith(path_prefix)]


def test_gate_env_defaults_to_empty():
    assert MeisterConfig().gate.env == {}
    cfg = _parse_config_dict({})
    assert cfg.gate.env == {}
    assert _issues(cfg, "gate.env") == []


def test_gate_env_parses_string_map():
    cfg = _parse_config_dict({"gate": {"env": {"JAVA_HOME": "/opt/jdk", "MAVEN_OPTS": ""}}})
    assert cfg.gate.env == {"JAVA_HOME": "/opt/jdk", "MAVEN_OPTS": ""}
    assert _issues(cfg, "gate.env") == []


def test_gate_env_invalid_value_is_dropped_with_issue():
    cfg = _parse_config_dict({"gate": {"env": {"OK": "1", "BAD": 3, "NONE": None, 5: "x"}}})
    assert cfg.gate.env == {"OK": "1"}
    paths = {issue.path for issue in _issues(cfg, "gate.env")}
    assert {"gate.env.BAD", "gate.env.NONE", "gate.env"} <= paths
    assert all(issue.level == "error" for issue in _issues(cfg, "gate.env"))


def test_gate_env_not_object_uses_default():
    cfg = _parse_config_dict({"gate": {"env": ["A=1"]}})
    assert cfg.gate.env == {}
    issues = _issues(cfg, "gate.env")
    assert [issue.path for issue in issues] == ["gate.env"]
    assert issues[0].level == "error"


def test_target_dir_is_tolerated_by_default():
    assert "target/**" in MeisterConfig().scope.tolerated_files
    assert "target/**" in _parse_config_dict({}).scope.tolerated_files
