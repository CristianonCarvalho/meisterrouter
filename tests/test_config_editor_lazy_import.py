"""O config_editor importa sem o ruamel.yaml e so falha (com erro proprio) ao precisar dele."""

import importlib
import sys

import pytest

import meister
import meister.config_editor as config_editor


RUAMEL_MODULES = ("ruamel", "ruamel.yaml", "ruamel.yaml.comments", "ruamel.yaml.error")

CONFIG = """\
# comentario do dono
workers:
  tier_order:
    - name: tier_1
      harness: copilot
      model: gpt-5-mini
"""


@pytest.fixture
def without_ruamel(monkeypatch):
    """Importa uma copia nova do modulo com o ruamel bloqueado; ao final volta o modulo original.

    O original e devolvido (sem reload) para nao trocar a identidade das classes usadas por outros testes.
    """
    for name in list(sys.modules):
        if name == "ruamel" or name.startswith("ruamel."):
            monkeypatch.delitem(sys.modules, name)
    for name in RUAMEL_MODULES:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.delitem(sys.modules, "meister.config_editor")
    monkeypatch.setattr(meister, "config_editor", config_editor)
    module = importlib.import_module("meister.config_editor")
    assert module is not config_editor
    yield module


def _write_config(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return str(path)


def test_module_imports_without_ruamel(without_ruamel):
    assert issubclass(without_ruamel.ConfigEditorUnavailable, without_ruamel.ConfigEditorError)


def test_functions_needing_ruamel_raise_unavailable(without_ruamel, tmp_path):
    module = without_ruamel
    path = _write_config(tmp_path)
    with pytest.raises(module.ConfigEditorUnavailable, match="ruamel.yaml"):
        module.load_effective_lanes(path)

    document = module.LaneDocument(path=path, has_tier_order=True, stamp=module._stamp(open(path, "rb").read()))
    with pytest.raises(module.ConfigEditorUnavailable):
        module.apply_lanes(document, [], document.stamp)
    with pytest.raises(module.ConfigEditorUnavailable):
        module.restore_default_lanes(document, document.stamp)


def test_missing_file_without_ruamel_uses_defaults(without_ruamel, tmp_path):
    document = without_ruamel.load_effective_lanes(str(tmp_path / "missing.yaml"))
    assert document.has_tier_order is False
    assert document.lanes


def test_with_ruamel_everything_works(tmp_path):
    module = importlib.import_module("meister.config_editor")
    assert module is config_editor
    path = _write_config(tmp_path)
    document = module.load_effective_lanes(path)
    assert document.has_tier_order is True
    lanes = [dict(document.lanes[0], model="gpt-5")]
    updated = module.apply_lanes(document, lanes, document.stamp)
    assert updated.lanes[0]["model"] == "gpt-5"
    assert "# comentario do dono" in open(path, encoding="utf-8").read()
