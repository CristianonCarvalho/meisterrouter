"""
meister.setup_cmd — Lógica de instalação e configuração do MeisterRouter com Herdr.

Módulo com lógica pura e testável:
- Geração e mesclagem determinística de atalhos em ~/.config/herdr/config.toml
- Bloco gerenciado delimitado por marcadores
- Escrita segura atômica com backup automático e validação via herdr config check
- Diagnóstico do ambiente (Python, Herdr, plugin, atalhos, daemon, workers, OpenRouter API key)
- Geração do template de configuração do projeto (meister config init)
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import subprocess
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from meister.i18n import t

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

START_MARKER = '# >>> meisterrouter: gerenciado por "meister setup" (não edite dentro do bloco) >>>'
END_MARKER = '# <<< meisterrouter <<<'


@dataclass
class KeyBinding:
    key: str
    type: str  # "shell" ou "popup"
    command: str
    description: Optional[str] = None
    width: Optional[str] = None
    height: Optional[str] = None


@dataclass
class MergeResult:
    new_text: str
    actions: dict[str, str]  # key -> "added" | "already" | "conflict"
    changed: bool
    details: dict[str, str] = field(default_factory=dict)

    @property
    def added(self) -> list[str]:
        return [k for k, v in self.actions.items() if v == "added"]

    @property
    def already(self) -> list[str]:
        return [k for k, v in self.actions.items() if v == "already"]

    @property
    def conflict(self) -> list[str]:
        return [k for k, v in self.actions.items() if v == "conflict"]


@dataclass
class DiagnosticItem:
    name: str
    status: str  # "ok", "aviso", "erro"
    message: str


def default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Executor padrão de comandos de subprocesso."""
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def resolve_meister_path() -> str:
    """Resolve o caminho absoluto do executável meister.

    Prefere o executável que está rodando o comando (sem resolver links simbólicos), porque pode haver
    várias instalações no PATH; sem ele, usa o primeiro `meister` do PATH.
    """
    if sys.argv and sys.argv[0] and os.path.basename(sys.argv[0]) == "meister":
        argv_path = os.path.abspath(sys.argv[0])
        if os.path.isfile(argv_path):
            return argv_path
    which_path = shutil.which("meister")
    if which_path:
        return os.path.abspath(which_path)
    return "meister"


def get_desired_bindings(meister_path: str, direct_keys: bool = False) -> list[KeyBinding]:
    """Retorna a lista de atalhos desejados para o Herdr."""
    bindings = [
        KeyBinding(
            key="prefix+m",
            type="shell",
            command=f"{meister_path} herdr-action orchestrate",
            description=t("commands.setup.binding_orchestrate"),
        ),
        KeyBinding(
            key="prefix+shift+m",
            type="popup",
            command=f"{meister_path} dashboard --tui",
            width="85%",
            height="85%",
            description=t("commands.setup.binding_dashboard"),
        ),
        KeyBinding(
            key="prefix+t",
            type="popup",
            command=f"{meister_path} timeline",
            width="85%",
            height="85%",
            description=t("commands.setup.binding_timeline"),
        ),
    ]

    if direct_keys:
        bindings.extend([
            KeyBinding(
                key="ctrl+alt+m",
                type="shell",
                command=f"{meister_path} herdr-action orchestrate",
                description=t("commands.setup.binding_orchestrate_direct"),
            ),
            KeyBinding(
                key="ctrl+alt+shift+m",
                type="popup",
                command=f"{meister_path} dashboard --tui",
                width="85%",
                height="85%",
                description=t("commands.setup.binding_dashboard_direct"),
            ),
            KeyBinding(
                key="ctrl+alt+t",
                type="popup",
                command=f"{meister_path} timeline",
                width="85%",
                height="85%",
                description=t("commands.setup.binding_timeline_direct"),
            ),
        ])

    return bindings


def render_binding(b: KeyBinding) -> str:
    """Renderiza uma entrada [[keys.command]] em formato TOML determinístico."""
    lines = [
        "[[keys.command]]",
        f'key = "{b.key}"',
        f'type = "{b.type}"',
        f'command = "{b.command}"',
    ]
    if b.width:
        lines.append(f'width = "{b.width}"')
    if b.height:
        lines.append(f'height = "{b.height}"')
    if b.description:
        lines.append(f'description = "{b.description}"')
    return "\n".join(lines)


def render_bindings_block(bindings: list[KeyBinding]) -> str:
    """Renderiza o bloco gerenciado completo com delimitadores."""
    if not bindings:
        return ""
    body = "\n\n".join(render_binding(b) for b in bindings)
    return f"{START_MARKER}\n{body}\n{END_MARKER}"


