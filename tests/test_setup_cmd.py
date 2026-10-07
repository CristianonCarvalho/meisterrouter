"""
tests.test_setup_cmd — Testes unitários para a lógica pura de setup_cmd.py.

Sem rede, sem herdr real e sem tocar no HOME real.
"""

import shutil
import stat
import sys
from dataclasses import dataclass


from meister.setup_cmd import (
    START_MARKER,
    END_MARKER,
    check_herdr,
    check_openrouter_key,
    check_python,
    check_worker_clis,
    ensure_herdr_plugin,
    get_desired_bindings,
    merge_keys_block,
    render_keys_block,
    resolve_meister_path,
    write_herdr_config,
)
from meister.config import MeisterConfig, RouterConfig, WorkerTier, WorkersConfig


OWNER_FIXTURE = """# General settings
[general]
theme = "dark"

# MeisterRouter Shortcuts
[[keys.command]]
key = "prefix+m"
type = "shell"
command = "/Users/cristianocarvalho/.local/bin/meister herdr-action orchestrate"

[[keys.command]]
key = "ctrl+alt+m"
type = "shell"
command = "/Users/cristianocarvalho/.local/bin/meister herdr-action orchestrate"

[[keys.command]]
key = "prefix+shift+m"
type = "popup"
command = "/Users/cristianocarvalho/.local/bin/meister dashboard --tui"
width = "85%"
height = "85%"

[[keys.command]]
key = "ctrl+alt+shift+m"
type = "popup"
command = "/Users/cristianocarvalho/.local/bin/meister dashboard --tui"
width = "85%"
height = "85%"

[[keys.command]]
key = "prefix+t"
type = "popup"
command = "/Users/cristianocarvalho/.local/bin/meister timeline"
width = "85%"
height = "85%"
description = "MeisterRouter: linha do tempo (Gantt) do projeto"

[[keys.command]]
key = "ctrl+alt+t"
type = "popup"
command = "/Users/cristianocarvalho/.local/bin/meister timeline"
width = "85%"
height = "85%"
description = "MeisterRouter: linha do tempo (Gantt) do projeto (direto)"

# Outros plugins
[[keys.command]]
key = "prefix+u"
type = "shell"
command = "herdr plugin pane open --plugin herdr-agent-usage --entrypoint dashboard --focus"
"""


@dataclass
class FakeCompletedProcess:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


# ── Testes de renderização e mesclagem ─────────────────────────────────────────

def test_render_keys_block_default():
    block = render_keys_block("/custom/bin/meister", direct_keys=False)
    assert START_MARKER in block
    assert END_MARKER in block
    assert 'key = "prefix+m"' in block
    assert 'command = "/custom/bin/meister herdr-action orchestrate"' in block
    assert 'key = "prefix+shift+m"' in block
    assert 'command = "/custom/bin/meister dashboard --tui"' in block
    assert 'key = "prefix+t"' in block
    assert 'command = "/custom/bin/meister timeline"' in block
    assert 'ctrl+alt+m' not in block


def test_render_keys_block_direct_keys():
    block = render_keys_block("/custom/bin/meister", direct_keys=True)
    assert 'key = "prefix+m"' in block
    assert 'key = "prefix+shift+m"' in block
    assert 'key = "prefix+t"' in block
    assert 'key = "ctrl+alt+m"' in block
    assert 'key = "ctrl+alt+shift+m"' in block
    assert 'key = "ctrl+alt+t"' in block


def test_merge_keys_block_empty_file():
    desired = get_desired_bindings("/bin/meister")
    res = merge_keys_block("", desired)
    assert res.changed is True
    assert set(res.added) == {"prefix+m", "prefix+shift+m", "prefix+t"}
    assert res.actions["prefix+m"] == "added"
    assert START_MARKER in res.new_text
    assert END_MARKER in res.new_text
    assert res.new_text.endswith(f"{END_MARKER}\n")


def test_merge_keys_block_preserves_external_content_with_newline():
    existing = "[general]\nmode = 'auto'\n\n# User comment\n"
    desired = get_desired_bindings("/bin/meister")
    res = merge_keys_block(existing, desired)
    assert res.changed is True
    assert res.new_text.startswith(existing)
    assert START_MARKER in res.new_text
    assert END_MARKER in res.new_text


