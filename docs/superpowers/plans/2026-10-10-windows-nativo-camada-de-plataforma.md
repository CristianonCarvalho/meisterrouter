# Windows nativo: camada de plataforma `osops` Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois de:** `2026-10-10-windows-base-de-testes.md` (infraestrutura de teste, já na `main`) e `2026-10-10-hosts-fase2-adaptador-process.md` (cria `meister/hosts/_proc.py`, que a Tarefa 1 passa a delegar à camada nova). Origem: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md` (seções 8 e 10) e a leitura do job informativo do Windows no CI (`51 failed, 62 errors` numa sessão interrompida).
>
> **Hot files:** `meister/worker.py` (Tarefas 2 e 3), `meister/cli.py` (2 e 4), `meister/herdr/bridge.py` (6). Não rodar junto de outro plano que mexa neles.
>
> **Depois do plano, passo manual do dono:** quando o job do Windows ficar verde de forma estável no CI, tirar `continue-on-error` dele em `.github/workflows/ci.yml` (hoje é informativo).

**Goal:** Fazer o Meister funcionar de verdade no Windows nativo, com o host `process`, sem WSL: isolar tudo que é específico de sistema operacional atrás de `meister/osops/` (uma implementação POSIX, que é o código de hoje movido sem mudança de comportamento, e uma de Windows) e corrigir os pontos que hoje quebram ou, pior, fazem algo perigoso no Windows.

**Architecture:** `meister/osops/` expõe um conjunto pequeno de operações: `pid_alive`, `process_signature`, `popen_session_kwargs`, `interrupt_group`, `terminate_tree`, `kill_tree`, `lock_file`/`unlock_file`, `list_processes` e `kill_self`. `posix.py` guarda o que já existe em `worker.py`, `cli.py`, `clean.py` e `faults.py`. `windows.py` usa `psutil` (dependência instalada **apenas** no Windows) e `msvcrt`. O resto do código chama `osops`, nunca `os.killpg`, `fcntl` ou `ps` diretamente. Linux e macOS não mudam.

**Tech Stack:** Python ≥ 3.10, `psutil>=5.9` (marcador `sys_platform == "win32"`), `msvcrt` (biblioteca padrão do Windows), `pytest`.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Achado que dá prioridade à Tarefa 2 | `os.kill(pid, 0)` aparece em `cli.py:123`, `worktree.py:1168`, `worker.py:233`, `setup_cmd.py:402` e `dashboard/state.py:128` para "o processo existe?". No Windows `signal.CTRL_C_EVENT` vale `0`, então essa chamada envia Ctrl+C ao grupo de processos do pid, e se o pid for o do próprio pytest o resultado é um `KeyboardInterrupt` que derruba a sessão (é a hipótese mais forte para a interrupção vista no CI do Windows; a confirmar). A Tarefa 2 é pequena e independente do resto, e pode ser feita antes das demais. |
| `psutil` | Só no Windows (`psutil>=5.9; sys_platform == "win32"` em `pyproject.toml` e `requirements.txt`). No Linux e no macOS o caminho é o código atual, para não arriscar regressão. |
| Hooks do Claude Code | Continuam `bash .claude/hooks/*.sh`: o Claude Code no Windows já exige o Git for Windows, que traz `bash`. Correções mínimas apenas (Tarefa 5): gravar os scripts com final de linha LF e tolerar a ausência de `chmod`. Reescrever os hooks em Python (`meister hook ...`) fica fora deste plano. |
| Interrupção no Windows | `interrupt_group` envia `CTRL_BREAK_EVENT` ao grupo (o processo é criado com `CREATE_NEW_PROCESS_GROUP`); se não houver efeito em 2 s, `terminate_tree`. |
| Harness `.cmd` | Os CLIs instalados por `npm` no Windows são atalhos `.cmd`, e o prompt segue como argumento de linha de comando. Aspas, `%`, `&` e quebras de linha podem ser corrompidos pelo `cmd.exe`. A Tarefa 9 só **mede** isso; a mitigação depende do resultado e fica para um plano próprio. |
| Plataformas | Linux, macOS e Windows 10/11 com Git for Windows. Windows continua marcado como informativo no CI até o passo manual acima. |

## Global Constraints

