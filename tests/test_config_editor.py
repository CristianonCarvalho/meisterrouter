import hashlib
import os
import stat
import sys

from meister.config import _default_config_data
import pytest

from meister import config_editor
from meister.config_editor import (
    ConfigEditorError,
    ConfigStampError,
    ConfigValidationError,
    GENERATED_COMMENT,
    LaneDocument,
    LaneEdit,
    apply_lanes,
    load_effective_lanes,
    restore_default_lanes,
    validate_lanes,
)
from meister.i18n import EN_MESSAGES, PT_BR_MESSAGES, t


PROJECT_CONFIG = """\
# Comentario do topo
workers:
  # vias do projeto
  tier_order:
    - name: lane_a  # primeira
      harness: codex
      model: gpt-x
      effort: medium
      cost_per_m_tokens: 0.2
    - name: lane_b
      harness: claude
      model: sonnet
      enabled: false
      max_parallel: 2
# fim
"""


def _default_lanes() -> list:
    return _default_config_data()["workers"]["tier_order"]


def test_reading_does_not_change_file_with_comments(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(PROJECT_CONFIG.encode("utf-8"))

    load_effective_lanes(str(path))

    assert path.read_text(encoding="utf-8") == PROJECT_CONFIG


def test_without_tier_order_returns_default_lanes_including_disabled(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(("# so comentario\nconcurrency:\n  max_parallel_workers: 2\n").encode("utf-8"))

    document = load_effective_lanes(str(path))

    assert isinstance(document, LaneDocument)
    assert document.has_tier_order is False
    assert document.path == str(path)
    assert document.lanes == _default_lanes()
    assert any(lane.get("enabled") is False for lane in document.lanes) == any(
        lane.get("enabled") is False for lane in _default_lanes()
    )


def test_missing_file_returns_default_lanes_and_empty_stamp(tmp_path):
    path = tmp_path / "meister.config.yaml"

    document = load_effective_lanes(str(path))

    assert document.has_tier_order is False
    assert document.lanes == _default_lanes()
    assert document.stamp == hashlib.sha256(b"").hexdigest()
    assert not path.exists()


def test_with_tier_order_returns_project_lanes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(PROJECT_CONFIG.encode("utf-8"))

    document = load_effective_lanes(str(path))

    assert document.has_tier_order is True
    assert [lane["name"] for lane in document.lanes] == ["lane_a", "lane_b"]
    assert document.lanes[0] == {
        "name": "lane_a",
        "harness": "codex",
        "model": "gpt-x",
        "effort": "medium",
        "cost_per_m_tokens": 0.2,
    }
    assert type(document.lanes[0]) is dict
    assert document.lanes[1]["enabled"] is False
    assert document.lanes[1]["max_parallel"] == 2


def test_stamp_changes_when_file_changes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(PROJECT_CONFIG.encode("utf-8"))
    first = load_effective_lanes(str(path)).stamp

    assert load_effective_lanes(str(path)).stamp == first
    assert first == hashlib.sha256(PROJECT_CONFIG.encode("utf-8")).hexdigest()

    path.write_bytes(PROJECT_CONFIG.replace("gpt-x", "gpt-y").encode("utf-8"))

    assert load_effective_lanes(str(path)).stamp != first


def test_lane_edit_holds_editable_fields():
    edit = LaneEdit(name="lane_a", harness="codex", model="gpt-x", effort=None, enabled=True, max_parallel=None)

    assert edit.name == "lane_a"
    assert edit.enabled is True


def _project_document(tmp_path) -> LaneDocument:
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(PROJECT_CONFIG.encode("utf-8"))
    return load_effective_lanes(str(path))


def _valid_request() -> list:
    return [
        {"name": "lane_b", "harness": "claude", "model": "sonnet", "effort": "high", "enabled": True, "max_parallel": 3},
        {"name": "lane_a", "harness": "codex", "model": "gpt-x", "effort": "medium", "enabled": True, "max_parallel": None},
    ]


def _errors(issues) -> list:
    return [issue for issue in issues if issue.level == "error"]


def test_valid_request_has_no_errors_and_writes_nothing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    document = _project_document(project)

    issues = validate_lanes(document, _valid_request())

    assert _errors(issues) == []
    assert (project / "meister.config.yaml").read_text(encoding="utf-8") == PROJECT_CONFIG
    assert sorted(path.name for path in project.iterdir()) == ["meister.config.yaml"]
    assert [lane["name"] for lane in document.lanes] == ["lane_a", "lane_b"]


def test_valid_request_accepts_lane_edit_objects(tmp_path):
    document = _project_document(tmp_path)
    lanes = [LaneEdit(**lane) for lane in _valid_request()]

    assert _errors(validate_lanes(document, lanes)) == []


def test_valid_request_against_default_lanes_without_file(tmp_path):
    document = load_effective_lanes(str(tmp_path / "meister.config.yaml"))
    lanes = [
        {key: lane.get(key) for key in ("name", "harness", "model", "effort", "enabled", "max_parallel")}
        for lane in document.lanes
    ]
    for lane in lanes:
        lane["enabled"] = lane["enabled"] is not False

    assert _errors(validate_lanes(document, lanes)) == []


def test_fields_may_be_omitted_and_keep_current_values(tmp_path):
    document = _project_document(tmp_path)

    issues = validate_lanes(document, [{"name": "lane_a"}, {"name": "lane_b"}])

    assert _errors(issues) == []


def test_no_enabled_lane_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    for lane in lanes:
        lane["enabled"] = False

    issues = _errors(validate_lanes(document, lanes))

    assert [issue.path for issue in issues] == ["lanes"]
    assert issues[0].message == t("config_editor.no_enabled_lane")


def test_duplicate_name_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[1]["name"] = "lane_b"

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert "lanes[1].name" in paths


def test_unknown_lane_cannot_be_created(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request() + [{"name": "lane_new", "harness": "codex", "model": "m", "enabled": True}]

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[2].name"]


def test_missing_lane_cannot_be_removed(tmp_path):
    document = _project_document(tmp_path)

    issues = _errors(validate_lanes(document, _valid_request()[:1]))

    assert [issue.path for issue in issues] == ["lanes"]
    assert "lane_a" in issues[0].message


def test_missing_name_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    del lanes[0]["name"]

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert "lanes[0].name" in paths


def test_invalid_harness_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["harness"] = "bogus"

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[0].harness"]


def test_invalid_effort_for_harness_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[1]["effort"] = "max"  # codex nao aceita max

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[1].effort"]


def test_null_effort_is_valid(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[1]["effort"] = None

    assert _errors(validate_lanes(document, lanes)) == []


@pytest.mark.parametrize("model", ["", "   ", None, 3])
def test_empty_model_is_error(tmp_path, model):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["model"] = model

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[0].model"]


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", True])
def test_invalid_max_parallel_is_error(tmp_path, value):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["max_parallel"] = value

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[0].max_parallel"]


def test_non_boolean_enabled_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["enabled"] = "yes"

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert "lanes[0].enabled" in paths


@pytest.mark.parametrize("field_name", ["cost_per_m_tokens", "best_for", "credit_usd", "whatever"])
def test_non_editable_field_is_error(tmp_path, field_name):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0][field_name] = 1

    issues = _errors(validate_lanes(document, lanes))

    assert [issue.path for issue in issues] == [f"lanes[0].{field_name}"]


def test_lanes_must_be_a_list_of_objects(tmp_path):
    document = _project_document(tmp_path)

    assert [issue.path for issue in _errors(validate_lanes(document, {"name": "lane_a"}))] == ["lanes"]
    assert [issue.path for issue in _errors(validate_lanes(document, ["lane_a", _valid_request()[1]]))] == [
        "lanes[0]"
    ]


def test_config_validation_runs_on_resulting_document(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(PROJECT_CONFIG.replace("cost_per_m_tokens: 0.2", "credit_usd: -5").encode("utf-8"))
    document = load_effective_lanes(str(path))

    paths = [issue.path for issue in _errors(validate_lanes(document, _valid_request()))]

    assert any(path.startswith("workers.tier_order") and path.endswith("credit_usd") for path in paths)


def test_config_editor_messages_exist_in_both_catalogs():
    keys = {key for key in EN_MESSAGES if key.startswith("config_editor.")}

    assert keys
    assert keys == {key for key in PT_BR_MESSAGES if key.startswith("config_editor.")}
    assert all(value.isascii() for key, value in EN_MESSAGES.items() if key in keys)


# Copia do meister.config.yaml do projeto (sem tier_order), com os seus cinco comentarios.
REALISTIC_CONFIG = """\
router:
  # Primeira via do catálogo, sem Jev: o agy travou em planos e o Copilot concluiu os planos recentes; o fallback do tier_order continua valendo.
  mode: first
scope:
  # Alterações em tests/ fora do escopo são aceitas se a suíte inteira passar e ficam registradas no evento scope_tolerated.
  tolerated_files:
    - pnpm-lock.yaml
    - package-lock.json
    - yarn.lock
    - poetry.lock
    - uv.lock
    - "*.tsbuildinfo"
    - .vitest/**
    - tests/**
gate:
  commands:
    # O lint rápido falha cedo e mantém o feedback ágil.
    - name: ruff
      run: "{python} -m ruff check ."
      timeout_seconds: 120
      required: true
    # O mypy também roda no gate local, não apenas no CI.
    - name: mypy
      run: "{python} -m mypy meister"
      timeout_seconds: 300
      required: true
    # A suíte pode levar até três minutos; 900 s evita timeout sob carga.
    - name: pytest
      run: "{python} -m pytest -q -p no:cacheprovider"
      timeout_seconds: 900
      required: true
"""


def _files(directory) -> list:
    return sorted(path.name for path in directory.iterdir() if path.name != "_meister_isolated")


def _comments(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("#")]


def _realistic_document(tmp_path) -> LaneDocument:
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(REALISTIC_CONFIG.encode("utf-8"))
    return load_effective_lanes(str(path))


def _edit_request(document: LaneDocument) -> list:
    lanes = [
        {key: lane.get(key) for key in ("name", "harness", "model", "effort", "enabled", "max_parallel")}
        for lane in document.lanes
    ]
    for lane in lanes:
        lane["enabled"] = lane["enabled"] is not False
    return lanes


def test_apply_lanes_keeps_the_five_realistic_comments(tmp_path):
    document = _realistic_document(tmp_path)
    lanes = _edit_request(document)
    lanes.reverse()
    lanes[0]["model"] = "modelo-novo"

    result = apply_lanes(document, lanes, document.stamp)

    text = (tmp_path / "meister.config.yaml").read_text(encoding="utf-8")
    original_comments = _comments(REALISTIC_CONFIG)
    assert len(original_comments) == 5
    assert _comments(text)[:5] == original_comments
    assert text.startswith(REALISTIC_CONFIG)
    assert GENERATED_COMMENT in text
    assert result.has_tier_order is True
    assert [lane["name"] for lane in result.lanes] == [lane["name"] for lane in lanes]
    assert result.lanes[0]["model"] == "modelo-novo"
    assert result.stamp == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_apply_lanes_preserves_non_editable_fields_and_order_of_keys(tmp_path):
    document = _realistic_document(tmp_path)
    lanes = _edit_request(document)
    lanes.reverse()

    result = apply_lanes(document, lanes, document.stamp)

    defaults = {lane["name"]: lane for lane in _default_lanes()}
    for lane in result.lanes:
        default = defaults[lane["name"]]
        for key in ("cost_per_m_tokens", "best_for", "eligible_classes", "max_retries", "credit_usd"):
            assert lane.get(key) == default.get(key)
    top_keys = [line.split(":")[0] for line in (tmp_path / "meister.config.yaml").read_text().splitlines()
                if line and not line.startswith((" ", "#"))]
    assert top_keys == ["router", "scope", "gate", "workers"]


def test_apply_lanes_round_trip_on_existing_tier_order_keeps_comments(tmp_path):
    document = _project_document(tmp_path)

    result = apply_lanes(document, _valid_request(), document.stamp)

    text = (tmp_path / "meister.config.yaml").read_text(encoding="utf-8")
    for comment in ("# Comentario do topo", "# vias do projeto", "# primeira", "# fim"):
        assert comment in text
    assert [lane["name"] for lane in result.lanes] == ["lane_b", "lane_a"]
    assert result.lanes[1]["cost_per_m_tokens"] == 0.2
    assert result.lanes[0]["enabled"] is True
    assert result.lanes[0]["max_parallel"] == 3
    assert result.lanes[0]["effort"] == "high"
    assert "max_parallel" not in result.lanes[1]


def test_generated_comment_is_written_only_once(tmp_path):
    document = _realistic_document(tmp_path)
    first = apply_lanes(document, _edit_request(document), document.stamp)

    apply_lanes(first, _edit_request(first), first.stamp)

    assert (tmp_path / "meister.config.yaml").read_text(encoding="utf-8").count(GENERATED_COMMENT) == 1


def test_apply_lanes_creates_backup_with_original_content(tmp_path):
    document = _realistic_document(tmp_path)

    apply_lanes(document, _edit_request(document), document.stamp)

    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == REALISTIC_CONFIG.encode("utf-8")


def test_apply_lanes_creates_missing_file_without_backup(tmp_path):
    path = tmp_path / "meister.config.yaml"
    document = load_effective_lanes(str(path))

    result = apply_lanes(document, _edit_request(document), document.stamp)

    assert path.exists()
    assert not (tmp_path / "meister.config.yaml.bak").exists()
    assert result.has_tier_order is True


def test_failure_while_replacing_keeps_original_byte_for_byte(tmp_path, monkeypatch):
    document = _realistic_document(tmp_path)
    (tmp_path / "meister.config.yaml.bak").write_bytes(b"backup antigo\n")
    real_replace = os.replace

    def failing_replace(source, target):
        if os.fspath(target).endswith("meister.config.yaml"):
            raise OSError("disco cheio")
        return real_replace(source, target)

    monkeypatch.setattr(config_editor.os, "replace", failing_replace)

    with pytest.raises(ConfigEditorError):
        apply_lanes(document, _edit_request(document), document.stamp)

    assert (tmp_path / "meister.config.yaml").read_bytes() == REALISTIC_CONFIG.encode("utf-8")
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == b"backup antigo\n"
    assert _files(tmp_path) == ["meister.config.yaml", "meister.config.yaml.bak"]


def test_failure_after_replacing_restores_original_from_backup(tmp_path, monkeypatch):
    document = _realistic_document(tmp_path)

    def broken_check(path, content):
        raise ValueError("falha injetada")

    monkeypatch.setattr(config_editor, "_check_written", broken_check)

    with pytest.raises(ConfigEditorError):
        apply_lanes(document, _edit_request(document), document.stamp)

    assert (tmp_path / "meister.config.yaml").read_bytes() == REALISTIC_CONFIG.encode("utf-8")
    assert not (tmp_path / "meister.config.yaml.bak").exists()
    assert _files(tmp_path) == ["meister.config.yaml"]


def test_different_stamp_is_refused_and_nothing_is_written(tmp_path):
    document = _realistic_document(tmp_path)
    changed = REALISTIC_CONFIG + "# editado a mao\n"
    (tmp_path / "meister.config.yaml").write_bytes(changed.encode("utf-8"))

    with pytest.raises(ConfigStampError) as error:
        apply_lanes(document, _edit_request(document), document.stamp)

    assert str(error.value) == t("config_editor.stamp_mismatch")
    assert (tmp_path / "meister.config.yaml").read_text(encoding="utf-8") == changed
    assert not (tmp_path / "meister.config.yaml.bak").exists()


def test_invalid_lanes_are_refused_with_issues(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["harness"] = "bogus"

    with pytest.raises(ConfigValidationError) as error:
        apply_lanes(document, lanes, document.stamp)

    assert [issue.path for issue in error.value.issues] == ["lanes[0].harness"]
    assert (tmp_path / "meister.config.yaml").read_text(encoding="utf-8") == PROJECT_CONFIG


@pytest.mark.skipif(sys.platform == "win32", reason="os.chmod nao define bits de permissao POSIX no Windows")
def test_apply_lanes_preserves_file_mode(tmp_path):
    document = _realistic_document(tmp_path)
    path = tmp_path / "meister.config.yaml"
    os.chmod(path, 0o640)

    apply_lanes(document, _edit_request(document), document.stamp)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
    assert stat.S_IMODE(os.stat(tmp_path / "meister.config.yaml.bak").st_mode) == 0o640


@pytest.mark.skipif(sys.platform == "win32", reason="os.chmod nao define bits de permissao POSIX no Windows")
def test_restore_default_removes_tier_order_and_empty_workers(tmp_path):
    document = _realistic_document(tmp_path)
    written = apply_lanes(document, _edit_request(document), document.stamp)
    path = tmp_path / "meister.config.yaml"
    os.chmod(path, 0o600)
    before_restore = path.read_bytes()

    result = restore_default_lanes(written, written.stamp)

    text = path.read_text(encoding="utf-8")
    assert "workers" not in text
    assert GENERATED_COMMENT not in text
    assert _comments(text) == _comments(REALISTIC_CONFIG)
    assert result.has_tier_order is False
    assert result.lanes == _default_lanes()
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == before_restore
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_restore_default_keeps_other_worker_keys(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(PROJECT_CONFIG.replace("workers:\n", "workers:\n  enabled:\n    lane_a: true\n").encode("utf-8"))
    document = load_effective_lanes(str(path))

    result = restore_default_lanes(document, document.stamp)

    text = path.read_text(encoding="utf-8")
    assert "tier_order" not in text
    assert "lane_a: true" in text
    assert "# Comentario do topo" in text
    assert result.has_tier_order is False


def test_restore_default_refuses_different_stamp(tmp_path):
    document = _project_document(tmp_path)

    with pytest.raises(ConfigStampError):
        restore_default_lanes(document, "outro")

    assert (tmp_path / "meister.config.yaml").read_text(encoding="utf-8") == PROJECT_CONFIG


def test_restore_default_without_tier_order_writes_nothing(tmp_path):
    document = _realistic_document(tmp_path)

    result = restore_default_lanes(document, document.stamp)

    assert (tmp_path / "meister.config.yaml").read_text(encoding="utf-8") == REALISTIC_CONFIG
    assert _files(tmp_path) == ["meister.config.yaml"]
    assert result.has_tier_order is False
