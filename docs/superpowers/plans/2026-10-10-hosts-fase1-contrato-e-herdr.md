# Hosts de worker, fase 1: contrato `WorkerHost` e adaptador Herdr Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Origem:** fase 1 de `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`. É uma refatoração **sem mudança de comportamento**: o bridge passa a falar com um contrato, e o único adaptador é o Herdr, que embrulha o `HerdrSocketClient` atual. O adaptador `process` (fase 2) e a escolha `runtime.host` ficam fora.
>
> **Ordem:** o `bridge.py` (2,6 mil linhas) é editado por três tarefas em sequência (5, 6 e 7), cada uma em uma região diferente. Não rodar em paralelo com outro plano que mexa em `meister/herdr/bridge.py` ou `meister/herdr/workers.py`.

**Goal:** Introduzir `meister/hosts/` com o contrato `WorkerHost` (e `WorkerHandle`, `WorkerCommand`) e o adaptador `HerdrHost`, e migrar `WorkerSpawner` e `HerdrEventBridge` para usá-los, de modo que a sequência de chamadas ao `HerdrSocketClient` fique idêntica em cada cenário.

**Architecture:** O bridge usa o cliente do Herdr em quatro papéis: criar o worker (`create_tab`/`split_pane`/`wait_pane_ready`/`send_text`, hoje no `WorkerSpawner`), observá-lo (`pane_exists`, `read_pane`), controlá-lo (`send_interrupt`, `close_tab`/`close_pane`) e falar com o usuário (`show_notification`, `get_current_pane`, `subscribe_events`). O contrato tem uma operação por papel. O worker é identificado por um `WorkerHandle(id, aux)`: `id` é o `pane_id` de hoje e `aux` é um texto opaco do adaptador (no Herdr, o `tab_id`). O bridge continua gravando os dois no estado pelo `register_pane(pane_id, tab_id=...)` existente, então o esquema do banco não muda e o reaping de órfãos depois de um reinício reconstrói o handle a partir da linha gravada.

**Tech Stack:** Python ≥ 3.10, `asyncio`, `typing.Protocol`, `pytest`. Nenhuma dependência nova.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Identidade do worker | `WorkerHandle(id: str, aux: str \| None = None)`, imutável. `id` = `pane_id`; `aux` = `tab_id` no Herdr. |
| Fechar | `host.close(handle)`: no Herdr, com `aux` fecha a aba (`close_tab`), sem `aux` fecha o painel (`close_pane`). Hoje essa decisão está espalhada pelo bridge (`tab_closed`, `pane_closed`); passa para o adaptador. |
| Estar vivo | `alive(handle) -> bool \| None`. `None` = "o host não sabe" (cliente sem `pane_exists`); o bridge trata `None` como hoje trata a ausência do método, isto é, não faz a checagem. |
| Comando | `WorkerCommand(argv, env, cwd, label, terminal_line=None)`. A linha de shell (`command_str`, com `/bin/sh -c` e o arquivo de código de saída) **continua montada no bridge** nesta fase e vai em `terminal_line`, usada só por hosts de terminal. Movê-la para o adaptador fica para a fase 2. |
| Eventos | `start(on_event)` conecta e assina os eventos, se o host tiver (`capabilities` com `"push_events"`); sem eventos o núcleo continua por polling. |
| Compatibilidade | `HerdrEventBridge(config, client=...)` e `WorkerSpawner(config, herdr_client=...)` continuam aceitando o cliente: quando não recebem um host, constroem um `HerdrHost`. `bridge.client` continua existindo (propriedade) porque testes o leem e o substituem. |

## Global Constraints

- **Sem mudança de comportamento:** os testes existentes do Herdr (`tests/test_herdr_*.py`, `tests/test_workers.py`, `tests/test_worker_execution.py`, `tests/test_crash_matrix.py`, `tests/test_integration_merge.py`) passam **sem alteração de asserções**. Só se aceita mudar fiação (construtor, fixture).
- A sequência e os argumentos das chamadas ao cliente do Herdr em cada cenário não mudam; os testes de delegação da Tarefa 3 provam isso.
- Sem dependência nova; compatível com Python 3.10; `from __future__ import annotations` nos módulos novos.
- Mensagens ao usuário continuam por `t()`; nada de string nova solta (`tests/test_i18n_ratchet.py` e `tests/test_locales_parity.py` ficam verdes). `tests/test_no_hardcoded_models.py` continua passando.
- `ruff check .` e `mypy meister` limpos; a suíte inteira verde ao fim de **cada** tarefa.
- Testes sem rede, sem tocar `~/.meister`, sem chamar CLI de IA.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Contrato `WorkerHost`