- **Linux e macOS: nenhum comportamento muda.** `posix.py` recebe o código existente movido; os testes atuais (matriz de falhas, órfãos, daemon, limpeza) passam sem mudar asserções.
- Nenhum chamador fora de `meister/osops/` usa `os.killpg`, `os.getpgid`, `fcntl`, `signal.SIGKILL`/`SIGHUP` diretamente, nem `os.kill(pid, 0)`, nem o comando `ps`. A Tarefa 2 inclui um teste que varre o código-fonte e falha se alguma dessas formas reaparecer.
- Sem dependência nova fora do Windows; compatível com Python 3.10; `from __future__ import annotations` nos módulos novos.
- Mensagens por `t()` com entradas em todos os catálogos de `meister/locales/`; `tests/test_i18n_ratchet.py`, `tests/test_locales_parity.py` e `tests/test_no_hardcoded_models.py` verdes.
- `ruff check .` e `mypy meister` limpos no Linux; a suíte inteira verde ao fim de **cada** tarefa.
- Testes específicos do Windows usam `@pytest.mark.skipif(sys.platform != "win32", ...)`; os de POSIX, `posix_only` de `tests/platform_marks.py`.
- Processos de teste sempre encerrados no `finally`.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Pacote `osops` e a implementação POSIX

**Files:**
- Create: `meister/osops/__init__.py`
- Create: `meister/osops/posix.py`
- Modify: `meister/hosts/_proc.py`
- Test: `tests/test_osops_posix.py`

**Depends on:** none

`__init__.py` escolhe o módulo por `sys.platform` (`windows` se `win32`, senão `posix`) e reexporta as funções: `pid_alive(pid) -> bool`, `process_signature(pid) -> str | None`, `popen_session_kwargs() -> dict` (POSIX: `{"start_new_session": True}`), `interrupt_group(pid) -> None`, `terminate_tree(pid, grace=5.0) -> None`, `kill_tree(pgid_or_pid, *, is_pgid=True) -> None`, `lock_file(fd, *, blocking=False) -> bool`, `unlock_file(fd) -> None`, `list_processes() -> list[tuple[int, list[str]]]` e `kill_self() -> NoReturn`. `posix.py` implementa cada uma com **o mesmo código de hoje**, movido sem alteração de lógica: `pid_alive` = `os.kill(pid, 0)` (agora o único lugar permitido); `process_signature` e `kill_tree` vêm de `meister/worker.py` (`process_start_signature`, `kill_process_tree`); `lock_file` vem do `fcntl.flock` de `cli.py`; `list_processes` do `ps -axo pid=,command=` de `meister/clean.py` (~136–150); `kill_self` de `meister/faults.py` (`SIGKILL` em si mesmo). Em `meister/hosts/_proc.py` (fase 2), `terminate_tree`, `process_signature` e `kill_tree` passam a delegar a `meister.osops`. `worker.py`, `cli.py`, `clean.py` e `faults.py` **ainda não** são alterados nesta tarefa (as próximas fazem isso); os helpers de `worker.py` viram apenas reexportações finas de `osops` para os testes existentes continuarem importando de lá. Testes: cada função com processos reais curtos, e a matriz de importação (`meister.osops` carrega sem `psutil` instalado no Linux).

### Task 2: "O processo existe?" sem `os.kill(pid, 0)`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/worktree.py`
- Modify: `meister/worker.py`
- Modify: `meister/setup_cmd.py`
- Modify: `meister/dashboard/state.py`
- Test: `tests/test_no_raw_posix_calls.py`

**Depends on:** Task 1

Troque os cinco usos de `os.kill(pid, 0)` (`cli.py:123`, `worktree.py:1168`, `worker.py:233`, `setup_cmd.py:402`, `dashboard/state.py:128`) por `osops.pid_alive(pid)`, preservando o tratamento de `ProcessLookupError`/`PermissionError` que cada um já tem (no POSIX, `PermissionError` significa "existe"). `tests/test_no_raw_posix_calls.py` varre `meister/**/*.py` (exceto `meister/osops/`) com `ast` e falha se encontrar chamada a `os.kill(_, 0)`; as demais formas proibidas (`os.killpg`, `os.getpgid`, `import fcntl`) entram na lista do teste **apenas depois** de as Tarefas 3 a 6 as removerem (nesta tarefa o teste cobre só `os.kill(_, 0)`; as tarefas seguintes ampliam a lista).

