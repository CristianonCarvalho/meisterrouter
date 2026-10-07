# Diagramas do MeisterRouter

Diagramas [Mermaid](https://mermaid.js.org/) do funcionamento do sistema (o GitHub os desenha direto na página).
Cada um foi montado a partir do código; os nomes de módulos, eventos e estados são os reais.
O fluxo completo "do plano à `main`" também está em [`FLUXO_MEISTERROUTER.md`](FLUXO_MEISTERROUTER.md).

| # | Diagrama | Responde a |
|---|---|---|
| 1 | [Visão geral (contexto)](#1-visão-geral-contexto) | Quem participa e o que cada um faz |
| 2 | [Componentes](#2-componentes) | Quais módulos existem e como se ligam |
| 3 | [Sequência de um `orchestrate`](#3-sequência-de-um-orchestrate) | O que acontece, na ordem, de um plano até a `main` |
| 4 | [Escolha da via](#4-escolha-da-via-roteamento) | Como uma subtarefa chega a um worker |
| 5 | [Falhas, timeout e cota](#5-falhas-timeout-e-cota-de-um-worker) | O que acontece quando o worker não termina |
| 6 | [Integração e gates](#6-integração-e-gates) | Por que nada entra na `main` sem verificação |
| 7 | [Estados do run e da subtarefa](#7-estados-do-run-e-da-subtarefa) | Transições válidas e retomada |
| 8 | [Worktrees e branches](#8-worktrees-e-branches) | Onde o código de cada worker vive |
| 9 | [Telemetria e visualização](#9-telemetria-e-visualização) | De onde vêm o dashboard, a linha do tempo e o relatório |
| 10 | [Guard do Claude Code](#10-guard-do-claude-code) | Como a orquestradora é impedida de editar código |
| 11 | [Instalação e integração com o Herdr](#11-instalação-e-integração-com-o-herdr) | O que o instalador e o `init` configuram |

---

## 1. Visão geral (contexto)

Você escolhe uma **LLM orquestradora** (por exemplo o Claude Code) que planeja. O Meister, que é código
determinístico, distribui as tarefas pelas suas **assinaturas** (Copilot, Codex, Antigravity, Claude),
coordena os workers e integra o resultado. A única parte não determinística do Meister é o **Jev**, que só
escolhe a via inicial e julga o resultado.

```mermaid
flowchart LR
  U(["Você"])
  ORQ["LLM orquestradora<br/>(Claude Code, Codex...)<br/>planeja e revisa"]
  subgraph MR["MeisterRouter (Python, determinístico)"]
    CLI["CLI meister<br/>e daemon"]
    CORE["Orquestração:<br/>DAG, estado, merge, gates"]
  end
  JEV["Jev (OpenRouter)<br/>classify e control<br/>única parte não determinística"]
  HERDR["Herdr<br/>tabs visíveis, popups, atalhos"]
  subgraph W["Workers (CLIs nas suas assinaturas)"]
    W1["copilot"]
    W2["codex"]
    W3["agy (Gemini)"]
    W4["claude"]
  end
  GIT[("Repositório Git<br/>worktrees e branches")]
  OBS["Dashboard, linha do tempo<br/>e relatório"]

  U --> ORQ
  ORQ -- "plano.json" --> CLI
  CLI --> CORE
  CORE -- "qual via?" --> JEV
  JEV -- "via e julgamento" --> CORE
  CORE -- "abre tab e roda" --> HERDR
  HERDR --> W
  W -- "código no worktree" --> GIT
  CORE -- "gate, merge, fast-forward" --> GIT
  CORE -. "eventos JSONL" .-> OBS
  U -. "acompanha" .-> OBS
  U -. "acompanha" .-> HERDR
```

---

## 2. Componentes

Módulos de `meister/` agrupados por responsabilidade. As setas cheias são chamadas; as tracejadas,
leitura de dados.

```mermaid
flowchart TB
  subgraph IF["Interface"]
    CLI["cli.py<br/>comandos: orchestrate, worker, classify,<br/>control, plan, clean, report, timeline,<br/>dashboard, daemon, init, install-hooks"]
    HK["hooks.py + templates/<br/>hooks Git e Claude, guard"]
    PLG["herdr-plugin.toml<br/>ações do plugin"]
  end

  subgraph CFGG["Configuração"]
    CFG["config.py<br/>load_config e validate_config"]
    DCFG["default_config.yaml<br/>+ meister.config.yaml do projeto"]
  end

  subgraph PLAN["Plano"]
    PL["plan.py + plan_adapters/<br/>importa e valida o JSON canônico"]
    PA["plan_analysis.py"]
    DAG["herdr/dag.py<br/>TaskDAG e lotes independentes"]
  end

  subgraph CORE["Orquestração"]
    BR["herdr/bridge.py<br/>HerdrEventBridge: run_orchestration_cycle,<br/>execute_plan, execute_subtask"]
    ST["state.py<br/>StateManager: SQLite WAL, FSM,<br/>circuit breakers"]
  end

  subgraph ROUTE["Roteamento"]
    JEV["jev.py<br/>classify_task e control_cycle"]
    JC["jev_context.py<br/>contexto enviado ao Jev"]
  end

  subgraph EXEC["Execução dos workers"]
    WS["herdr/workers.py<br/>WorkerSpawner, tiers, detecção de cota"]
    WK["worker.py<br/>HarnessWorker, tabs do Herdr"]
    HC["herdr/client.py + events.py<br/>socket do Herdr"]
  end

  subgraph INTG["Integração"]
    WT["worktree.py<br/>WorktreeManager e IntegrationPipeline"]
    GT["gate.py<br/>DeterministicGate e cache"]
    ENV["env_setup.py<br/>dependências do worktree"]
  end

  subgraph TEL["Telemetria"]
    LG["logger.py<br/>orchestration_log.jsonl"]
    US["usage.py<br/>tokens, créditos, custo"]
    RP["report.py<br/>meister report"]
    DB["dashboard/<br/>server.py + metrics.py"]
    TL["timeline*.py + log_tail.py<br/>linha do tempo (Gantt)"]
    PG["progress.py<br/>progresso no terminal"]
  end

  EXT1["OpenRouter"]
  EXT2["Herdr"]
  EXT3["CLIs: copilot, codex, agy, claude"]
  EXT4[("Git")]

  CLI --> BR
  CLI --> PL
  CLI --> CFG
  DCFG --> CFG
  PL --> DAG
  CLI --> PA
  BR --> DAG
  BR --> ST
  BR --> JEV
  BR --> JC
  BR --> WS
  WS --> WK
  WK --> HC
  BR --> WT
  BR --> GT
  WT --> GT
  WT --> ENV
  GT --> ENV
  JEV --> EXT1
  HC --> EXT2
  WK --> EXT3
  WT --> EXT4
  BR --> LG
  WK --> LG
  JEV --> LG
  GT --> LG
  US --> LG
  LG -.-> RP
  LG -.-> DB
  LG -.-> TL
  LG -.-> PG
  PLG --> CLI
  CLI --> HK
```

---

## 3. Sequência de um `orchestrate`

Um plano com uma subtarefa, do comando até a `main`. Com várias subtarefas independentes, o bloco
"para cada subtarefa" roda em paralelo, e só o merge é serializado (trava de merge).

```mermaid
sequenceDiagram
  autonumber
  actor O as Orquestradora (você)
  participant C as CLI meister
  participant B as Bridge (orquestrador)
  participant S as Estado (SQLite)
  participant J as Jev (OpenRouter)
  participant H as Herdr
  participant W as Worker (CLI da assinatura)
  participant T as Worktrees e Git
  participant G as Gate determinístico

  O->>C: meister orchestrate --plan-file plano.json
  C->>C: carrega e valida a configuração (inválida: rc 2, nada é criado)
  C->>B: run_orchestration_cycle
  B->>B: load_plan, monta o DAG e os lotes independentes
  B->>S: run RUNNING, orchestration_start
  B->>T: cria branch e worktree de integração

  loop para cada lote de tarefas independentes
    par para cada subtarefa do lote
      B->>J: classify (router.mode = jev)
      J-->>B: classe e via recomendada
      Note over B: elegibilidade, cooldown, disjuntor e vaga por via
      B->>T: cria worktree e branch da subtarefa
      B->>H: abre tab visível e roda o worker
      H->>W: executa a tarefa no worktree
      W-->>H: termina
      H-->>B: resultado do pane
      B->>G: escopo e gate no worktree do worker
      G-->>B: aprovado
      B->>T: commit do worker
      Note over B,T: trava de merge: uma subtarefa por vez
      B->>T: merge --no-ff na branch de integração
      B->>G: gate de integração (pós-merge)
      G-->>B: aprovado (se falhar: rollback e subtarefa rejeitada)
      B->>S: subtarefa COMPLETED com integrated_sha
    end
  end

  B->>G: gate final na branch de integração
  B->>J: control (julga o resultado)
  J-->>B: COMPLETE
  B->>T: confere que cada subtarefa COMPLETED é ancestral da integração
  B->>T: fast-forward da main (único ponto que toca a main)
  B->>S: run COMPLETED, orchestration_end
  B->>T: limpa worktrees e branches
  B->>H: fecha as tabs
  B-->>C: concluído
  C-->>O: resumo no terminal
```

---

## 4. Escolha da via (roteamento)

`router.mode` decide quem escolhe a via inicial. A elegibilidade (`eligible_classes`) e o disjuntor podem
trocar a escolha; o resultado fica registrado em `route_decision`.

```mermaid
flowchart TD
  A["Subtarefa pronta"] --> B{"router.mode"}
  B -- "first" --> C["1ª via de workers.tier_order<br/>(sem rede)"]
  B -- "jev (padrão)" --> D{"Jev em cooldown?"}
  D -- "sim" --> C
  D -- "não" --> E["classify no Jev<br/>até router.max_attempts tentativas,<br/>timeout router.timeout_seconds"]
  E -- "falhou ou lento" --> F["marca cooldown<br/>router.unavailable_cooldown_seconds"]
  F --> C
  E -- "classe + via recomendada" --> G{"A via aceita a classe?<br/>eligible_classes"}
  G -- "sim" --> H["via escolhida"]
  G -- "não" --> I["troca pela via elegível mais próxima<br/>antes na lista, senão depois, senão mantém<br/>route_decision: ineligible_replaced"]
  I --> H
  C --> H
  H --> J{"Disjuntor da via aberto?"}
  J -- "sim" --> K["próxima via disponível<br/>tier_skipped_breaker"]
  J -- "não" --> L["espera vaga da via<br/>max_parallel"]
  K --> L
  L --> M["worker na tab do Herdr"]
```

---

## 5. Falhas, timeout e cota de um worker

O pane do worker pode terminar bem, esgotar a cota, sumir ou estourar o tempo. O Meister nunca assume
a implementação: ou integra o trabalho, ou tenta de novo, ou passa para a próxima via.

```mermaid
flowchart TD
  A["Worker rodando na tab"] --> B{"O que aconteceu?"}
  B -- "terminou" --> OK["resultado recebido"]
  B -- "cota ou rate limit (429)" --> Q["abre o disjuntor da via<br/>quota_error"]
  Q --> NX["próxima via disponível de tier_order"]
  NX --> A
  B -- "pane sumiu (detectado em ~5 s)" --> PL{"tentativas de pane_lost<br/>disponíveis?"}
  PL -- "sim" --> RT["worker_retry na mesma via"]
  RT --> A
  PL -- "não" --> INF["erro de infraestrutura<br/>sem trocar de via"]
  B -- "estourou o tempo" --> TO["interrompe o pane<br/>worker_timeout"]
  TO --> RES{"há resultado ou<br/>mudanças no worktree?"}
  RES -- "sim" --> SAL["trabalho salvaguardado<br/>worker_timeout_salvaged"]
  RES -- "não" --> RTO{"tentativas restantes?"}
  RTO -- "sim" --> RT
  RTO -- "não" --> ESC{"há próxima via?"}
  ESC -- "sim" --> NX
  ESC -- "não" --> FAIL["subtarefa FAILED"]
  OK --> GATE["gate e integração"]
  SAL --> GATE
  GATE -- "reprovado (gate, escopo, merge)<br/>ou worker com erro" --> REJ["subtask_rejected<br/>subtarefa FAILED, sem trocar de via"]
  REJ --> RUNF
  GATE -- "aprovado" --> DONE["subtask_completed"]
  INF --> RUNF["run FAILED<br/>rodar o mesmo comando retoma"]
  FAIL --> RUNF
```

---

## 6. Integração e gates

O **gate** é a verificação determinística (por padrão `ruff` e `pytest`, ou os comandos de `gate.commands`).
Roda em três momentos. Um gate aprovado é guardado em cache pelo conteúdo do código e dos comandos
(`gate.cache`), então uma verificação idêntica não se repete.

```mermaid
flowchart TD
  W["Worker termina no worktree da subtarefa"] --> S{"Escopo: só mexeu nos<br/>target_files?"}
  S -- "não" --> R1["reprovada: violação de escopo"]
  S -- "sim" --> G1["Gate 1: no worktree do worker<br/>(em paralelo entre subtarefas)"]
  G1 -- "falhou" --> R2["reprovada: portão falhou"]
  G1 -- "ok" --> CM["commit do worker"]
  CM --> LK["trava de merge<br/>(uma subtarefa por vez)"]
  LK --> MG["merge --no-ff na branch<br/>meister/integration/run"]
  MG -- "conflito" --> R3["reprovada: falha no merge"]
  MG -- "ok" --> G2["Gate 2: no worktree de integração<br/>(serial, sob a trava)"]
  G2 -- "falhou" --> RB["rollback do merge<br/>reprovada"]
  G2 -- "ok" --> CP["COMPLETED + integrated_sha"]
  CP --> MORE{"mais subtarefas?"}
  MORE -- "sim" --> W
  MORE -- "não" --> G3["Gate 3: final, na branch de integração"]
  G3 -- "falhou" --> RF["run FAILED<br/>main intacta"]
  G3 -- "ok" --> JC["Jev control: COMPLETE?"]
  JC -- "não" --> RF
  JC -- "sim" --> AN["todas as subtarefas COMPLETED<br/>são ancestrais da integração?"]
  AN -- "não" --> RF
  AN -- "sim" --> FF["fast-forward da main"]

  CACHE[("cache do gate<br/>árvore Git + hash dos comandos,<br/>só aprovações")]
  G1 -.-> CACHE
  G2 -.-> CACHE
  G3 -.-> CACHE
```

---

## 7. Estados do run e da subtarefa

Máquinas de estado de `meister/state.py` (transições inválidas lançam erro). Um run `FAILED` ou
`RUNNING` volta a `RUNNING` ao rodar o mesmo plano: as subtarefas já `COMPLETED` são puladas.

```mermaid
stateDiagram-v2
  direction LR
  state "Run" as RUN {
    [*] --> PENDING
    PENDING --> RUNNING
    PENDING --> CANCELLED
    RUNNING --> COMPLETED
    RUNNING --> FAILED
    RUNNING --> CANCELLED
    FAILED --> RUNNING: retomada
    FAILED --> PENDING
    FAILED --> CANCELLED
    COMPLETED --> RUNNING
    CANCELLED --> RUNNING
    CANCELLED --> PENDING
  }
```

```mermaid
stateDiagram-v2
  direction LR
  state "Subtarefa" as SUB {
    [*] --> PENDING
    PENDING --> RUNNING
    PENDING --> FAILED
    RUNNING --> COMPLETED
    RUNNING --> FAILED
    RUNNING --> RETRYING
    RETRYING --> RUNNING
    RETRYING --> FAILED
    FAILED --> PENDING: retomada
    FAILED --> RUNNING
    FAILED --> RETRYING
    COMPLETED --> RUNNING
    COMPLETED --> PENDING
  }
```

---

## 8. Worktrees e branches

Cada subtarefa trabalha num worktree próprio; o merge acontece numa branch de integração e a `main` só
avança por fast-forward no fim, depois de todos os gates.

```mermaid
gitGraph
  commit id: "main (base)"
  branch meister/integration/run
  checkout main
  branch meister/worktree/tarefa-1
  checkout meister/worktree/tarefa-1
  commit id: "worker 1"
  checkout main
  branch meister/worktree/tarefa-2
  checkout meister/worktree/tarefa-2
  commit id: "worker 2"
  checkout meister/integration/run
  merge meister/worktree/tarefa-1 id: "merge --no-ff + gate"
  merge meister/worktree/tarefa-2 id: "merge --no-ff + gate 2"
  checkout main
  merge meister/integration/run id: "fast-forward"
```

Notas:
- Trabalho não integrado de uma subtarefa descartada fica em `refs/meister/archive/<tarefa>-<data>` e
  é removido pelo Meister depois de 7 dias. `meister clean` apaga as branches antigas.
- O fast-forward é o único passo que toca a `main`. Se qualquer gate falhar, a `main` continua intacta.

---

## 9. Telemetria e visualização

Tudo é gravado como eventos no `orchestration_log.jsonl` (em `.meister/logs/` do projeto, ou em
`MEISTER_LOG_DIR`). As telas só leem esse arquivo: não alteram nada.

```mermaid
flowchart LR
  subgraph PROD["Quem grava"]
    P1["bridge e worker<br/>worker_spawn, worker_phase,<br/>subtask_*, orchestration_*"]
    P2["Jev<br/>classify, control, route_decision"]
    P3["gate e worktree<br/>fase gate: cached, saved_seconds"]
    P4["usage.py<br/>tokens, créditos,<br/>custo reportado ou estimado"]
  end
  LOG[("orchestration_log.jsonl<br/>current_run.json")]
  subgraph CONS["Quem lê"]
    D["meister dashboard<br/>web em localhost:5050<br/>e TUI (--tui)"]
    TLN["meister timeline<br/>Gantt: worker, gate, integração,<br/>fila, espera, linha do Jev"]
    RPT["meister report<br/>custo, tempo, tentativas"]
    PRG["progresso no terminal<br/>do orchestrate"]
  end
  CFGY["config: tier_order, credit_usd,<br/>cost_per_m_tokens"]

  P1 --> LOG
  P2 --> LOG
  P3 --> LOG
  P4 --> LOG
  LOG -.-> D
  LOG -.-> TLN
  LOG -.-> RPT
  LOG -.-> PRG
  CFGY -.-> RPT
  CFGY -.-> D
  CFGY -.-> TLN
```

**Dentro da linha do tempo** (`meister timeline`), três camadas separadas:

```mermaid
flowchart LR
  LT["log_tail.py<br/>leitura incremental,<br/>somente leitura"] --> M["timeline.py<br/>modelo puro:<br/>eventos para Timeline"]
  M --> V["timeline_view.py<br/>desenho puro:<br/>Timeline para linhas ANSI"]
  V --> A["timeline_app.py<br/>teclado, zoom, pausa,<br/>modo ao vivo e replay"]
  CLI["timeline_cli.py<br/>--once, --all, --run-id"] --> M
  CLI --> V
```

---

## 10. Guard do Claude Code

Hook `PreToolUse` instalado por `meister init --hooks` (ou `install-hooks --claude`). Impede que a LLM
orquestradora escreva código direto: nesses projetos quem implementa são os workers.

```mermaid
flowchart TD
  A["Claude vai usar Edit, Write,<br/>MultiEdit ou NotebookEdit"] --> B{"Variável de bypass?<br/>MEISTER_IN_PANE=1<br/>MEISTER_ALLOW_ORCHESTRATOR_EDIT=1<br/>ou .meister/allow_orchestrator"}
  B -- "sim" --> OK["libera"]
  B -- "não" --> C{"Consegue ler a chamada?"}
  C -- "não" --> BL["bloqueia (falha fechada)"]
  C -- "sim" --> D{"Caminho é documentação?<br/>docs/**, *.md, *.mdx, *.txt<br/>sem componente .."}
  D -- "sim" --> OK
  D -- "não" --> E{"Modo em .meister/guard_mode"}
  E -- "block (padrão)" --> BL2["recusa e orienta:<br/>use meister orchestrate"]
  E -- "ask" --> AS["pede a sua confirmação"]
  E -- "off" --> OK
```

---

## 11. Instalação e integração com o Herdr

O Herdr é pré-requisito. O instalador chama o `meister setup`, que faz o resto e é seguro rodar de novo.

```mermaid
flowchart TD
  H["Herdr instalado<br/>(pré-requisito)"] --> I1
  subgraph INS["Uma vez por máquina"]
    I1["bin/install.sh<br/>aborta se não achar o herdr"] --> I2["clona em ~/.local/share/meisterrouter<br/>cria .venv e instala o pacote"]
    I2 --> I3["symlink ~/.local/bin/meister"]
    I3 --> I4["meister setup"]
    I4 --> S1["herdr plugin link<br/>só se o plugin ainda não estiver ligado"]
    I4 --> S2["atalhos: bloco gerenciado no config.toml<br/>backup, herdr config check, reload;<br/>nunca sobrescreve atalhos seus"]
    I4 --> S3["diagnóstico: Python, daemon, CLIs dos workers,<br/>chave do Jev e configuração"]
    I7["OPENROUTER_API_KEY<br/>(só o Jev usa)"]
  end
  subgraph PRJ["Uma vez por projeto"]
    P1["meister setup --project"] --> P2["CLAUDE.md, CODEX.md, AGENTS.md<br/>.meister/logs"]
    P1 --> P3["hook Git pre-commit<br/>hooks do Claude e guard em .claude/"]
    P4["meister config init<br/>(opcional): vias e ordem"]
  end
  subgraph USO["No dia a dia (Herdr)"]
    U1["prefix+m<br/>orquestrar"]
    U2["prefix+shift+m<br/>dashboard em popup"]
    U3["prefix+t<br/>linha do tempo em popup"]
    U4["tabs dos workers<br/>visíveis em tempo real"]
  end
  S1 --> PRJ
  S2 --> USO
  PRJ --> USO
```