**Files:**
- Create: `meister/hosts/__init__.py`
- Create: `meister/hosts/base.py`
- Test: `tests/test_hosts_base.py`

**Depends on:** none

Crie o pacote `meister.hosts`. Em `base.py`: `@dataclass(frozen=True) WorkerHandle(id, aux=None)`; `@dataclass WorkerCommand(argv, env, cwd, label, terminal_line=None)`; constantes de capacidade (`CAP_VISIBLE = "visible"`, `CAP_PUSH_EVENTS = "push_events"`); `class HostError(RuntimeError)`; e `class WorkerHost(Protocol)` com `name: str`, `capabilities: frozenset[str]` e as operações assíncronas `start(on_event=None)`, `spawn(command, *, layout="tab") -> WorkerHandle`, `alive(handle) -> bool | None`, `tail(handle, lines=200) -> str`, `interrupt(handle)`, `close(handle)`, `notify(message, title=None)`, `process_info(handle) -> dict | None` e `current_context() -> dict | None` (estas duas últimas opcionais; documente o retorno `None` como "não suportado"). Exporte tudo em `__init__.py`. O teste verifica que `WorkerHandle` é imutável e comparável, que `WorkerCommand` aceita `terminal_line` ausente e que uma classe mínima que implementa as operações satisfaz `isinstance(obj, WorkerHost)` (use `@runtime_checkable`).

### Task 2: Kit de testes de contrato e host de mentira

**Files:**
- Create: `tests/hosts_contract.py`
- Create: `tests/hosts_fake.py`
- Test: `tests/test_hosts_fake.py`

**Depends on:** Task 1

Crie uma suíte reutilizável `HostContract` em `tests/hosts_contract.py` (classe base com uma fixture `host` a ser fornecida pela subclasse e testes assíncronos): `spawn` devolve um handle com `id` não vazio; `alive` é verdadeiro logo após o `spawn` e falso (ou `None` se o host não souber) depois de o processo terminar; `tail` devolve o texto já impresso; `interrupt` interrompe um worker que está esperando; `close` é idempotente (chamar duas vezes não levanta); `notify` não levanta. Crie `FakeHost` em `tests/hosts_fake.py`, em memória (guarda o texto de cada worker, simula término e interrupção), e `tests/test_hosts_fake.py` que roda a suíte contra ele. A suíte será reaproveitada pelos adaptadores `process` (fase 2) e `tmux`.

### Task 3: Adaptador `HerdrHost`

**Files:**
- Create: `meister/hosts/herdr.py`
- Modify: `meister/hosts/__init__.py`
- Test: `tests/test_hosts_herdr.py`

**Depends on:** Task 1

`HerdrHost(client, config=None)` embrulha um `HerdrSocketClient`. Mapa (replicando exatamente as chamadas de hoje de `WorkerSpawner.spawn_worker_tab/pane` e do bridge): `spawn(layout="tab")` faz `create_tab(cwd, label, focus=False)`, `wait_pane_ready` (se o cliente tiver) e `send_text(pane_id, terminal_line + "\n")`, devolvendo `WorkerHandle(id=pane_id, aux=tab_id)`; `layout="pane"` faz `split_pane` e devolve `aux=None`; `alive` → `pane_exists` (ou `None` se o cliente não tiver o método); `tail` → `read_pane`; `interrupt` → `send_interrupt`; `close` → `close_tab(aux)` se houver `aux`, senão `close_pane(id)`, ignorando falhas como o bridge faz hoje (registrar em debug); `notify` → `show_notification`; `start` → `connect()` se não conectado e `subscribe_events(on_event)`; `process_info` → `_call("pane.process_info", {"pane_id": id})`; `current_context` → `get_current_pane`. `capabilities = {"visible", "push_events"}`. Os testes usam um cliente falso (`MagicMock` com `AsyncMock`) e afirmam as chamadas e a ordem.

### Task 4: `WorkerSpawner` usa o host

**Files:**
- Modify: `meister/herdr/workers.py`
- Test: `tests/test_workers.py`
- Test: `tests/test_herdr_tabs.py`

