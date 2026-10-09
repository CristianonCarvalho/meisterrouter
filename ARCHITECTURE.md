# MeisterRouter — Especificação Arquitetural e Máquina de Estados

## 1. Visão Geral
O **MeisterRouter** é uma camada de desacoplamento entre:
1. **Orquestrador (arquiteto):** a LLM de planejamento escolhida pelo usuário (por exemplo, Claude Code ou Codex). Escreve o plano e dispara o Meister.
2. **Decisões tipadas (state machine):** TypeSafe Jev Decisions API (`typesafe/jev-1.13`), via OpenRouter, usada só para classificar e controlar.
3. **Workers (implementadores):** as vias definidas em `workers.tier_order` (`tier_1`, `tier_1b`, `tier_2`...). Cada via combina um harness (`copilot`, `codex`, `agy`, `claude`), um modelo e um preço, todos na configuração. Os modelos atuais e seus custos estão em [docs/models-and-costs.md](docs/models-and-costs.md) (pt-BR: [docs/pt-BR/MODELOS_E_CUSTOS.md](docs/pt-BR/MODELOS_E_CUSTOS.md)) e em `meister models`.

Ele resolve o problema fundamental de LLMs: **alucinação em decisões de controle, estimativa de complexidade inflada e desperdício financeiro em tarefas triviais**.

```mermaid
flowchart TD
    subgraph Orquestradores["1. Orquestrador (arquiteto)"]
        Orchestrator["LLM orquestradora<br/>(escolhida pelo usuário)"]
    end

    subgraph JevDecision["2. Decision Machine (TypeSafe Jev-1.13)"]
        JevClassify["meister classify"]
        JevControl["meister control"]
    end

    subgraph Workers["3. Workers (vias de workers.tier_order)"]
        Cheap["Via de menor custo<br/>(ex.: tier_1)<br/>Workers Small/Medium"]
        Deep["Via de raciocínio profundo<br/>(ex.: tier_2)<br/>Hard code"]
        Max["Via de escalonamento máximo<br/>(ex.: tier_3, eligible_classes: ESCALATE)"]
    end

    subgraph Verification["4. Verificação Determinística"]
        Tests["Test Runners (Vitest, Pytest, Cargo)"]
        Types["TypeCheck (tsc, mypy)"]
        Linter["Linters (ESLint, Ruff)"]
    end

    Orquestradores -->|Contexto da Tarefa| JevClassify
    JevClassify -->|Sugerir Nível & Modelo| Orquestradores
    Orquestradores -->|Despacha Subagente(s)| Workers
    Workers -->|Código Gerado| Verification
    Verification -->|Evidências (Pass/Fail + Diff)| JevControl
    JevControl -->|COMPLETE| Commit["Git Commit & Sucesso"]
    JevControl -->|RETRY / Switch Worker| Workers
    JevControl -->|ESCALATE| Orquestradores
```

## 2. A Máquina de Estados do Jev (`typesafe/jev-1.13`)

O Jev opera sem texto livre (sem geração conversacional), aceitando apenas esquemas JSON com perguntas tipadas (`choice`, `score`, `noul`) e retornando distribuições de probabilidade.

### Estados de Classificação (`classify`)
- **`SMALL`**: Edição pontual de 1-2 arquivos, correção de CSS, adição de caso de teste ou prop de componente.  
  *Implementador Alocado:* via de menor custo (ex.: `tier_1`).
- **`MEDIUM`**: Criação de novo componente, integração de store, ajuste de rotas ou testes de integração.  
  *Implementador Alocado:* via de menor custo ou intermediária (ex.: `tier_1b` ou `tier_2`).
- **`HIGH`**: Reestruturação de banco de dados, migração de estado complexa, algoritmos concorrentes ou matemáticos.  
  *Implementador Alocado:* via de raciocínio profundo (ex.: `tier_2`).
- **`ESCALATE`**: Incerteza arquitetural ou regressões cíclicas.  
  *Implementador Alocado:* via de escalonamento máximo (ex.: `tier_3`, com `eligible_classes: [ESCALATE]`).

### Estados de Controle (`control`)
- **`COMPLETE`**: Testes passam 100%, sem erros de tipo ou linter, diff coerente com a intenção.
- **`RETRY`**: Falha recuperável com o mesmo implementador.
- **`switch_implementer: true`**: Quando a via de menor custo falha e deve ser substituída pela via de raciocínio profundo.
- **`should_escalate: true`**: Quando a complexidade da tarefa foi subestimada e exige uma via de escalonamento máximo (ex.: `tier_3`).

## 3. Worker Execution Engine & Hierarquia de Fallback

O MeisterRouter implementa o módulo `meister.worker` (`NativeWorker`), eliminando a necessidade de implementações manuais pelo modelo arquiteto:

```
[Invocação Worker]
       │
       ▼
1. Primeira via habilitada da cadeia (`workers.tier_order`, ex.: `tier_1`)
       │  (se indisponível, timeout ou erro 400/404/rate-limit)
       ▼
2. Próxima via habilitada (ex.: `tier_1b`)
       │  (se indisponível ou falha)
       ▼
3. Próxima via habilitada (ex.: `tier_2`)
       │  (se indisponível)
       ▼
4. Vias com `eligible_classes: [ESCALATE]` (ex.: `tier_3`)
```

A ordem de `workers.tier_order` é a única cadeia de fallback.

### Regra Estrita: Zero Implementação Direta pelo Orquestrador
- **Anti-pattern:** A LLM orquestradora (ex.: no Claude Code ou no Codex) propor: *"Como a via falhou, eu mesmo implemento o código agora"*. Isso anula o ganho de custo da cadeia de workers.
- **Pattern Correto:** O arquiteto delega imediatamente à próxima via da cadeia via `meister worker --model tier_2` (ou o nome da via seguinte). A implementação direta pelo arquiteto só é permitida se **todas** as vias de workers estiverem comprovadamente inacessíveis.

## 4. Custos e Eficiência

Preços e modelos não fazem parte desta especificação: vivem na configuração (`meister/default_config.yaml` e `meister.config.yaml`). Para ver a cadeia ativa e o custo por milhão de tokens de cada via, rode `meister models`. Para o estudo comparativo, veja [docs/models-and-costs.md](docs/models-and-costs.md) (pt-BR: [docs/pt-BR/MODELOS_E_CUSTOS.md](docs/pt-BR/MODELOS_E_CUSTOS.md)). Os custos medidos de cada run aparecem em `meister report` e no dashboard.
