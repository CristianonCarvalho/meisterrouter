# MeisterRouter — Especificação Arquitetural e Máquina de Estados

## 1. Visão Geral
O **MeisterRouter** é uma camada de desacoplamento entre:
1. **Modelos de Arquitetura & Raciocínio (Orquestradores):** Claude Sonnet 5, OpenAI Codex, GPT-4o.
2. **Modelos de Decisão Tipada (State Machines):** TypeSafe Jev Decisions API (`typesafe/jev-1.13`).
3. **Modelos de Execução & Implementação (Workers):** GPT-6 Luna, Gemini 3.8 Flash, Claude 4.5 Haiku.

Ele resolve o problema fundamental de LLMs: **alucinação em decisões de controle, estimativa de complexidade inflada e desperdício financeiro em tarefas triviais**.

```mermaid
flowchart TD
    subgraph Orquestradores["1. Orquestradores (Architects)"]
        ClaudeCode["Claude Code (Sonnet 5)"]
        OpenAICodex["OpenAI Codex / GPT-4o"]
    end

    subgraph JevDecision["2. Decision Machine (TypeSafe Jev-1.13)"]
        JevClassify["meister classify"]
        JevControl["meister control"]
    end

    subgraph Workers["3. Subagentes Workers (Implementers)"]
        Luna["🌙 GPT-6 Luna ($0.20/M)<br/>Workers Small/Medium"]
        Haiku["⚡ Claude 4.5 Haiku ($0.77/M)<br/>Workers Rápidos"]
        Gemini["✨ Gemini 3.8 Flash ($1.50/M)<br/>Deep Reasoning & Hard Code"]
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
  *Implementador Alocado:* **GPT-6 Luna** (Custo: $0.02/tarefa).
- **`MEDIUM`**: Criação de novo componente, integração de store, ajuste de rotas ou testes de integração.  
  *Implementador Alocado:* **GPT-6 Luna** ou **Claude 4.5 Haiku**.
- **`HIGH`**: Reestruturação de banco de dados, migração de estado complexa, algoritmos concorrentes ou matemáticos.  
  *Implementador Alocado:* **Gemini 3.8 Flash** ou **Claude Sonnet 5**.
- **`ESCALATE`**: Incerteza arquitetural ou regressões cíclicas.  
  *Implementador Alocado:* **Gemini 3.8 Flash** ou **Claude Opus 5.5**.

### Estados de Controle (`control`)
- **`COMPLETE`**: Testes passam 100%, sem erros de tipo ou linter, diff coerente com a intenção.
- **`RETRY`**: Falha recuperável com o mesmo implementador.
- **`switch_implementer: true`**: Quando o implementador de menor custo (ex: Luna/Haiku) falha e deve ser substituído pelo modelo de raciocínio profundo (**Gemini 3.8 Flash**).
- **`should_escalate: true`**: Quando a complexidade da tarefa foi subestimada e exige raciocínio de nível superior (Sonnet Alto ou Opus).

## 3. Worker Execution Engine & Hierarquia de Fallback

O MeisterRouter implementa o módulo `meister.worker` (`NativeWorker`), eliminando a necessidade de implementações manuais pelo modelo arquiteto:

```
[Invocação Worker]
       │
       ▼
1. Worker Primário: GPT-6 Luna ($0.20/M tokens)
       │  (se indisponível, timeout ou erro 400/404/rate-limit)
       ▼
2. Escalonamento Imediato: Gemini 3.8 Flash ($1.50/M tokens)
       │  (se indisponível ou falha)
       ▼
3. Worker Secundário: Claude 4.5 Haiku ($0.77/M tokens)
       │  (se indisponível)
       ▼
4. Escalonamento Máximo: Claude Sonnet 5 / Claude Opus 5.5
```

### Regra Estrita: Zero Implementação Direta pelo Orquestrador
- **Anti-pattern:** O modelo arquiteto (ex.: Sonnet 5 no Claude Code ou Codex no terminal) propor: *"Como Luna falhou, eu mesmo implemento o código agora"*. Isso destrói o ganho de custo-eficiência de 93%.
- **Pattern Correto:** O arquiteto delega imediatamente ao próximo modelo da cadeia via `meister worker --model gemini_flash` ou `meister worker --model haiku`. A implementação direta pelo arquiteto só é permitida se **todos** os tiers de workers estiverem comprovadamente inacessíveis.

## 4. Matriz Econômica e Eficiência

Comparativo de execução para 500 tarefas mensais:

| Arquitetura | Custo Estimado | Índice Médio de Inteligência |
|---|---|---|
| **Tradicional Single-Model (Sonnet 5)** | ~$500.00 - $750.00 / mês | 28 |
| **Tradicional Single-Model (Codex GPT-4o)** | ~$600.00 - $800.00 / mês | 30 |
| **MeisterRouter (Jev + Luna + Flash)** | **~$25.00 - $45.00 / mês** | **29 - 40** (Superior) |
| **Resultado** | **Economia de ~93%** | **Qualidade Superior** |