def render_keys_block(meister_path: str, direct_keys: bool = False) -> str:
    """Renderiza o bloco gerenciado de atalhos do MeisterRouter."""
    bindings = get_desired_bindings(meister_path, direct_keys=direct_keys)
    return render_bindings_block(bindings)


def merge_keys_block(existing_text: str, desired: list[KeyBinding]) -> MergeResult:
    """Mescla os atalhos desejados no texto TOML existente.

    Regras:
    - Atalho cuja key já está ligada fora do bloco a comando com 'meister': already
    - Atalho cuja key já está ligada fora do bloco a outro comando: conflict (não sobrescreve)
    - Demais atalhos: added (vão para o bloco gerenciado)
    - Bloco gerenciado existente é substituído pelo novo conteúdo
    - Todo conteúdo fora do bloco é preservado byte a byte
    - Se não há nada a adicionar e não existe bloco gerenciado, o texto não é alterado
    """
    had_block = False
    block_start = existing_text.find(START_MARKER)
    block_end = -1

    if block_start != -1:
        end_marker_pos = existing_text.find(END_MARKER, block_start + len(START_MARKER))
        if end_marker_pos != -1:
            had_block = True
            block_end = end_marker_pos + len(END_MARKER)

    if had_block:
        before_block = existing_text[:block_start]
        after_block = existing_text[block_end:]
        outside_text = before_block + after_block
    else:
        before_block = existing_text
        after_block = ""
        outside_text = existing_text

    # Parse do texto fora do bloco com tomllib
    existing_commands: dict[str, str] = {}
    if outside_text.strip():
        try:
            parsed = tomllib.loads(outside_text)
            keys_sec = parsed.get("keys")
            if isinstance(keys_sec, dict):
                cmds = keys_sec.get("command")
                if isinstance(cmds, list):
                    for item in cmds:
                        if isinstance(item, dict) and "key" in item:
                            k = str(item["key"])
                            cmd = str(item.get("command", ""))
                            existing_commands[k] = cmd
        except Exception:
            # Em caso de falha de parsing no texto externo, preserva sem colidir
            pass

    actions: dict[str, str] = {}
    details: dict[str, str] = {}
    added: list[KeyBinding] = []

    for b in desired:
        if b.key in existing_commands:
            cmd = existing_commands[b.key]
            if "meister" in cmd.lower():
                actions[b.key] = "already"
                details[b.key] = cmd
            else:
                actions[b.key] = "conflict"
                details[b.key] = cmd
        else:
            actions[b.key] = "added"
            added.append(b)

    new_block_str = render_bindings_block(added)

    if not had_block:
        if not added:
            # Nada a adicionar e não há bloco: inalterado
            return MergeResult(
                new_text=existing_text,
                actions=actions,
                changed=False,
                details=details,
            )

        # Adiciona o bloco ao fim do arquivo com uma linha em branco antes
        if not existing_text:
            new_text = new_block_str + "\n"
        elif existing_text.endswith("\n\n"):
            new_text = existing_text + new_block_str + "\n"
        elif existing_text.endswith("\n"):
            new_text = existing_text + "\n" + new_block_str + "\n"
        else:
            new_text = existing_text + "\n\n" + new_block_str + "\n"
    else:
        # Substitui o bloco existente
        if added:
            new_text = before_block + new_block_str + after_block
        else:
            # Remove o bloco existente
            cleaned_before = before_block
            if cleaned_before.endswith("\n\n"):
                cleaned_before = cleaned_before[:-1]
            new_text = cleaned_before + after_block.lstrip("\n")

    changed = new_text != existing_text

    return MergeResult(
        new_text=new_text,
        actions=actions,
        changed=changed,
        details=details,
    )


