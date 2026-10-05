> Se você é um worker do MeisterRouter (`MEISTER_IN_PANE=1`) ou está implementando uma
> tarefa que lhe foi passada: ignore as regras de delegação abaixo e implemente diretamente;
> não rode `meister classify`, `meister worker`, `meister orchestrate` nem `meister control`.

# Claude Code → MeisterRouter — Orquestração Multi-Modelo Autônoma

Este projeto utiliza o **MeisterRouter** para orquestração multi-modelo orientada a custo mínimo e máxima confiabilidade determinística.

## 01. Princípio Fundamental
Para toda tarefa de engenharia:
1. Atuar como arquiteto e orquestrador.
2. Usar o modelo e nível de raciocínio mínimos necessários.
3. Decisões de controle e classificação NUNCA são tomadas no achômetro: são governadas pelo **TypeSafe Jev Decisions API** (`typesafe/jev-1.13`).
4. Evidência determinística (testes, tipagem, linter, diff) precede qualquer asserção de conclusão.

## 02. Catálogo e Papéis dos Modelos

| Modelo | Papel Principal | Harness / Executável | Custo / 1M tokens |
|---|---|---|---|
| **GitHub Copilot CLI** | **Primeira via padrão de implementação** | Copilot CLI (`copilot`) | $0.20 |
| **GPT-6 Luna (medium)** | **Via Codex de baixo custo** | Codex CLI (`codex`) | **$0.20** |
| **Gemini 3.8 Flash (high)** | **Raciocínio Profundo / Escalonamento** | Antigravity CLI (`agy`) | $1.50 (Líder em Código Difícil; assinatura subsidiada: o custo efetivo é menor) |
| **Claude Sonnet 5.5 (high)** | **Arquiteto / Planejador** | Claude CLI (`claude`) | $4.00 |
| **Claude Opus 5.5** | **Escalonamento Máximo de Raciocínio** | Claude CLI (`claude`) | $8.00 |
| **TypeSafe Jev-1.13** | **Juiz de Máquina de Estados / Router** | OpenRouter (Somente Decisões) | $0.042 (só entrada; saída grátis) |

> 💲 **Custos:** preço combinado 3:1 (3 partes de entrada para 1 de saída) por 1M tokens, conferido no OpenRouter e no Artificial Analysis em 2026-10-05. O esforço de raciocínio não muda o preço por token, só quantos tokens o modelo gasta. Custo por tarefa no Artificial Analysis (benchmark de raciocínio, não o nosso trabalho de código): Sonnet 5.5 high US$ 1,12, Gemini 3.8 Flash high US$ 1,24, Luna high US$ 0,03. O Gemini via `agy` é coberto por assinatura subsidiada, por isso permanece como via de escalonamento.

> 🔒 **ISOLAMENTO OPENROUTER:** A API OpenRouter é utilizada EXCLUSIVAMENTE pelo JEV para decisões determinísticas (`classify` e `control`). Os workers NUNCA consomem tokens no OpenRouter; executam através dos respectivos harnesses instalados (`codex`, `agy`, `claude`, `copilot`).
> O catálogo efetivo vive em `meister/default_config.yaml` / `meister.config.yaml`.
> `workers.tier_order[].max_parallel` limita quantas subtarefas começam por via; fallback posterior não transfere a reserva.
> O roteador padrão é `jev`; use `router: {mode: first}` para sempre escolher a primeira via sem rede.
> `router.timeout_seconds` (10), `router.max_attempts` (2) e `router.unavailable_cooldown_seconds` (300)
> limitam a espera pelo Jev e o intervalo de resfriamento após uma falha.

## 03. Fluxo de Trabalho Obrigatório do Agente

Com `router.mode: jev` (padrão), o `orchestrate` usa o Jev para escolher a via inicial de cada subtarefa.

### Passo 1: Classificação Inicial (`classify`)
Antes de implementar ou criar subagentes, rodar:
```bash
./bin/meister classify --context "<descrição clara da solicitação do usuário>"
```
O Jev retornará em JSON:
- `classification`: `SMALL` | `MEDIUM` | `HIGH` | `ESCALATE`
- `recommended_implementer`: um nome de via configurado em `workers.tier_order`

### Passo 2: Execução e Delegação Obrigatória
Como orquestrador, invoque o implementador recomendado via CLI do MeisterRouter:
```bash
# 1. Execução direta pelo worker recomendado:
meister worker --model copilot_luna --task "<tarefa>" [--files "<arquivos_separados_por_virgula>"]

# 2. SE o modelo recomendado falhar ou estiver inativo, ESCALE IMEDIATAMENTE para o próximo:
meister worker --model codex_luna --task "<tarefa>" [--files "<arquivos>"]

# 3. Ou delegar o ciclo completo ao orquestrador autônomo:
meister orchestrate --task "<tarefa>"
```

> ⚠️ **REGRA CRÍTICA: ZERO IMPLEMENTAÇÃO DIRETA PELO ORQUESTRADOR!**
> Se o modelo recomendado (ex: **Luna**) não estiver ativo, acessível ou falhar:
> 1. **NÃO assuma a implementação como orquestrador (Sonnet 5).**
> 2. **Repasse IMEDIATAMENTE para o próximo da cadeia:** delegue para a próxima via de `tier_order`.
> 3. Continue pela ordem de `tier_order`; ela é a única cadeia de fallback.
> 4. O orquestrador só escreve código diretamente se TODOS os workers da cadeia estiverem comprovadamente inacessíveis.

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

## 05. Fluxo de Trabalho com Planos Estruturados (superpowers → orchestrate)

Use este fluxo para tarefas de múltiplos passos geradas pela skill `superpowers:writing-plans`:

### 1. Escrever o plano com `superpowers:writing-plans`
A skill gera um arquivo em `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`.

### 2. Converter para JSON canônico
```bash
meister plan import --format superpowers docs/superpowers/plans/YYYY-MM-DD-<feature>.md \
  -o plano.json
```
O comando imprime a tabela de tarefas (id, arquivos, dependências) para revisão humana.
Opcionalmente adicione a linha `**Depends on:** Task 2, Task 5` em tarefas no plano para
controlar dependências explicitamente.

### 3. Revisar o DAG
Confirme que ids, arquivos e dependências estão corretos antes de executar.

### 4. Executar com orchestrate
```bash
meister orchestrate --plan-file plano.json
```
> ⚠️ **Use `meister orchestrate`** — não os executores internos do superpowers
> (`subagent-driven-development`, `executing-plans`) nesta etapa. O `orchestrate`
> integra commits do worker, gerencia worktrees e aciona o evidence gate.

### 5. Validar um plano JSON existente
```bash
meister plan validate plano.json
```

### Notas sobre formatos legados
O flag `--allow-freeform` permite usar texto livre/lista markdown com `orchestrate`
(comportamento pré-contrato, sem validação de esquema):
```bash
meister orchestrate --task "lista de tarefas livre" --allow-freeform
```
Não use em produção — não valida dependências, tipos ou chaves proibidas.
Tarefas sem `target_files` (ex.: `--allow-unscoped` ou `--allow-freeform`) rodam isoladas, sem paralelismo, por não haver como saber se conflitam.