### Task 3: Controle de processos do worker

**Files:**
- Modify: `meister/worker.py`
- Test: `tests/test_worker_orphan.py`
- Test: `tests/test_no_raw_posix_calls.py`

**Depends on:** Task 2

Em `meister/worker.py`: `kill_process_tree` e `process_start_signature` passam a chamar `osops` diretamente (a definição local some; mantenha os nomes públicos como funções finas que delegam, porque os testes e `meister/hosts/` os importam); o `subprocess.Popen` do harness (~630) usa `**osops.popen_session_kwargs()` no lugar de `start_new_session=True`; `install_termination_handlers` (~266) registra `SIGHUP` somente se `hasattr(signal, "SIGHUP")` e usa `SIGBREAK` no Windows; remova as menções diretas a `os.killpg` e `signal.SIGKILL`. Amplie `tests/test_no_raw_posix_calls.py` para proibir `os.killpg`, `os.getpgid` e `signal.SIGKILL` fora de `meister/osops/` **e dos arquivos que as próximas tarefas ainda não migraram** (lista explícita e decrescente de exceções no teste). Os testes de órfãos continuam passando sem mudar asserções.

### Task 4: Daemon sem `fcntl`

**Files:**
- Modify: `meister/cli.py`
- Test: `tests/test_cli_herdr.py`
- Test: `tests/test_no_raw_posix_calls.py`

**Depends on:** Task 3

No comando `daemon` (`cli.py` ~1153, 1174–1255): `import fcntl` e as quatro chamadas `fcntl.flock` viram `osops.lock_file`/`unlock_file`; `os.kill(pid, signal.SIGTERM)` (~1153) vira `osops.terminate_tree(pid)`; a verificação `128 + int(signal.SIGHUP)` (~1514) usa `getattr(signal, "SIGHUP", None)` e ignora a comparação quando não existe. O comportamento no POSIX é idêntico (mesmos sinais, mesmo bloqueio não bloqueante). Ajuste a lista de exceções do teste de varredura removendo `meister/cli.py` e acrescentando `import fcntl` à lista proibida.

### Task 5: Limpeza, falhas injetadas, janela do dashboard e hooks

**Files:**
- Modify: `meister/clean.py`
- Modify: `meister/faults.py`
- Modify: `meister/dashboard/launcher.py`
- Modify: `meister/hooks.py`
- Test: `tests/test_hooks.py`

**Depends on:** Task 1

`clean.py`: o processo de varredura (`ps -axo`, ~136–150) vira `osops.list_processes()`; o restante da lógica (filtrar por linha de comando) não muda. `faults.py`: `os.kill(os.getpid(), signal.SIGKILL)` (~78) vira `osops.kill_self()`. `dashboard/launcher.py`: os dois `start_new_session=True` (~282 e ~334) viram `**osops.popen_session_kwargs()`. `hooks.py`: `_write_executable` grava com `newline="\n"` (no Windows o modo texto converteria `\n` em CRLF e quebraria o `bash`) e só chama `os.chmod` quando `sys.platform != "win32"`. Em `tests/test_hooks.py`, acrescente um teste que instala os hooks e confirma que os arquivos gravados não contêm `\r`. Teste no Linux; o efeito no Windows é verificado pelo CI.

### Task 6: Reaping de órfãos no bridge

**Files:**
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_worker_orphan.py`
- Test: `tests/test_no_raw_posix_calls.py`

**Depends on:** Task 3, Task 4

Nas linhas ~2234–2245 de `bridge.py` (`os.killpg(int(pgid), 15)`, `os.killpg(os.getpgid(int(rec_pid)), 15)` e `os.kill(int(rec_pid), 15)`), use `osops.kill_tree(int(pgid))` e `osops.terminate_tree(int(rec_pid))`, preservando a ordem de tentativas e o tratamento de erro. Deixe a linha de shell do Herdr (`/bin/sh -c ...`, ~1049) como está: é específica do host Herdr, que não existe no Windows. Zere a lista de exceções do teste de varredura para os padrões já tratados.

### Task 7: Implementação Windows de `osops`

**Files:**
- Create: `meister/osops/windows.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Test: `tests/test_osops_windows.py`

**Depends on:** Task 1