def test_merge_keys_block_preserves_external_content_without_trailing_newline():
    existing = "[general]\nmode = 'auto'"
    desired = get_desired_bindings("/bin/meister")
    res = merge_keys_block(existing, desired)
    assert res.changed is True
    assert res.new_text.startswith(existing)
    assert f"\n\n{START_MARKER}" in res.new_text


def test_merge_keys_block_owner_fixture_all_already():
    desired = get_desired_bindings("/Users/cristianocarvalho/.local/bin/meister", direct_keys=True)
    res = merge_keys_block(OWNER_FIXTURE, desired)
    assert res.changed is False
    assert res.new_text == OWNER_FIXTURE
    for b in desired:
        assert res.actions[b.key] == "already"
    assert len(res.added) == 0
    assert len(res.conflict) == 0


def test_merge_keys_block_conflict_not_overwritten():
    existing = """[[keys.command]]
key = "prefix+t"
type = "shell"
command = "my_custom_tmux_script.sh"
"""
    desired = get_desired_bindings("/bin/meister")
    res = merge_keys_block(existing, desired)
    assert res.actions["prefix+t"] == "conflict"
    assert "my_custom_tmux_script.sh" in res.new_text
    assert START_MARKER in res.new_text
    assert 'key = "prefix+m"' in res.new_text
    assert 'key = "prefix+shift+m"' in res.new_text
    # prefix+t NÃO deve entrar no bloco gerenciado por ser conflito
    assert 'key = "prefix+t"' not in res.new_text[res.new_text.find(START_MARKER):]


def test_merge_keys_block_replaces_existing_managed_block():
    old_managed = f"{START_MARKER}\n[[keys.command]]\nkey = \"prefix+old\"\ntype = \"shell\"\ncommand = \"meister old\"\n{END_MARKER}"
    existing = f"# header\n[section]\n\n{old_managed}\n\n# footer\n"
    desired = get_desired_bindings("/bin/meister")
    res = merge_keys_block(existing, desired)
    assert res.changed is True
    assert "prefix+old" not in res.new_text
    assert "# header\n[section]\n\n" in res.new_text
    assert "\n\n# footer\n" in res.new_text
    assert START_MARKER in res.new_text
    assert END_MARKER in res.new_text


def test_merge_keys_block_deterministic():
    desired = get_desired_bindings("/bin/meister", direct_keys=True)
    res1 = merge_keys_block(OWNER_FIXTURE, desired)
    res2 = merge_keys_block(OWNER_FIXTURE, desired)
    assert res1.new_text == res2.new_text

    empty1 = merge_keys_block("", desired)
    empty2 = merge_keys_block("", desired)
    assert empty1.new_text == empty2.new_text


def test_resolve_meister_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: "/opt/bin/meister" if cmd == "meister" else None)
    assert resolve_meister_path() == "/opt/bin/meister"


def test_resolve_meister_path_prefers_the_running_executable(monkeypatch, tmp_path):
    """Com várias instalações no PATH, vale o executável que rodou o comando (e o link não é resolvido)."""
    real = tmp_path / "repo" / "bin" / "meister"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n")
    link = tmp_path / "local" / "meister"
    link.parent.mkdir()
    link.symlink_to(real)
    monkeypatch.setattr(sys, "argv", [str(link), "setup"])
    monkeypatch.setattr(shutil, "which", lambda cmd: "/opt/outro/meister")
    assert resolve_meister_path() == str(link)


def test_resolve_meister_path_ignores_argv_that_is_not_meister(monkeypatch, tmp_path):
    other = tmp_path / "pytest"
    other.write_text("x")
    monkeypatch.setattr(sys, "argv", [str(other)])
    monkeypatch.setattr(shutil, "which", lambda cmd: "/opt/bin/meister")
    assert resolve_meister_path() == "/opt/bin/meister"


# ── Testes de escrita segura e idempotência ───────────────────────────────────