def write_herdr_config(
    config_path: str,
    merge_result: MergeResult,
    runner: Optional[Callable[[list[str]], Any]] = None,
) -> tuple[bool, str]:
    """Grava as alterações de forma segura e atômica no arquivo de configuração do Herdr."""
    if runner is None:
        runner = default_runner

    if not merge_result.changed:
        return True, t("commands.setup.no_changes")

    parent_dir = os.path.dirname(os.path.abspath(config_path))
    os.makedirs(parent_dir, exist_ok=True)

    original_existed = os.path.exists(config_path)
    original_bytes = b""
    if original_existed:
        with open(config_path, "rb") as f:
            original_bytes = f.read()

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{config_path}.meister-backup-{timestamp}"
        with open(backup_path, "wb") as f:
            f.write(original_bytes)

    temp_file = tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8")
    try:
        temp_file.write(merge_result.new_text)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        if original_existed:
            shutil.copymode(config_path, temp_file.name)  # o arquivo temporário nasce 0600
        os.replace(temp_file.name, config_path)
    except Exception as e:
        if os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except Exception:
                pass
        return False, t("commands.setup.atomic_write_failed", error=e)

    # Validação com herdr config check
    check_proc = runner(["herdr", "config", "check"])
    check_rc = getattr(check_proc, "returncode", 1)
    if check_rc != 0:
        check_msg = (
            getattr(check_proc, "stderr", "")
            or getattr(check_proc, "stdout", "")
            or t("commands.setup.config_check_default")
        ).strip()
        if original_existed:
            with open(config_path, "wb") as f:
                f.write(original_bytes)
        else:
            if os.path.exists(config_path):
                os.remove(config_path)
        return False, t("commands.setup.config_check_failed", message=check_msg)

    # Recarrega o servidor Herdr
    reload_proc = runner(["herdr", "server", "reload-config"])
    reload_rc = getattr(reload_proc, "returncode", 0)
    if reload_rc != 0:
        reload_msg = (
            getattr(reload_proc, "stderr", "") or getattr(reload_proc, "stdout", "") or ""
        ).strip()
        return True, t("commands.setup.reload_warning", message=reload_msg)

    return True, t("commands.setup.config_reloaded")


def ensure_herdr_plugin(
    repo_root: Optional[Path] = None,
    runner: Optional[Callable[[list[str]], Any]] = None,
    dry_run: bool = False,
) -> tuple[str, str]:
    """Verifica e vincula o plugin do MeisterRouter no Herdr."""
    if runner is None:
        runner = default_runner

    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent

    manifest_path = repo_root / "herdr-plugin.toml"

    list_proc = runner(["herdr", "plugin", "list"])
    list_stdout = getattr(list_proc, "stdout", "") or ""
    if "dev.meisterrouter.orchestrator" in list_stdout:
        return "ok", t("commands.setup.plugin_linked")

    if dry_run:
        if manifest_path.exists():
            return "ok", t("commands.setup.plugin_would_link", path=repo_root)
        return "aviso", t("commands.setup.plugin_manifest_missing")

    if not manifest_path.exists():
        return "aviso", t("commands.setup.plugin_manifest_missing_clone")

    link_proc = runner(["herdr", "plugin", "link", str(repo_root)])
    link_rc = getattr(link_proc, "returncode", 1)
    if link_rc == 0:
        return "ok", t("commands.setup.plugin_link_success")

    err_msg = (getattr(link_proc, "stderr", "") or getattr(link_proc, "stdout", "") or "").strip()
    return "erro", t("commands.setup.plugin_link_failed", error=err_msg)


# ── Diagnósticos ──────────────────────────────────────────────────────────────

