# Hosts de worker por adaptador: Meister sem dependência de terminal (Herdr, tmux ou nenhum)

**Status:** fases 1 e 2 concluídas (fase 1: contrato `WorkerHost` e adaptador `HerdrHost`, sem mudança de comportamento; fase 2: adaptador `process` e seleção `runtime.host: auto`). Fases 3 a 6 pendentes; ver seção 10.
**Base observada:** `main` em `19c875e` (2026-10-09). Números de linha são aproximados; prefira buscar pelo nome da função.
**Origem:** conversa de 2026-10-09 com o dono do projeto. Decisões já tomadas: **mesmo repositório** (nada de fork), Herdr vira **um adaptador** entre outros (tmux e execução direta como irmãos), objetivo de usar o Meister **em qualquer harness**.

## 1. Problema

Hoje o `meister orchestrate` só roda dentro do Herdr. Sem cliente, o orquestrador falha:

- `meister/herdr/bridge.py`: `RuntimeError("Herdr client is required to execute subtask")` e `"... required for orchestration cycle"`.
- `meister/herdr/workers.py`: `"Herdr client is required to spawn worker panes"` e `"... worker tabs"`.
- O README e o instalador tratam o Herdr como pré-requisito (o instalador aborta sem ele).

Isso impede usar o Meister de um harness qualquer (Claude Code, Codex, Copilot CLI, Antigravity, ...) sem instalar o Herdr, e amarra o produto a um terminal específico.

## 2. Objetivos e não objetivos

**Objetivos**

1. Rodar o ciclo completo (`classify`, workers, gate, integração, `control`) **sem Herdr nem tmux**.
2. Tratar onde o worker roda como uma peça **substituível**: Herdr, tmux e execução direta como adaptadores do mesmo contrato.
3. **Nenhuma quebra** para quem usa o Herdr hoje.
4. Permitir que o orquestrador (qualquer LLM de linha de comando) rode o `orchestrate` em segundo plano e acompanhe o andamento.

**Não objetivos**

- Reescrever o bridge, o gate, o Jev, o estado ou as worktrees.
- Suporte a Windows. Deixou de ser não objetivo: o Windows nativo está em validação (ver seção 8).
- Isolamento de sistema de arquivos dos workers (sandbox). Está fora de escopo; ver seção 8.

## 3. O que já é independente do Herdr (verificado)

O acoplamento está numa fronteira só, o objeto `client`; o resto já é neutro.

- **Protocolo de tarefa por arquivos.** O bridge grava um `task.json`, o `meister run-task <arquivo>` executa e devolve um `result.json`, que o bridge consulta (cerca de a cada 0,2 s). Nada disso passa pelo Herdr.
- **O harness já roda em segundo plano.** `HarnessWorker.run` (`meister/worker.py`) lança o harness com `subprocess.Popen`, `stdin=DEVNULL`, saída por pipe e `start_new_session=True`. O painel do Herdr só hospeda o `run-task` e mostra a saída repetida.
- **Comandos não interativos e sem aprovação**, definidos em `build_harness_command`: `codex exec --dangerously-bypass-approvals-and-sandbox`, `-p` com `--dangerously-skip-permissions` (claude e agy), `-p --allow-all --no-ask-user` (copilot).
- **Execução direta de um worker** já existe no comando `meister worker` (caminho "execução direta" quando o Herdr não está disponível ou com `--no-pane`).
- **Visualização independente de terminal:** `meister timeline` (TUI, `--once`, `--json`, janela web), `meister dashboard`, `meister report`, `meister replay`, o progresso do `orchestrate` no stderr, e `meister wait` (espera a run terminar, `--format json`, códigos de saída 0, 1, 124 e 130, lê só o log).
- **Proteção contra worker parado** por inatividade (`idle_timeout_seconds`, 600 s por padrão) e reaping de harness órfão por PID e assinatura do processo (`reap_harness`).
- **Retomada** de runs (`--resume`) e matriz de falhas de processo nos testes.

## 4. O que o Herdr faz hoje (a ser coberto pelo contrato)

| Função | Onde hoje | Equivalente em adaptador local |
|---|---|---|
| Hospedar o `run-task` | `herdr/workers.py` (`spawn_worker_tab`, `spawn_worker_pane`) | `Popen` em novo grupo de processos, saída em arquivo de log |
| Saber se morreu | evento `pane_exited` e `pane_exists` | `proc.poll()` e o arquivo de PID existente |
| Detectar worker parado | `read_pane` com impressão digital (`bridge.py`) | impressão digital do final do log (o fingerprint do worktree já existe) |
| Detectar cota ou rate limit | saída do painel em `handle_herdr_event` | varrer o log com `detect_quota_or_rate_limit` (**hipótese a validar**) |
| Interromper | `send_interrupt`, `close_pane`, `close_tab` | `killpg` (existe `kill_process_tree`) |
| Avisos | `show_notification` | `ProgressReporter` no stderr (já existe) |
| Ver os workers | abas visíveis | timeline e dashboard; log por worker (novo) |

