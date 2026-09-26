# Claude Code → MeisterRouter — Orquestração Multi-Modelo Autônoma

Este projeto utiliza o **MeisterRouter** para orquestração multi-modelo orientada a custo mínimo e máxima confiabilidade determinística.

## 01. Princípio Fundamental
Para toda tarefa de engenharia:
1. Atuar como arquiteto e orquestrador.
2. Usar o modelo e nível de raciocínio mínimos necessários.
3. Decisões de controle e classificação NUNCA são tomadas no achômetro: são governadas pelo **TypeSafe Jev Decisions API** (`typesafe/jev-1.13`).
4. Evidência determinística (testes, tipagem, linter, diff) precede qualquer asserção de conclusão.

## 02. Catálogo e Papéis dos Modelos

| Modelo | Papel Principal | Benchmark Artificial Analysis | Custo / 1M tokens |
|---|---|---|---|
| **GPT-6 Luna (medium)** | **Implementador Primário (Worker)** | Intelligence: 29 \| Automation: 40% | **$0.077** (Líder Absoluto de Custo) |
| **Claude 4.5 Haiku** | **Implementador Secundário** | Intelligence: 17 \| Automation: 3% | $0.77 |
| **Gemini 3.8 Flash (medium)** | **Raciocínio Profundo / Escalonamento** | Intelligence: 40 \| Terminal-Bench: 20% | $0.5775 (Líder em Código Difícil) |
| **Claude Sonnet 5** | **Arquiteto / Orquestrador Geral** | Intelligence: 28 \| SciCode: 52% | $1.54 |
| **Claude Opus 5.5** | **Escalonamento Máximo de Raciocínio** | Intelligence: 35 | $15.00 |
| **TypeSafe Jev-1.13** | **Juiz de Máquina de Estados / Router** | Modelo Tipado de Decisão | $0.50 |

## 03. Fluxo de Trabalho Obrigatório do Agente

### Passo 1: Classificação Inicial (`classify`)
Antes de implementar ou criar subagentes, rodar:
```bash
./bin/meister classify --context "<descrição clara da solicitação do usuário>"
```
O Jev retornará em JSON:
- `classification`: `SMALL` | `MEDIUM` | `HIGH` | `ESCALATE`
- `recommended_implementer`: `luna` | `haiku` | `gemini_flash` | `sonnet`

### Passo 2: Execução
- **SMALL / MEDIUM:** Implementar via subagente de baixo custo (**GPT-6 Luna** ou **Haiku 4.5**).
- **HIGH:** Implementar via **Gemini 3.8 Flash** ou **Sonnet 5**.
- **Paralelização:** Subtarefas independentes devem ser despachadas em terminais paralelos.

### Passo 3: Verificação Determinística
Rodar compilação, suíte de testes e linter:
```bash
pytest tests/
git diff
```

### Passo 4: Julgamento e Controle (`control`)
Após coletar as evidências determinísticas:
```bash
./bin/meister control --diff-summary "<resumo das alterações>" --test-result pass|fail [--attempts N]
```

## 04. Telemetria e Dashboard
```bash
./bin/meister dashboard
# Abre http://localhost:5050
```