def test_write_herdr_config_creates_backup_and_writes_atomically(tmp_path):
    cfg_file = tmp_path / "config.toml"
    initial_content = "[general]\nkey = 'val'\n"
    cfg_file.write_text(initial_content, encoding="utf-8")

    desired = get_desired_bindings("/bin/meister")
    merge_res = merge_keys_block(initial_content, desired)

    commands_called = []
    def runner(cmd):
        commands_called.append(list(cmd))
        return FakeCompletedProcess(0, "ok", "")

    ok, msg = write_herdr_config(str(cfg_file), merge_res, runner=runner)
    assert ok is True
    assert cfg_file.read_text(encoding="utf-8") == merge_res.new_text

    # Verifica se o backup foi criado
    backups = list(tmp_path.glob("config.toml.meister-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == initial_content

    # Verifica comandos chamados
    assert ["herdr", "config", "check"] in commands_called
    assert ["herdr", "server", "reload-config"] in commands_called


def test_write_herdr_config_preserves_file_permissions(tmp_path):
    for mode in (0o644, 0o600):
        cfg_file = tmp_path / f"config{mode:o}.toml"
        cfg_file.write_text("[general]\n", encoding="utf-8")
        cfg_file.chmod(mode)
        merge_res = merge_keys_block("[general]\n", get_desired_bindings("/bin/meister"))
        ok, _ = write_herdr_config(str(cfg_file), merge_res, runner=lambda cmd: FakeCompletedProcess(0, "", ""))
        assert ok is True
        assert stat.S_IMODE(cfg_file.stat().st_mode) == mode


def test_write_herdr_config_idempotent_no_new_backup(tmp_path):
    cfg_file = tmp_path / "config.toml"
    initial = "[general]\n"
    cfg_file.write_text(initial, encoding="utf-8")

    desired = get_desired_bindings("/bin/meister")
    merge_res1 = merge_keys_block(initial, desired)

    commands_called = []
    def runner(cmd):
        commands_called.append(list(cmd))
        return FakeCompletedProcess(0, "ok", "")

    ok1, _ = write_herdr_config(str(cfg_file), merge_res1, runner=runner)
    assert ok1 is True
    backups1 = list(tmp_path.glob("config.toml.meister-backup-*"))
    assert len(backups1) == 1

    # 2ª execução com o mesmo arquivo já gravado
    content_after = cfg_file.read_text(encoding="utf-8")
    merge_res2 = merge_keys_block(content_after, desired)
    assert merge_res2.changed is False

    ok2, msg2 = write_herdr_config(str(cfg_file), merge_res2, runner=runner)
    assert ok2 is True
    assert msg2 == "sem mudanças"

    # Nenhum backup adicional foi gerado
    backups2 = list(tmp_path.glob("config.toml.meister-backup-*"))
    assert len(backups2) == 1


def test_write_herdr_config_check_fails_restores_original_byte_for_byte(tmp_path):
    cfg_file = tmp_path / "config.toml"
    initial_content = "[general]\nkey = 'original'\n"
    cfg_file.write_text(initial_content, encoding="utf-8")

    desired = get_desired_bindings("/bin/meister")
    merge_res = merge_keys_block(initial_content, desired)

    commands_called = []
    def runner(cmd):
        commands_called.append(list(cmd))
        if cmd == ["herdr", "config", "check"]:
            return FakeCompletedProcess(1, "", "syntax error on line 42")
        return FakeCompletedProcess(0, "ok", "")

    ok, msg = write_herdr_config(str(cfg_file), merge_res, runner=runner)
    assert ok is False
    assert "syntax error on line 42" in msg

    # Arquivo deve ter sido restaurado byte a byte
    assert cfg_file.read_text(encoding="utf-8") == initial_content

    # reload-config NUNCA deve ser chamado quando check falha
    assert ["herdr", "server", "reload-config"] not in commands_called


def test_write_herdr_config_reload_fails_is_warning_not_error(tmp_path):
    cfg_file = tmp_path / "config.toml"
    initial_content = "[general]\n"
    cfg_file.write_text(initial_content, encoding="utf-8")

    desired = get_desired_bindings("/bin/meister")
    merge_res = merge_keys_block(initial_content, desired)

    def runner(cmd):
        if cmd == ["herdr", "config", "check"]:
            return FakeCompletedProcess(0, "valid", "")
        if cmd == ["herdr", "server", "reload-config"]:
            return FakeCompletedProcess(1, "", "server not running")
        return FakeCompletedProcess(0, "", "")

    ok, msg = write_herdr_config(str(cfg_file), merge_res, runner=runner)
    assert ok is True
    assert "não pôde ser recarregado" in msg


# ── Testes de plugin e diagnóstico ────────────────────────────────────────────

def test_ensure_herdr_plugin_already_linked():
    called = []
    def runner(cmd):
        called.append(list(cmd))
        return FakeCompletedProcess(0, "dev.meisterrouter.orchestrator v0.9.0", "")

    status, msg = ensure_herdr_plugin(runner=runner)
    assert status == "ok"
    assert "já vinculado" in msg
    assert len(called) == 1
    assert called[0] == ["herdr", "plugin", "list"]


def test_ensure_herdr_plugin_links_when_absent(tmp_path):
    manifest = tmp_path / "herdr-plugin.toml"
    manifest.write_text('id = "dev.meisterrouter.orchestrator"\n', encoding="utf-8")

    called = []
    def runner(cmd):
        called.append(list(cmd))
        if cmd == ["herdr", "plugin", "list"]:
            return FakeCompletedProcess(0, "other-plugin v1.0", "")
        if cmd[:3] == ["herdr", "plugin", "link"]:
            return FakeCompletedProcess(0, "linked ok", "")
        return FakeCompletedProcess(0, "", "")

    status, msg = ensure_herdr_plugin(repo_root=tmp_path, runner=runner)
    assert status == "ok"
    assert "vinculado com sucesso" in msg
    assert ["herdr", "plugin", "link", str(tmp_path)] in called


def test_ensure_herdr_plugin_dry_run_never_executes_link(tmp_path):
    manifest = tmp_path / "herdr-plugin.toml"
    manifest.write_text('id = "dev.meisterrouter.orchestrator"\n', encoding="utf-8")

    called = []
    def runner(cmd):
        called.append(list(cmd))
        return FakeCompletedProcess(0, "", "")

    status, msg = ensure_herdr_plugin(repo_root=tmp_path, runner=runner, dry_run=True)
    assert status == "ok"
    assert "[simulação]" in msg
    for c in called:
        assert c != ["herdr", "plugin", "link", str(tmp_path)]


def test_check_python():
    item = check_python()
    assert item.status == "ok"
    assert "Python" in item.message


def test_check_herdr_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    item = check_herdr()
    assert item.status == "erro"
    assert "curl -fsSL https://herdr.dev/install.sh | sh" in item.message


def test_check_openrouter_key_secret_never_leaked(monkeypatch):
    secret = "sk-teste-SEGREDO_SUPER_CONFIDENCIAL_12345"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    item = check_openrouter_key()
    assert item.status == "ok"
    assert secret not in item.message
    assert "SEGREDO" not in item.message
    assert "OPENROUTER_API_KEY configurada" in item.message


def test_check_openrouter_key_modes(monkeypatch):
    import meister.jev
    def fake_get_api_key():
        raise ValueError("Chave ausente")
    monkeypatch.setattr(meister.jev, "get_api_key", fake_get_api_key)

    # router.mode: jev sem chave -> aviso
    cfg_jev = MeisterConfig(router=RouterConfig(mode="jev"))
    item_jev = check_openrouter_key(cfg_jev)
    assert item_jev.status == "aviso"
    assert "o Jev não funcionará; use router.mode: first" in item_jev.message

    # router.mode: first sem chave -> ok
    cfg_first = MeisterConfig(router=RouterConfig(mode="first"))
    item_first = check_openrouter_key(cfg_first)
    assert item_first.status == "ok"
    assert "Jev não é utilizado" in item_first.message


def test_check_worker_clis_disabled_tier_not_checked(monkeypatch):
    tier_enabled = WorkerTier(name="copilot_luna", harness="copilot", model="gpt-6-luna", cost_per_m_tokens=0.20)
    tier_disabled = WorkerTier(name="codex_luna", harness="codex", model="gpt-6-luna", cost_per_m_tokens=0.20, enabled=False)

    cfg = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[tier_enabled],
            disabled=[tier_disabled],
        )
    )

    # Simula que copilot existe e codex não existe
    def fake_which(harness):
        if harness == "copilot":
            return "/bin/copilot"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    items = check_worker_clis(cfg)

    # Somente copilot deve ter sido checado
    assert len(items) == 1
    assert items[0].name == "worker_cli"
    assert items[0].status == "ok"
    assert "copilot" in items[0].message
    # codex não deve aparecer nos diagnósticos
    assert not any("codex" in i.message for i in items)