Fora do host: `herdr-plugin.toml`, `bin/herdr-meister.sh`, `herdr-action`, `daemon` e os atalhos instalados pelo `meister setup` são **superfície de interface** do Herdr. Continuam existindo como integração opcional.

## 5. Design

### 5.1 Dois papéis separados

- **Host de worker:** onde o `run-task` roda, como se sabe se vive, como ler o final da saída, como interromper. É o que o adaptador implementa.
- **Superfície de interface:** como o usuário dispara e acompanha (atalhos e popups do Herdr, timeline, dashboard). Não faz parte do contrato do host.

### 5.2 Contrato

O worker é referenciado por um **`WorkerHandle(id, aux)`**: `id` é a string opaca que o host devolve (no Herdr, o `pane_id`, algo como `"w9:pFG"`) e `aux` guarda dados extras do host (no Herdr, o `tab_id`, usado para fechar a aba). O bridge chaveia `active_workers`, eventos de saída e de cota pelo `id`, então esse formato é preservado.

O que foi implementado em `meister/hosts/base.py` (a versão abaixo substitui o esboço original):

```python
class WorkerHost(Protocol):             # runtime_checkable
    name: str
    capabilities: frozenset[str]        # CAP_VISIBLE = "visible", CAP_PUSH_EVENTS = "push_events"

    async def start(self, on_event: Optional[EventCallback] = None) -> None: ...
    async def spawn(self, command: WorkerCommand, *, layout: str = "tab") -> WorkerHandle: ...
    async def alive(self, handle: WorkerHandle) -> bool | None: ...      # None = desconhecido
    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str: ...
    async def interrupt(self, handle: WorkerHandle) -> None: ...
    async def close(self, handle: WorkerHandle) -> None: ...
    async def notify(self, message: str, title: Optional[str] = None) -> None: ...
    async def process_info(self, handle: WorkerHandle) -> dict[str, Any] | None: ...  # None = não suportado
    async def current_context(self) -> dict[str, Any] | None: ...                    # None = não suportado

@dataclass
class WorkerCommand:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    label: str
    terminal_line: Optional[str] = None  # linha digitada no terminal do worker após o start; None não digita nada

class WorkerHandle:  # frozen dataclass
    id: str
    aux: Any = None
```

Erros de operação são levantados como `HostError(RuntimeError)`.

Regras:

- **Polling é o padrão.** O núcleo consulta `alive` e `tail`; `start(on_event)` só recebe eventos push quando o adaptador declara `CAP_PUSH_EVENTS` (o Herdr declara; o processo local não precisa).
- **Layout é do adaptador.** `spawn` recebe `layout` (`"tab"` ou `"pane"`); o `HerdrHost` decide a forma a partir de `workers.layout_strategy`, e o contrato fala só em "um worker".
- **`alive` pode responder `None`** quando o host não sabe dizer; o núcleo trata isso como desconhecido, não como morto.
- **`process_info` e `current_context` são opcionais** e retornam `None` quando o host não os suporta.
- **Conceitos do Herdr (`workspace_id`, `architect_pane_id`) saem do núcleo** e viram opções do adaptador Herdr (`HerdrHost(client, config)`).

Adaptadores existentes: `HerdrHost` (`meister/hosts/herdr.py`), que embrulha o `HerdrSocketClient` e replica as mesmas chamadas, na mesma ordem e com os mesmos argumentos, que o código anterior fazia. Para testes há um host de mentira e um kit de contrato (`tests/hosts_fake.py`, `tests/hosts_contract.py`).

### 5.3 Adaptadores

| Adaptador | Mecanismo | Observações |
|---|---|---|
| `process` (padrão) | `subprocess.Popen` com `start_new_session=True`, stdout e stderr em `.meister/` (log por worker), `killpg` para interromper | Sem dependências. Funciona em qualquer harness. |
| `herdr` | Embrulha o `HerdrSocketClient` existente | Comportamento atual preservado, incluindo eventos push. |
| `tmux` | `new-window -d -c <cwd>`, `capture-pane -p`, `send-keys C-c`, `#{pane_dead}` com `remain-on-exit` | Sem fluxo de eventos; o polling resolve. Visualização lado a lado. |
| de terceiros | registro por nome (e, depois, entry point) | Fora do escopo inicial. |

