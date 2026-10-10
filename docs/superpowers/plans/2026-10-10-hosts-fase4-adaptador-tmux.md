# Hosts de worker, fase 4: adaptador `tmux` Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois do plano `2026-10-10-hosts-fase2-adaptador-process.md`:** usa o contrato, a suíte `HostContract` e `select_host`. Independe da fase 3. Origem: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`, seção 10, fase 4.
>
> **Hot file:** `.github/workflows/ci.yml` (Tarefa 3). Não rodar junto de outro plano que mexa nele.

**Goal:** Quem quiser ver os workers lado a lado, sem Herdr, usa `runtime.host: tmux`: cada worker abre uma janela do tmux, e o Meister lê, interrompe e fecha por comandos do `tmux`. Nada muda para quem usa `auto`, `process` ou `herdr`.

**Architecture:** `TmuxHost` implementa o contrato chamando o binário `tmux` com `asyncio.create_subprocess_exec`. Como o Herdr, é um host de terminal: roda a **linha de shell** que o bridge já monta (`WorkerCommand.terminal_line`, com o prefixo de variáveis e o arquivo de código de saída), então o bridge não muda. Não há fluxo de eventos; o laço do bridge faz polling de `alive` e `tail`, e a varredura de cota da fase 2 cobre o resto. Para o tmux não depender do servidor do usuário nos testes, o adaptador aceita um nome de socket (`tmux -L`).

**Tech Stack:** Python ≥ 3.10, `asyncio`, `tmux` ≥ 3.0 (dependência de execução apenas para quem escolhe esse host), `pytest`. Nenhuma dependência Python nova.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Sessão | Se `$TMUX` está definido, usa a sessão atual. Senão cria uma sessão destacada `meister-<pid>` e avisa, por `notify`, o comando `tmux attach -t meister-<pid>`. Ao fim da run, o adaptador só fecha as janelas que criou; a sessão criada por ele é encerrada quando fica sem janelas do Meister. |
| Janela por worker | `new-window -d -P -F '#{window_id} #{pane_id}' -n <label> -c <cwd> <terminal_line>`, com `remain-on-exit on` ligado na janela logo depois de criada (para o texto e o estado de saída continuarem legíveis). `WorkerHandle(id=<pane_id>, aux=<window_id>)`. Se o processo morrer antes de a opção ser ligada, `alive` é falso e `tail` vazio (aceito e documentado). |
| Vivo | `alive` é verdadeiro se o painel existe e `#{pane_dead}` é `0`. |
| Texto | `tail` usa `capture-pane -p -J -S -<linhas>`. |
| Interrupção | `send-keys C-c` no painel. |
| Fechar | `kill-window` na janela; idempotente (janela inexistente não é erro). |
| Aviso | `display-message`; se não há cliente anexado, escreve no stderr. |
| Capacidades | `{"visible"}`. Sem `push_events`. |
| Seleção | `auto` nunca escolhe `tmux` (a ordem é Herdr, depois `process`). Só `runtime.host: tmux` explícito. |
| Versão | `tmux -V` menor que 3.0, ou binário ausente, é `HostError` com mensagem traduzida. |

## Global Constraints

- Sem dependência Python nova; compatível com Python 3.10; `from __future__ import annotations`.
- Mensagens ao usuário por `t()` com entradas nos dois idiomas; `tests/test_i18n_ratchet.py`, `tests/test_locales_parity.py` e `tests/test_no_hardcoded_models.py` verdes.
- Os testes com `tmux` usam um servidor isolado (`-L meister-test-<pid>`), nunca o do usuário, e o encerram no `finally` (`kill-server`). São pulados quando `shutil.which("tmux")` é `None` ou a plataforma é Windows. Nenhum teste existente fica dependente do `tmux`.
- `ruff check .` e `mypy meister` limpos; a suíte inteira verde ao fim de **cada** tarefa, com e sem `tmux` instalado.
- Testes sem rede, sem tocar `~/.meister`, sem chamar CLI de IA.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: `TmuxHost`

**Files:**
- Create: `meister/hosts/tmux.py`
- Modify: `meister/hosts/__init__.py`
- Modify: `tests/hosts_contract.py`
- Test: `tests/test_hosts_tmux.py`

**Depends on:** none

`TmuxHost(config=None, *, socket_name=None, session=None)`. Uma função interna `_tmux(*args) -> str` executa `tmux [-L socket] args` e levanta `HostError` com a saída de erro quando o código de saída não é zero (os métodos tolerantes capturam esse erro). `start`: confere `tmux -V` (≥ 3.0), usa a sessão atual (`$TMUX`) ou cria a sessão destacada e a anuncia por `notify`. Demais operações conforme a tabela de decisões: `spawn` exige `command.terminal_line` (sem ele, `HostError`), `alive`, `tail`, `interrupt`, `close`, `notify`, `process_info` (`#{pane_pid}` → `{"pid": N}`) e `current_context` (`{"session": nome}`). `capabilities = frozenset({"visible"})`. Em `tests/hosts_contract.py` garanta que o auxiliar que monta comandos de teste preenche `argv` **e** `terminal_line` (`shlex.join(argv)`), porque os hosts de terminal usam a linha. `tests/test_hosts_tmux.py` roda a suíte `HostContract` contra o `TmuxHost` com socket isolado e acrescenta: janela criada e fechada, `close` idempotente, `tail` com o texto impresso, `interrupt` interrompendo um `sleep`, e erro claro para `tmux` ausente (simule `shutil.which` como `None`).

### Task 2: Seleção do `tmux`

**Files:**
- Modify: `meister/hosts/select.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_hosts_select.py`

**Depends on:** Task 1

Em `select_host`, o modo `tmux` passa a devolver `TmuxHost()`; se o binário `tmux` não existir ou for anterior à 3.0, `HostError` com mensagem traduzida (chave nova `engine.hosts.tmux_not_found`). A chave `engine.hosts.tmux_unavailable` da fase 2 deixa de ser usada e deve ser removida dos dois catálogos (e do teste que a citava). Garanta, com teste, que `auto` **nunca** escolhe `tmux`, mesmo dentro de uma sessão tmux (`$TMUX` definido).

### Task 3: `tmux` no CI

**Files:**
- Modify: `.github/workflows/ci.yml`

**Depends on:** Task 2

Antes do passo `Run Pytest`, acrescente `Install tmux` com `if: runner.os == 'Linux'` e `sudo apt-get update && sudo apt-get install -y tmux`, e `if: runner.os == 'macOS'` com `brew install tmux`. Nenhum outro passo muda; o Windows não instala nada. Valide que o YAML carrega e que `tests/test_python_floor.py` passa (a chave `matrix.python-version` fica como está).

### Task 4: Ponta a ponta com tmux

**Files:**
- Create: `tests/test_e2e_tmux_host.py`

**Depends on:** Task 2

Repita o cenário de `tests/test_e2e_process_host.py` (fase 2, harness de mentira em `tests/fixtures/fake_harness.py`) com `runtime.host: tmux` e socket isolado: duas tarefas integradas, código de saída gravado pela linha de shell, fallback quando o harness imprime cota, e nenhuma janela do Meister sobrando ao final. Pule quando não houver `tmux` ou na plataforma Windows.

### Task 5: Documentação

**Files:**
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`
- Modify: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`

**Depends on:** Task 4

Em `docs/advanced.md` e no equivalente em português, documente `runtime.host: tmux`: requisito (`tmux` ≥ 3.0), comportamento dentro e fora de uma sessão, o comando `tmux attach -t meister-<pid>`, e a limitação de processo que morre antes de `remain-on-exit`. No documento de design, marque a fase 4 como concluída.
