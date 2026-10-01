# Fluxo do MeisterRouter

Visão completa do processo, do plano à `main`. Gerado em 2026-10-01 a partir do código mesclado até o PR #17.

```mermaid
flowchart TD
  subgraph PLAN["1. Planejamento"]
    A["Arquiteto: LLM que planeja<br/>(superpowers: writing-plans)"] --> B["plano.md"]
    B --> C["meister plan import<br/>adaptador e validação de esquema"]
    C --> D[("plano.json canônico<br/>id, target_files, depends_on")]
  end

  subgraph CFG["Configuração"]
    CF1["default_config.yaml<br/>(empacotado)"] --> CF3["load_config e validate_config"]
    CF2["meister.config.yaml<br/>(projeto, opcional)"] --> CF3
  end

  D --> O["meister orchestrate --plan-file"]
  CF3 --> O
  O --> V{"Config válida?"}
  V -- "não" --> X["rc 2: nada é criado"]
  V -- "sim" --> S[("SQLite: run, subtarefas,<br/>disjuntores")]
  S --> G["DAG: lotes de tarefas independentes<br/>mesmo arquivo ou sem escopo = em sequência"]

  subgraph SUB["2. Cada subtarefa do lote, em paralelo"]
    G --> R{"router.mode"}
    R -- "jev (padrão)" --> J["Jev no OpenRouter escolhe a via inicial"]
    J -- "falha ou lento" --> CD["cooldown: usa a 1ª via"]
    R -- "first" --> T1["1ª via da lista"]
    J --> SL
    CD --> SL
    T1 --> SL["Vaga por via (max_parallel)<br/>e disjuntor, sempre para a frente"]
    SL --> W["Worktree próprio"]
    W --> HT["Worker em tab visível do Herdr<br/>copilot, codex, agy ou claude"]
    HT --> WAIT{"Resultado"}
    WAIT -- "cota 429" --> FB["Abre o disjuntor<br/>e tenta a próxima via"]
    FB --> SL
    WAIT -- "pane sumiu (5 s)" --> ERR["Erro de infraestrutura<br/>sem trocar de via"]
    WAIT -- "concluído" --> GATE
  end

  subgraph INT["3. Integração determinística"]
    GATE["Gate no worktree:<br/>escopo e testes"] --> CM["Commit do worker"]
    CM --> MG["merge --no-ff na branch<br/>de integração"]
    MG --> IG{"Gate de integração"}
    IG -- "falhou" --> RB["Rollback: subtarefa rejeitada"]
    IG -- "ok" --> DONE["COMPLETED + integrated_sha"]
  end

  ERR --> FAIL["Run FAILED<br/>rodar o mesmo comando retoma"]
  RB --> FAIL
  DONE --> NEXT{"Há mais lotes?"}
  NEXT -- "sim" --> G
  NEXT -- "não" --> JC["Jev control<br/>julga o resultado"]
  JC --> FF["Fast-forward da main"]
  FF --> CL["Limpeza: worktrees,<br/>branches e tabs"]

  subgraph OBS["Observabilidade (plugin Herdr)"]
    LOG[("JSONL de eventos")]
    DASH["Dashboard e TUI"]
    DMN["Daemon: eventos do Herdr"]
  end
  O -.-> LOG
  HT -.-> LOG
  LOG -.-> DASH
  DMN -.-> HT
```

## Como ler

- **1. Planejamento:** O plano vira JSON validado, e a configuração é verificada antes de qualquer coisa. Configuração inválida encerra com rc 2 sem criar nada.
- **2. Cada subtarefa:** O Jev (ou a primeira via) escolhe a via inicial. Há uma vaga por via e um worker numa tab visível do Herdr. Cota esgotada abre o disjuntor e passa para a próxima via; um pane que sumiu é detectado em até 5 s.
- **3. Integração:** Nada entra na main sem passar pelos gates. Falha em qualquer etapa deixa a main intacta, e rodar o mesmo comando retoma de onde parou.

## Papéis

| Papel | Quem é | Faz |
|---|---|---|
| Arquiteto | LLM que planeja (hoje a sessão do Claude Code) | Gera o plano |
| Orquestrador | Código Python (`meister orchestrate`), sem LLM | Fila, worktrees, merge, retries, cota |
| Jev | Modelo no OpenRouter | Escolhe a via inicial (`classify`) e julga o resultado (`control`) |
| Workers | `copilot`, `codex`, `agy`, `claude` | Implementam as subtarefas |