### 5.4 Seleção

`meister.config.yaml`:

```yaml
runtime:
  host: auto   # auto | process | herdr | tmux
```

- `auto`: se houver socket do Herdr acessível, usa `herdr` (mantém o comportamento atual); senão usa `process`.
- A ausência do Herdr deixa de ser erro. As mensagens `Herdr client is required...` somem do caminho padrão.

### 5.5 Onde fica no código

- Novo pacote `meister/hosts/` com `base.py` (contrato), `process.py`, `herdr.py` e, depois, `tmux.py`.
- `meister/herdr/` permanece como está na primeira etapa (cliente, plugin, TUI). O bridge passa a falar com `WorkerHost`.
- Renomear `meister/herdr/` (o bridge e o DAG são o orquestrador, não "Herdr") fica para uma etapa posterior e opcional.

## 6. Execução em segundo plano e permissões

**O orquestrador em segundo plano.** O `meister orchestrate` continua em primeiro plano por padrão. Para um harness rodá-lo em segundo plano sem bloquear:

- usar o recurso de segundo plano do próprio harness, ou `nohup meister orchestrate ... > .meister/orchestrate.log 2>&1 &` (o `setsid` evita que o processo morra com a sessão; o comportamento varia entre harnesses e **não foi verificado em cada um**);
- acompanhar com `meister wait --format json` (bloqueia até a run terminar e devolve o código de saída), `meister timeline --json --once` e `meister report`;
- retomar com `--resume` se cair.

**Yolo (pular permissões).**

- **Workers:** já rodam em modo sem aprovação. Nenhuma mudança.
- **Orquestrador:** não recomendado em modo yolo. O guard hook (`PreToolUse`) é a barreira que impede o orquestrador de editar código direto; em geral hooks continuam valendo sem permissões, mas isso depende do harness (**não verificado**). Preferir uma permissão específica que libere só `meister *` (por exemplo, `Bash(meister:*)` no Claude Code).
- Se quiser yolo em tudo, executar dentro de contêiner ou VM descartável.

**Limite conhecido:** os workers rodam com acesso total à máquina. O gate de escopo vigia só o que muda no worktree; uma escrita fora dele não é detectada. O `build_safe_worker_env` filtra variáveis de ambiente, mas não isola o sistema de arquivos.

## 7. Visualização sem terminal

| Necessidade | Meio | Situação |
|---|---|---|
| Linha do tempo das tarefas | `meister timeline` (TUI, web, `--json`) | existe |
| Métricas, custo, eventos | `meister dashboard`, `report`, `replay` | existe |
| Esperar o fim e obter o resultado | `meister wait --format json` | existe |
| Saída de um worker específico, ao vivo | log por worker + `meister logs --follow` | **novo** |
| Terminais lado a lado | adaptador `tmux` ou `herdr` | opcional |

O que se perde sem Herdr ou tmux é só ver o terminal de cada worker ao vivo e lado a lado. Se isso basta como experiência padrão é uma decisão de produto (seção 10).

## 8. O que continua necessário sem Herdr

- Pelo menos um harness autenticado: `copilot`, `codex`, `agy` ou `claude`.
- Git (cada worker roda num worktree).
- Python 3.10 ou superior e as bibliotecas do projeto. `flask` hoje é obrigatório até para a CLI iniciar (a TUI importa o dashboard); torná-lo opcional é uma melhoria separada.
- Linux, macOS ou Windows nativo (em validação; ver a nota abaixo).
- Chave do OpenRouter **somente** para o Jev; sem ela, usar `router: {mode: first}`. O Jev tem fallback determinístico quando está fora do ar.

**Windows nativo (em validação).** Deixou de ser "não suportado". O código específico de cada sistema operacional fica na camada `meister/osops/`: `posix.py` mantém o comportamento existente de Linux e macOS, e `windows.py` cobre o Windows. `psutil` é dependência apenas no Windows. A correção de `os.kill(pid, 0)` e a gravação dos hooks com final de linha LF fazem parte da entrega. Nenhum código fora de `meister/osops/` usa diretamente `os.killpg`, `os.getpgid`, `fcntl`, `signal.SIGKILL`/`SIGHUP`, `os.kill(pid, 0)` ou `ps`; um teste varre o código-fonte e falha se essas formas reaparecerem.

Ficou de fora desta entrega:

- **Hooks em Python.** Os hooks continuam como scripts de shell, gravados com LF; reescrevê-los em Python não entrou.
- **Mitigação dos atalhos `.cmd`.** Os atalhos do Windows não receberam tratamento específico.
- **Job do Windows obrigatório no CI.** Tornar o job do Windows obrigatório é uma decisão manual do dono do projeto, não automática.