**Depends on:** Task 3

`WorkerSpawner.__init__` ganha `host: WorkerHost | None = None`. Sem `host` mas com `herdr_client`, constrói `HerdrHost(herdr_client)`. `spawn_worker_tab` e `spawn_worker_pane` passam a chamar `self.host.spawn(...)`, mantendo as assinaturas e os retornos públicos: `(tab_id, pane_id, tier)` com `tab_id = handle.aux` e `pane_id = handle.id`, e `(pane_id, tier)`. Os erros atuais (`"Herdr client is required to spawn worker panes/tabs"`) continuam quando não há host nem cliente. `herdr_client` continua um atributo legível (testes o leem e o atribuem; o bridge o atribui em `bridge.py:~249`). Os testes existentes passam sem mudar asserções; acrescente um que construa o spawner só com `host=` (um `FakeHost`).

### Task 5: Bridge, construtor e ciclo de vida

**Files:**
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_herdr_bridge.py`

**Depends on:** Task 4

Em `HerdrEventBridge.__init__` (~240–260), aceite `host: WorkerHost | None = None`. Sem `host` mas com `client`, construa `HerdrHost(client)` e compartilhe-o com o spawner. Mantenha `self.client` como propriedade com setter (reconstrói o host ao ser atribuído) para os testes que fazem `bridge.client = ...`. Migre a região de ciclo de vida e avisos (~2280–2620): `connect`/`is_connected`/`subscribe_events` → `host.start(self.handle_herdr_event)`; `get_current_pane` → `host.current_context()`; as cerca de 20 chamadas a `show_notification` → `host.notify`; a leitura do painel do arquiteto (`read_pane(architect_pane_id)`, ~2316) → `host.tail(WorkerHandle(architect_pane_id))`. Nenhuma outra região do arquivo muda nesta tarefa.

### Task 6: Bridge, observar e interromper

**Files:**
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_herdr_bridge.py`

**Depends on:** Task 5

Migre as chamadas de observação e interrupção para o host: `read_pane` (~1167, 1214, 1271, 1585, 1730) → `host.tail`; `pane_exists` (~1283–1285) → `host.alive` (tratando `None` como a ausência de `hasattr(...)` hoje); `send_interrupt` (~597, 1395, 1602, 1711, 1747, 2224) → `host.interrupt`. Os `WorkerHandle` são montados a partir de `pane_id` e `tab_id` que o código já tem em escopo. A impressão digital do painel, a detecção de cota e a janela de inatividade não mudam. Não mexer em nada de fechamento nesta tarefa.

### Task 7: Bridge, fechar workers e reaping de órfãos

**Files:**
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_herdr_bridge.py`
- Test: `tests/test_worker_orphan.py`

**Depends on:** Task 6

Substitua as chamadas `close_tab`/`close_pane` e as variáveis de controle `tab_closed`/`pane_closed` (~1356–1760, 2075–2083) por `host.close(WorkerHandle(pane_id, tab_id))`, mantendo as mesmas condições de quando fechar e os mesmos logs em caso de falha. No reaping de órfãos (~2207–2259), reconstrua o handle a partir da linha gravada (`pane_id` e `tab_id` do `register_pane`) e use `host.close` e `host.process_info` (no lugar de `_call("pane.process_info", ...)`); `process_info` devolvendo `None` mantém o caminho atual de "sem informação de processo". `register_pane(pane_id, ..., tab_id=tab_id)` e o esquema do estado não mudam. Ao fim, `grep -n "self\.client\." meister/herdr/bridge.py` não deve retornar nada fora da propriedade de compatibilidade.

### Task 8: Documentação da fase 1

**Files:**
- Modify: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`
- Modify: `ARCHITECTURE.md`

**Depends on:** Task 7

No documento de design, marque a fase 1 como concluída (seção 10) e atualize o contrato da seção 5.2 com o que de fato foi implementado (`WorkerHandle(id, aux)`, `WorkerCommand.terminal_line`, `alive -> bool | None`, `start`, `process_info`, `current_context`). Em `ARCHITECTURE.md`, acrescente uma subseção curta "Hosts de worker" explicando o contrato, o adaptador Herdr e o ponto de extensão para novos adaptadores, sem nomes de modelos. `tests/test_docs_links.py` deve continuar passando.