`windows.py` implementa o contrato com `psutil` e `msvcrt`: `pid_alive` (`psutil.pid_exists` e estado diferente de zumbi); `process_signature` (`str(psutil.Process(pid).create_time())`, `None` se não existir); `popen_session_kwargs` (`{"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}`); `interrupt_group` (`os.kill(pid, signal.CTRL_BREAK_EVENT)`, tolerante a falha); `terminate_tree` (filhos recursivos via `psutil`: `terminate()`, espera até `grace`, depois `kill()`); `kill_tree` (no Windows `pgid == pid`; mesma lógica); `lock_file` (`msvcrt.locking` com `LK_NBLCK` em 1 byte, devolve `False` se ocupado) e `unlock_file`; `list_processes` (`psutil.process_iter(["pid", "cmdline"])`); `kill_self` (`os._exit(137)`). Dependência `psutil>=5.9; sys_platform == "win32"` em `pyproject.toml` (extras e dependências) e em `requirements.txt`. Os testes de `tests/test_osops_windows.py` rodam só no Windows (processos reais curtos) e há um teste portátil que importa o módulo com `psutil` e `msvcrt` simulados em `sys.modules` e confere as chamadas, para ter cobertura da lógica também no Linux.

### Task 8: Host `process` no Windows

**Files:**
- Modify: `tests/test_hosts_process.py`
- Modify: `tests/test_e2e_process_host.py`
- Modify: `tests/fixtures/fake_harness.py`
- Modify: `meister/hosts/process.py`

**Depends on:** Task 7

Faça a suíte `HostContract` e o teste ponta a ponta do host `process` rodarem também no Windows: remova a marca de só-Unix desses dois arquivos, faça o `fake_harness.py` ser lançado por `sys.executable` (sem depender de shebang) e, em `meister/hosts/process.py`, garanta que `spawn` usa `osops.popen_session_kwargs()` e que `interrupt` usa `osops.interrupt_group`. Se algum caso do contrato não puder valer no Windows (por exemplo `interrupt` de um `sleep` que ignora Ctrl+Break), marque-o com `skipif` e um motivo específico, nunca o remova. No Linux a suíte continua idêntica.

### Task 9: Medir os atalhos `.cmd` dos harnesses

**Files:**
- Create: `tests/test_windows_cmd_shim_args.py`

**Depends on:** Task 7

Teste só do Windows que cria um `fake.cmd` que grava seus argumentos, um por linha, num arquivo, e o executa pelo mesmo caminho que `HarnessWorker` usa (`subprocess.Popen` com lista de argumentos). Casos de prompt: com espaços; com aspas duplas e simples; com `%PATH%`; com `&`, `|`, `^`, `<` e `>`; com quebras de linha; com 20 mil caracteres. Cada caso é um teste separado que **afirma** a integridade do argumento; os que falharem hoje ficam marcados `xfail(strict=False, reason="...")` com a causa observada, de modo que o CI do Windows registre o que está corrompido sem ficar vermelho por isso. Não altere `meister/worker.py` nesta tarefa.

### Task 10: Documentação para quem usa Windows

**Files:**
- Modify: `README.md`
- Modify: `README.pt-BR.md`
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`

**Depends on:** Task 8

Na tabela de pré-requisitos do README, acrescente a linha de plataformas: Linux, macOS e Windows 10/11 (nativo, **em validação**; requer Git for Windows) e a observação de que no Windows o host é o `process` (sem Herdr). Em `docs/advanced.md` (e no equivalente em português), uma seção "Windows": instalação (`pipx install` ou `pip install` a partir do repositório; o `meister.exe` é criado pelo pip), `git config --global core.longpaths true`, onde ficam os logs, o aviso sobre os atalhos `.cmd` dos harnesses (resultado da Tarefa 9), e o que não é suportado ainda (Herdr, tmux). Links válidos nos dois idiomas.

### Task 11: CHANGELOG e documento de design

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`
- Modify: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`

**Depends on:** Task 10

Em `[Unreleased]`, nos dois idiomas: "Suporte nativo ao Windows (em validação): camada `meister.osops`, `psutil` apenas no Windows, correção de `os.kill(pid, 0)`, hooks gravados com final de linha LF". No documento de design, atualize a seção 8 (Windows deixa de ser "não suportado") e registre o que ficou de fora: hooks em Python, a mitigação dos atalhos `.cmd` e a decisão manual de tornar o job do Windows obrigatório.