## 9. Testes

- **Suíte de contrato parametrizada por adaptador**, usando um harness falso (script) que: termina com sucesso, termina com falha, fica parado, imprime texto de cota, ignora o primeiro sinal de interrupção. Casos: `spawn`, `alive` (vivo e morto), `tail`, `interrupt`, `close`, reaping de órfãos, detecção de worker parado por inatividade e de cota.
- `process` e `tmux` rodam no CI de Linux (o `tmux` é pulado se não estiver instalado). `herdr` usa os dublês atuais.
- Os testes específicos do Herdr (cerca de 4,8 mil linhas em 7 arquivos) continuam válidos na primeira etapa, porque o adaptador Herdr embrulha o cliente atual.
- A matriz de falhas (`test_crash_matrix.py`) passa a rodar também com o host `process`.
- CI sem Herdr instalado deve passar na suíte inteira.

## 10. Fases (cada uma com a suíte verde)

Conforme a política de planos do projeto (muitas tarefas pequenas), cada fase se divide em tarefas de 3 a 5 arquivos.

1. **Contrato e adaptador Herdr. (Concluída.)** Criado `meister/hosts/base.py` e `herdr.py`; bridge e spawner falam com `WorkerHost`, sem mudança de comportamento. Testes existentes do Herdr passam sem alteração de asserções; a sequência de chamadas ao cliente é verificada pelos testes de delegação.
2. **Adaptador `process`. (Concluída.)** Spawn, vivo/morto, `tail` por arquivo de log, interrupção por grupo de processos; seleção `runtime.host: auto`; teste de ponta a ponta sem Herdr; validação de cota e worker parado.
   Decisões efetivas:
   - `WorkerCommand.log_file` (caminho da saída do worker) e `WorkerCommand.exit_file` (arquivo onde o código de saída é gravado ao término, via `_record_exit`). Ambos opcionais em `meister/hosts/base.py`.
   - Seleção `runtime.host: auto` (valores válidos: `auto`, `process`, `herdr`, `tmux`): usa `herdr` quando o socket está acessível; caso contrário, `process`. A seleção é feita em `meister/hosts/select.py`.
   - Log por worker em `<log_dir>/workers/<label>.log`, quando `log_file` não é informado.
   - Varredura de cota por `tail`: o bridge lê periodicamente o final da saída do worker e passa ao `detect_quota_or_rate_limit`. Mesmo mecanismo do Herdr, agora sobre o log.
3. **Experiência para qualquer harness.** Log por worker e `meister logs --follow`; documentação de execução em segundo plano com `meister wait`; modelos de instrução por harness; variável neutra `MEISTER_WORKER` (mantendo `MEISTER_IN_PANE` como alias, pois o guard hook depende dela).
4. **Adaptador `tmux`.**
5. **Documentação e instalador.** Herdr passa de pré-requisito a opcional; `meister setup` deixa de abortar sem ele; README e `CLAUDE.md`/`AGENTS.md` atualizados.
6. **Opcional:** renomear `meister/herdr/` e tornar `flask` opcional.

## 11. Riscos

- **Vazamento de abstração.** `workspace_id` e `architect_pane_id` aparecem no orquestrador; precisam virar opções do adaptador sem alterar o comportamento do Herdr.
- **Cota no modo local.** Mitigado por varredura periódica do `tail` (log por worker) com `detect_quota_or_rate_limit`. Validar com o harness real.
- **Velocidade do upstream.** Há muitos commits por dia em `bridge.py`; fazer a fase 1 em PRs pequenos reduz conflito.
- **Worker que espera resposta.** Sem terminal, ninguém responde. O tempo limite de inatividade precisa continuar valendo no adaptador `process`.
- **Segundo plano varia por harness.** Documentar o comando padrão (`nohup`/`setsid`) e testar nos harnesses suportados.

## 12. Decisões em aberto

1. `process` como padrão quando não houver Herdr (proposto) ou recusar e pedir escolha explícita?
2. O nome `meister/hosts/` serve?
3. Timeline mais dashboard mais log por worker bastam como experiência padrão, ou o `tmux` precisa estar na primeira entrega?
4. Tornar `flask` opcional entra neste trabalho ou fica separado?

## 13. Lacunas desta análise

- Não li o `bridge.py` inteiro nem medi o que mudou nos commits recentes; só estudei os pontos de contato com o `client`.
- Não rodei nada sem Herdr e não havia Herdr real disponível.
- Não sei quais funções do plugin Herdr (popups, atalhos) o dono usa no dia a dia.
- Não verifiquei, harness a harness, como o segundo plano e os hooks se comportam em modo sem permissões.
