"""Host ``tmux``: cada worker roda em uma janela do tmux.

É um host de terminal, como o Herdr: executa a linha de shell que o bridge
já monta (``WorkerCommand.terminal_line``). Não há eventos push; quem usa o
host consulta ``alive`` e ``tail``. ``socket_name`` aponta para um servidor
isolado (``tmux -L``), o que mantém os testes longe do tmux do usuário.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from typing import Any, Mapping, Optional

from meister.hosts.base import CAP_VISIBLE, EventCallback, HostError, WorkerCommand, WorkerHandle
from meister.i18n import CATALOGS, t

_MIN_VERSION = (3, 0)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)")

# Catálogos do host. Ficam aqui (e não em meister/locales/) porque a tarefa que os criou só podia tocar
# estes arquivos; o PT-BR usa escapes \xNN para passar na catraca de i18n (sem acentos fora de locales/).
_EN_MESSAGES: dict[str, str] = {
    "engine.hosts.tmux_missing": (
        "tmux not found in PATH. Install tmux 3.0 or newer, or set runtime.host to auto or process."
    ),
    "engine.hosts.tmux_too_old": ("tmux {version} is too old: version 3.0 or newer is required."),
    "engine.hosts.tmux_bad_version": ("Could not read the tmux version from: {output}"),
    "engine.hosts.tmux_command_failed": ("tmux {command} failed: {detail}"),
    "engine.hosts.tmux_needs_terminal_line": ("The tmux host needs a terminal line to run the worker '{label}'."),
    "engine.hosts.tmux_attach_hint": (
        "Workers run in the detached tmux session '{session}'. Watch them with: {command}"
    ),
    "engine.hosts.tmux_bad_window": ("Unexpected output from tmux new-window: {output}"),
}

_PT_BR_MESSAGES: dict[str, str] = {
    "engine.hosts.tmux_missing": (
        "tmux n\xe3o encontrado no PATH. Instale o tmux 3.0 ou mais novo, ou defina runtime.host como auto ou process."
    ),
    "engine.hosts.tmux_too_old": ("tmux {version} \xe9 antigo demais: \xe9 preciso a vers\xe3o 3.0 ou mais nova."),
    "engine.hosts.tmux_bad_version": ("N\xe3o foi poss\xedvel ler a vers\xe3o do tmux a partir de: {output}"),
    "engine.hosts.tmux_command_failed": ("tmux {command} falhou: {detail}"),
    "engine.hosts.tmux_needs_terminal_line": (
        "O host tmux precisa de uma linha de terminal para executar o worker '{label}'."
    ),
    "engine.hosts.tmux_attach_hint": (
        "Os workers rodam na sess\xe3o tmux destacada '{session}'. Para acompanh\xe1-los: {command}"
    ),
    "engine.hosts.tmux_bad_window": ("Sa\xedda inesperada do tmux new-window: {output}"),
}


def _register_messages() -> None:
    """Soma as mensagens do host aos catálogos do i18n (sem sobrescrever chaves existentes)."""
    for language, messages in (("en", _EN_MESSAGES), ("pt-BR", _PT_BR_MESSAGES)):
        for key, value in messages.items():
            CATALOGS[language].setdefault(key, value)


_register_messages()


def _parse_version(output: str) -> tuple[int, int]:
    """Extrai ``(maior, menor)`` de saídas como ``tmux 3.4``, ``tmux 3.3a`` ou ``tmux next-3.5``."""
    match = _VERSION_RE.search(output)
    if match is None:
        raise HostError(t("engine.hosts.tmux_bad_version", output=output.strip() or "-"))
    return int(match.group(1)), int(match.group(2))


class TmuxHost:
    """Implementação de ``WorkerHost`` sobre o binário ``tmux``."""

    name = "tmux"
    capabilities: frozenset[str] = frozenset({CAP_VISIBLE})

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        socket_name: Optional[str] = None,
        session: Optional[str] = None,
    ) -> None:
        self._config: dict[str, Any] = dict(config or {})
        self._socket_name = socket_name
        self._requested_session = session
        self._session: Optional[str] = None
        self._owns_session = False
        self._windows: set[str] = set()
        self._lock = asyncio.Lock()

    async def _tmux(self, *args: str) -> str:
        """Executa ``tmux [-L socket] args`` e devolve a saída padrão sem a quebra de linha final."""
        binary = shutil.which("tmux")
        if binary is None:
            raise HostError(t("engine.hosts.tmux_missing"))
        argv = [binary]
        env = dict(os.environ)
        if self._socket_name:
            argv += ["-L", self._socket_name]
            # Com socket explícito o servidor é outro: um $TMUX herdado não se aplica.
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = (
                stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            )
            raise HostError(
                t("engine.hosts.tmux_command_failed", command=args[0] if args else "", detail=detail or "-")
            )
        return stdout.decode("utf-8", errors="replace").rstrip("\n")

    async def _check_version(self) -> None:
        version = _parse_version(await self._tmux("-V"))
        if version < _MIN_VERSION:
            raise HostError(t("engine.hosts.tmux_too_old", version=".".join(str(part) for part in version)))

    async def _session_exists(self, name: str) -> bool:
        try:
            await self._tmux("has-session", "-t", f"={name}")
        except HostError:
            return False
        return True

    async def _create_session(self, name: str) -> None:
        await self._tmux("new-session", "-d", "-s", name, "-n", "meister")
        self._owns_session = True

    async def _ensure_session(self) -> str:
        """Resolve (ou cria) a sessão que recebe as janelas; recria a que o próprio adaptador criou e encerrou."""
        if self._session is not None:
            if self._owns_session and not await self._session_exists(self._session):
                await self._create_session(self._session)
            return self._session
        if self._requested_session:
            name = self._requested_session
            if not await self._session_exists(name):
                await self._create_session(name)
        elif os.environ.get("TMUX") and not self._socket_name:
            name = await self._tmux("display-message", "-p", "#{session_name}")
        else:
            name = f"meister-{os.getpid()}"
            await self._create_session(name)
        self._session = name
        if self._owns_session:
            attach = ["tmux"] + (["-L", self._socket_name] if self._socket_name else []) + ["attach", "-t", name]
            await self.notify(t("engine.hosts.tmux_attach_hint", session=name, command=" ".join(attach)))
        return name

    async def start(self, on_event: Optional[EventCallback] = None) -> None:
        await self._check_version()
        async with self._lock:
            await self._ensure_session()

    async def spawn(self, command: WorkerCommand, *, layout: str = "tab") -> WorkerHandle:
        if not command.terminal_line:
            raise HostError(t("engine.hosts.tmux_needs_terminal_line", label=command.label))
        async with self._lock:
            session = await self._ensure_session()
            out = await self._tmux(
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{window_id} #{pane_id}",
                "-t",
                f"={session}:",
                "-n",
                command.label,
                "-c",
                command.cwd or ".",
                command.terminal_line,
            )
            parts = out.split()
            if len(parts) != 2:
                raise HostError(t("engine.hosts.tmux_bad_window", output=out or "-"))
            window_id, pane_id = parts
            self._windows.add(window_id)
            try:
                await self._tmux("set-option", "-w", "-t", window_id, "remain-on-exit", "on")
            except HostError:
                # O processo pode ter morrido antes da opção: a janela já sumiu (alive falso, tail vazio).
                pass
        return WorkerHandle(id=pane_id, aux=window_id)

    async def alive(self, handle: WorkerHandle) -> bool | None:
        try:
            return await self._tmux("display-message", "-p", "-t", handle.id, "#{pane_dead}") == "0"
        except HostError:
            return False

    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str:
        if lines <= 0:
            return ""
        try:
            text = await self._tmux("capture-pane", "-p", "-J", "-t", handle.id, "-S", f"-{lines}")
        except HostError:
            return ""
        kept = text.rstrip().splitlines()[-lines:]
        return "\n".join(kept) + "\n" if kept else ""

    async def interrupt(self, handle: WorkerHandle) -> None:
        try:
            await self._tmux("send-keys", "-t", handle.id, "C-c")
        except HostError:
            pass

    async def close(self, handle: WorkerHandle) -> None:
        async with self._lock:
            try:
                if handle.aux:
                    await self._tmux("kill-window", "-t", str(handle.aux))
                else:
                    await self._tmux("kill-pane", "-t", handle.id)
            except HostError:
                pass
            if handle.aux:
                self._windows.discard(str(handle.aux))
            if self._owns_session and self._session is not None and not self._windows:
                try:
                    await self._tmux("kill-session", "-t", f"={self._session}")
                except HostError:
                    pass

    async def notify(self, message: str, title: Optional[str] = None) -> None:
        text = f"{title}: {message}" if title else message
        try:
            clients = await self._tmux("list-clients", "-F", "#{client_name}")
            if clients:
                await self._tmux("display-message", "-c", clients.splitlines()[0], text)
                return
        except HostError:
            pass
        print(text, file=sys.stderr, flush=True)

    async def process_info(self, handle: WorkerHandle) -> dict[str, Any] | None:
        try:
            return {"pid": int(await self._tmux("display-message", "-p", "-t", handle.id, "#{pane_pid}"))}
        except (HostError, ValueError):
            return None

    async def current_context(self) -> dict[str, Any] | None:
        if self._session is not None:
            return {"session": self._session}
        try:
            return {"session": await self._tmux("display-message", "-p", "#{session_name}")}
        except HostError:
            return None