def is_pid_alive(pid: int) -> bool:
    """Verifica se um processo com o PID fornecido está ativo."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_default_pid_file() -> str:
    """Retorna o caminho padrão do arquivo PID do daemon."""
    env_pid = os.environ.get("MEISTER_PID_FILE")
    if env_pid:
        return env_pid
    return os.path.expanduser("~/.meister/daemon.pid")


def check_python() -> DiagnosticItem:
    v = sys.version_info
    if v >= (3, 10):
        version = f"{v.major}.{v.minor}.{v.micro}"
        return DiagnosticItem("python", "ok", t("commands.setup.python_compatible", version=version))
    version = f"{v.major}.{v.minor}.{v.micro}"
    return DiagnosticItem("python", "erro", t("commands.setup.python_incompatible", version=version))


def check_herdr() -> DiagnosticItem:
    path = shutil.which("herdr")
    if path:
        return DiagnosticItem("herdr", "ok", t("commands.setup.herdr_found", path=path))
    return DiagnosticItem(
        "herdr",
        "erro",
        t("commands.setup.herdr_missing"),
    )


def check_daemon() -> DiagnosticItem:
    pid_file = get_default_pid_file()
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            if is_pid_alive(pid):
                return DiagnosticItem("daemon", "ok", t("commands.setup.daemon_active", pid=pid))
        except Exception:
            pass
    return DiagnosticItem("daemon", "aviso", t("commands.setup.daemon_inactive"))


def check_worker_clis(cfg: Any = None) -> list[DiagnosticItem]:
    """Verifica CLIs de workers apenas para vias habilitadas (tier_order)."""
    if cfg is None:
        try:
            from meister.config import load_config
            cfg = load_config()
        except Exception:
            return []

    try:
        from meister.worker import find_cli_binary
    except ImportError:
        def find_cli_binary(harness: str) -> Optional[str]:
            return shutil.which(harness)

    items: list[DiagnosticItem] = []
    # Apenas vias habilitadas em tier_order são checadas; vias desabilitadas são ignoradas
    for tier in cfg.workers.tier_order:
        bin_path = find_cli_binary(tier.harness)
        if bin_path:
            items.append(
                DiagnosticItem(
                    "worker_cli",
                    "ok",
                    t(
                        "commands.setup.worker_found",
                        harness=tier.harness,
                        tier=tier.name,
                        path=bin_path,
                    ),
                )
            )
        else:
            items.append(
                DiagnosticItem(
                    "worker_cli",
                    "aviso",
                    t("commands.setup.worker_missing", harness=tier.harness, tier=tier.name),
                )
            )
    return items


def check_openrouter_key(cfg: Any = None) -> DiagnosticItem:
    """Verifica disponibilidade da chave OpenRouter sem expor o valor."""
    if cfg is None:
        try:
            from meister.config import load_config
            cfg = load_config()
        except Exception:
            cfg = None

    mode = cfg.router.mode if cfg else "jev"

    has_key = False
    try:
        from meister.jev import get_api_key
        key = get_api_key()
        has_key = bool(key)
    except Exception:
        has_key = False

    if has_key:
        return DiagnosticItem("openrouter_key", "ok", t("commands.setup.openrouter_configured"))
    if mode == "first":
        return DiagnosticItem(
            "openrouter_key",
            "ok",
            t("commands.setup.openrouter_not_needed"),
        )
    return DiagnosticItem(
        "openrouter_key",
        "aviso",
        t("commands.setup.openrouter_missing"),
    )


def check_config_validity(cfg: Any = None) -> list[DiagnosticItem]:
    """Valida a configuração ativa com validate_config."""
    try:
        from meister.config import load_config, validate_config
        if cfg is None:
            cfg = load_config()
        source = cfg.config_source
        issues = validate_config(cfg)
        errors = [i for i in issues if i.level == "error"]
        warnings = [i for i in issues if i.level == "warning"]

        items: list[DiagnosticItem] = []
        if errors:
            for err in errors:
                items.append(
                    DiagnosticItem(
                        "config",
                        "erro",
                        t("commands.setup.config_error", source=source, path=err.path, message=err.message),
                    )
                )
        elif warnings:
            for w in warnings:
                items.append(
                    DiagnosticItem(
                        "config",
                        "aviso",
                        t("commands.setup.config_warning", source=source, path=w.path, message=w.message),
                    )
                )
        else:
            items.append(DiagnosticItem("config", "ok", t("commands.setup.config_valid", source=source)))
        return items
    except Exception as e:
        return [DiagnosticItem("config", "erro", t("commands.setup.config_load_failed", error=e))]


# ── meister config init ────────────────────────────────────────────────────────

def generate_config_yaml_content() -> str:
    """Gera o conteúdo de meister.config.yaml com apenas router e workers.tier_order."""
    return t("commands.setup.config_template")


# ── Execução de meister setup ─────────────────────────────────────────────────

def run_setup(
    dry_run: bool = False,
    direct_keys: bool = False,
    herdr_config_path: Optional[str] = None,
    project_path: Optional[str] = None,
    runner: Optional[Callable[[Sequence[str]], Any]] = None,
    echo: Optional[Callable[[str], None]] = None,
    init_project_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """Executa a lógica de instalação e diagnóstico do meister setup."""
    if runner is None:
        runner = default_runner
    if echo is None:
        echo = print

    # 1. Diagnóstico inicial; herdr ausente -> imprime orientação e sai com 1
    herdr_diag = check_herdr()
    if herdr_diag.status == "erro":
        echo(f"✗ {herdr_diag.message}")
        return 1

    # 2. Plugin
    plugin_status, plugin_msg = ensure_herdr_plugin(runner=runner, dry_run=dry_run)

    # 3. Atalhos
    cfg_path = os.path.abspath(herdr_config_path or os.path.expanduser("~/.config/herdr/config.toml"))
    existing_text = ""
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                existing_text = f.read()
        except Exception as e:
            echo(t("commands.setup.read_warning", path=cfg_path, error=e))

    meister_path = resolve_meister_path()
    desired = get_desired_bindings(meister_path, direct_keys=direct_keys)
    merge_result = merge_keys_block(existing_text, desired)

    shortcuts_status = "ok"
    shortcuts_msg = t(
        "commands.setup.shortcuts_configured",
        keys=", ".join(b.key for b in desired),
    )

    if dry_run:
        echo(t("commands.setup.dry_run_shortcuts", path=cfg_path))
        for b in desired:
            action = merge_result.actions.get(b.key, "added")
            if action == "already":
                echo(
                    t(
                        "commands.setup.shortcut_already",
                        shortcut=b.key,
                        command=merge_result.details.get(b.key, ""),
                    )
                )
            elif action == "conflict":
                echo(
                    t(
                        "commands.setup.shortcut_conflict",
                        shortcut=b.key,
                        command=merge_result.details.get(b.key, ""),
                    )
                )
            else:
                echo(t("commands.setup.shortcut_would_add", shortcut=b.key))

        if merge_result.changed:
            added_bindings = [b for b in desired if merge_result.actions.get(b.key) == "added"]
            echo(t("commands.setup.block_would_write"))
            echo(render_bindings_block(added_bindings))
        else:
            echo(t("commands.setup.no_shortcut_changes"))

        if merge_result.conflict:
            shortcuts_status = "aviso"
            shortcuts_msg = t("commands.setup.shortcut_conflicts", keys=", ".join(merge_result.conflict))
    else:
        if merge_result.conflict:
            shortcuts_status = "aviso"
            shortcuts_msg = t("commands.setup.shortcut_conflicts", keys=", ".join(merge_result.conflict))

        if merge_result.changed:
            write_ok, write_msg = write_herdr_config(cfg_path, merge_result, runner=runner)
            if not write_ok:
                shortcuts_status = "erro"
                shortcuts_msg = write_msg
            else:
                if shortcuts_status != "aviso":
                    shortcuts_msg = t(
                        "commands.setup.shortcuts_updated",
                        keys=", ".join(
                            b.key for b in desired if merge_result.actions.get(b.key) == "added"
                        ),
                    )

    # 4. Projeto
    if project_path is not None:
        if dry_run:
            echo(t("commands.setup.dry_run_project", path=project_path))
        else:
            if init_project_fn is not None:
                init_project_fn(project_path)

    # 5. Diagnósticos
    report_items: list[tuple[str, str]] = []

    # Python
    py_item = check_python()
    sym_py = "✓" if py_item.status == "ok" else ("!" if py_item.status == "aviso" else "✗")
    report_items.append((sym_py, py_item.message))

    # Herdr
    report_items.append(("✓", herdr_diag.message))

    # Plugin
    sym_plugin = "✓" if plugin_status == "ok" else ("!" if plugin_status == "aviso" else "✗")
    report_items.append((sym_plugin, plugin_msg))

    # Atalhos
    sym_sc = "✓" if shortcuts_status == "ok" else ("!" if shortcuts_status == "aviso" else "✗")
    report_items.append((sym_sc, shortcuts_msg))

    # Daemon
    daemon_item = check_daemon()
    sym_daemon = "✓" if daemon_item.status == "ok" else ("!" if daemon_item.status == "aviso" else "✗")
    report_items.append((sym_daemon, daemon_item.message))

    # Worker CLIs
    for w_item in check_worker_clis():
        sym_w = "✓" if w_item.status == "ok" else ("!" if w_item.status == "aviso" else "✗")
        report_items.append((sym_w, w_item.message))

    # OpenRouter
    openrouter_item = check_openrouter_key()
    sym_or = "✓" if openrouter_item.status == "ok" else ("!" if openrouter_item.status == "aviso" else "✗")
    report_items.append((sym_or, openrouter_item.message))

    # Config
    for c_item in check_config_validity():
        sym_c = "✓" if c_item.status == "ok" else ("!" if c_item.status == "aviso" else "✗")
        report_items.append((sym_c, c_item.message))

    # 6. Relatório final em português
    echo("\n" + "=" * 80)
    echo(t("commands.setup.report_title"))
    echo("=" * 80)
    for sym, msg in report_items:
        echo(f"{sym} {msg}")
    echo("=" * 80 + "\n")

    keys_summary = "prefix+m, prefix+shift+m, prefix+t"
    if direct_keys:
        keys_summary += ", ctrl+alt+m, ctrl+alt+shift+m, ctrl+alt+t"

    echo(t("commands.setup.next_steps"))
    echo(t("commands.setup.open_herdr"))
    echo(t("commands.setup.run_plan"))
    echo(t("commands.setup.shortcuts_summary", keys=keys_summary))

    if project_path is None:
        echo(t("commands.setup.project_tip"))

    has_error = any(sym == "✗" for sym, _ in report_items)
    return 1 if has_error else 0
