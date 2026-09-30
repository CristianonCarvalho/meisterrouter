# Suíte de Testes End-to-End (E2E) Reais

Esta pasta contém scripts de testes E2E para o **MeisterRouter**, executando fluxos reais com o multiplexador **Herdr** e workers de modelos locais (**Codex** e **Antigravity**).

> [!WARNING]
> **Estes testes NÃO rodam no CI.** Eles exigem ambiente interativo real com o Herdr em execução, credenciais para workers e ferramentas locais instaladas. No CI, roda apenas o teste-guarda em `tests/test_e2e_scripts.py`, que valida a sintaxe e a integridade destes scripts sem executar o Herdr.

> [!NOTE]
> **Interação com abas do Herdr:** Os scripts abrem abas e painéis temporários no Herdr durante a execução das tarefas e fecham automaticamente ao final apenas as abas `worker:*` que eles próprios criaram, preservando abas preexistentes do usuário.

---

## Pré-requisitos

1. **Herdr:** Sessão ativa no Herdr com `HERDR_ENV=1` e o comando `herdr` disponível no `PATH`.
2. **Workers de IA:**
   - `codex` CLI (para o tier Luna).
   - `agy` CLI (para o tier Gemini Flash).
3. **Ferramentas de sistema:** `git` (com branch inicial `main`) e `python3` (com ambiente virtual contendo as dependências de desenvolvimento do projeto).

---

## Scripts Disponíveis, Objetivos e Tempos Estimados

Todos os repositórios descartáveis criados pelos scripts utilizam um `meister.config.yaml` temporário restrito exclusivamente aos tiers **Luna** (`gpt-6-luna`) e **Gemini Flash** (`gemini-3.8-flash-medium`), com `max_retries: 1`. Nenhum modelo Claude ou de alto custo é utilizado.

### 1. `run_flow.sh` (~1 minuto)
Testa o ciclo fundamental do MeisterRouter de ponta a ponta:
- `meister classify`: classificação determinística via Jev e resolução da cadeia de fallback.
- `meister worker`: despacho do worker com fallback resiliente caso o primeiro modelo falhe.
- Evidência determinística: execução de `pytest` e validação do `git diff`.
- `meister control`: decisão final de aceitação (COMPLETE).
- Telemetria JSONL: validação de correlação de `run_id` e presença de eventos `worker_start` e `worker_end`.

### 2. `run_parallel.sh` (~30 segundos)
Testa a orquestração concorrente de subtarefas independentes (arquivos disjuntos):
- `meister orchestrate`: executa duas subtarefas (`t1` e `t2`) simultaneamente.
- Amostragem com `herdr_sampler.py`: monitora em tempo real a criação das abas `worker:t1` e `worker:t2` e painéis em worktrees isolados.
- Execução sobreposta: valida via `orchestration_log.jsonl` que o início de uma tarefa ocorreu antes do fim da outra.
- Merge serial: confirma que ambos os branches foram integrados ordenadamente na branch `main`.

### 3. `run_safety.sh` (~3 a 6 minutos)
Valida a robustez e invariantes de segurança do orquestrador sob condições adversas (36 checagens no total: S1 com 7, S2 com 11, S3 com 9 e S4 com 9):
- **Cenário S1 (Gate Reprova):** Uma subtarefa válida e outra contendo um teste que falha propositalmente. Garante que o gate determinístico bloqueia a integração, a branch `main` permanece intacta no commit base original, nenhum código inválido é incorporado e todos os worktrees temporários são limpos.
- **Cenário S2 (SIGKILL e Retomada):** Interrompe o orquestrador com sinal `SIGKILL` enquanto uma subtarefa dependente está em execução, e em seguida reexecuta exatamente o mesmo comando. Garante a idempotência da retomada: subtarefas já concluídas não são reexecutadas e o resultado integrado na `main` preserva o trabalho de todas as tarefas.
- **Cenário S3 (Crash após merge):** Injeta `MEISTER_CRASH_AT=after_merge_before_state` e `MEISTER_CRASH_TASK=t1` somente no primeiro `meister orchestrate`, simulando uma queda após o merge e antes da persistência do estado. A retomada roda sem injeção e verifica o evento `fault_injected`, que a t1 é reexecutada no máximo 1 vez (o estado dela não chegou a ser gravado) e a t2 uma só vez, integração única de `mul` e `shout`, `pytest` e limpeza de worktrees, branches e abas (tempo aproximado: a medir).
- **Cenário S4 (Crash após fast-forward):** Injeta `MEISTER_CRASH_AT=after_fast_forward_before_state` somente no primeiro `meister orchestrate`, simulando uma queda após o fast-forward e antes da persistência do estado. A retomada sem injeção valida os mesmos invariantes de S3 (tempo aproximado: a medir).

### 4. `run_plan.sh` (tempo aproximado: a medir)
Valida o fluxo end-to-end baseado em contrato de plano canônico (`superpowers -> plan import -> plan validate -> orchestrate --plan-file`) com 26 checagens (P0 com 5, P1 com 13 e P2 com 8):
- **Cenário P0 (Validação rápida sem LLM):** Valida a rejeição estrita de planos inválidos na conversão (`meister plan import` falha com código de saída diferente de zero citando a tarefa sem a seção `**Files:**`, e tem sucesso com `--allow-unscoped`), e confirma que o orquestrador bloqueia texto livre (`meister orchestrate --task "texto livre"` sai com código 2 sem criar registro de run no SQLite do repositório descartável e com zero eventos `worker_spawn` no JSONL).
- **Cenário P1 (Fluxo de plano real com LLM e retomada idempotente):** Converte um plano de implementação real no formato Superpowers (`plan.md`) para JSON canônico (`plan.json`) via `meister plan import`, valida o esquema canônico via `meister plan validate` e despacha a execução autônoma multi-tarefa via `meister orchestrate --plan-file`. Valida a ordem de dependência sequencial (`task_2` depende de `task_1`), a execução e integração correta na branch `main` (`def mul` em `calc.py` e `def shout` em `text.py` exatamente 1 vez cada), aprovação da suíte `pytest` na `main`, telemetria no JSONL (`worker_spawn` e `subtask_completed`), e a idempotência da retomada ao reexecutar exatamente o mesmo comando (`run_id` estável gerado a partir do JSON canônico, zero novos spawns de worker), além da limpeza total de worktrees, branches temporárias e abas de worker.
- **Cenário P2 (Dependência real entre tarefas sem arquivo em comum):** Valida a execução ordenada de tarefas com dependência lógica real onde a Task 2 consome um símbolo criado pela Task 1 (`square` em `geo.py` que faz `from calc import mul`), sem compartilhar arquivos (`target_files` estritamente disjuntos). Com `--deps sequential` padrão, comprova via telemetria que a Task 2 só é despachada após o término bem-sucedido da Task 1, integrando ambas na `main` com aprovação dos testes.

---

## Variáveis de Ambiente

Os scripts aceitam as seguintes variáveis de customização:

| Variável | Padrão | Descrição |
|---|---|---|
| `MEISTER_E2E_DIR` | `${TMPDIR:-/tmp}/meister-e2e` | Diretório descartável onde logs, repositórios temporários e worktrees de teste são criados. Os scripts nunca gravam arquivos dentro do repositório principal. |
| `MEISTER_E2E_PYTHON` | `<repo>/.venv/bin/python` (fallback: `python3`) | Caminho do interpretador Python a ser usado para testes e coleta de dados. |
| `E2E_LUNA_MODEL` | `gpt-6-luna` | Nome do modelo para o tier Luna no arquivo de configuração do repo descartável. Permite forçar falha no Luna para testar fallback (ex: `E2E_LUNA_MODEL=gpt-modelo-inexistente`). |
| `MEISTER_E2E_SAMPLE_SECS` | `120` | Duração máxima em segundos da amostragem de abas/painéis do Herdr pelo `herdr_sampler.py`. |
| `E2E_S1_T2_DESC` | *(descrição padrão de teste falho)* | Sobrescreve a descrição da subtarefa `t2` no cenário S1 de `run_safety.sh`. Permite testar deterministicamente o caminho de resultado inconclusivo / SKIP passando uma descrição benigna. |

---

## Como Executar

A partir da raiz do repositório:

```bash
# 1. Executar o fluxo padrão
bash tests/e2e/run_flow.sh

# 2. Executar o fluxo forçando fallback de modelo (Luna inexistente -> Gemini Flash)
E2E_LUNA_MODEL=gpt-modelo-inexistente bash tests/e2e/run_flow.sh

# 3. Executar o teste de orquestração paralela
bash tests/e2e/run_parallel.sh

# 4. Executar os testes de invariantes de segurança e retomada
bash tests/e2e/run_safety.sh

# 5. Executar o teste de segurança com subtarefa benigna para validar o caminho INCONCLUSIVO/SKIP
E2E_S1_T2_DESC="Adicione a função up(s) em text.py que retorna s.upper()" bash tests/e2e/run_safety.sh

# 6. Executar o fluxo E2E baseado em contrato de plano (Superpowers -> import -> validate -> orchestrate)
bash tests/e2e/run_plan.sh
```

---

## Como Ler o RESUMO e Códigos de Saída

Ao final de cada script, é exibido um resumo estruturado no seguinte formato:

```text
################ RESUMO ################
PASS  flow-a classify devolveu recomendado + cadeia
PASS  flow-b algum worker da cadeia concluiu
PASS  flow-c main tem def mul e test_mul
PASS  flow-d pytest passa na main
PASS  flow-e control respondeu COMPLETE
PASS  flow-f eventos JSONL com mesmo run_id e worker_start/end
PASS  flow-g sem worktrees/branches/tabs restantes

Totais: PASS=7  FAIL=0  SKIP=0
Resultado: SUCESSO (todas as checagens passaram)
```

- Cada linha indica o resultado (`PASS`, `FAIL` ou `SKIP`) seguido do identificador e descrição da checagem.
- **Códigos de saída:**
  - `0`: Todas as asserções passaram (`SUCESSO`).
  - `1`: Ao menos uma asserção falhou (`FALHA`).
  - `2`: Nenhuma asserção falhou, mas houve checagens marcadas como `SKIP` (`INCONCLUSIVO`).

> [!NOTE]
> **Resiliência e Inconclusividade no Cenário S1:** O cenário S1 de `run_safety.sh` depende de o worker (LLM) escrever um teste que propositalmente falha. Como os harnesses de workers incluem diretivas gerais de qualidade ("ensure tests pass"), alguns modelos podem se recusar a escrever testes quebrados ou ignorar o arquivo. O script repete o S1 até 2 vezes com um repositório limpo; caso o modelo persista em não produzir o teste falho, as asserções de reprovação do S1 (S1-a..g e S2-k) são marcadas como `SKIP` em vez de `FAIL`, e o script termina com código `2` (`INCONCLUSIVO`), evitando falsos negativos na suíte.

---

## S3 e S4: queda no meio do commit de estado

Os cenários **S3** e **S4** do `run_safety.sh` testam a tolerância e consistência contra quedas (crash-consistency) no momento exato entre operações concluídas no Git e a gravação de estado no banco de dados SQLite.

### Comparativo dos Cenários de Queda

| Cenário | Ponto de Queda (`MEISTER_CRASH_AT`) | Filtro de Tarefa (`MEISTER_CRASH_TASK`) | Estado no Momento da Queda | Tempo Aprox. |
|---|---|---|---|---|
| **S3** | `after_merge_before_state` | `t1` | O Git já integrou a subtarefa `t1`, mas o SQLite ainda não sabe. | a medir |
| **S4** | `after_fast_forward_before_state` | *(nenhum)* | A branch `main` já avançou, mas o run ainda não está `COMPLETED`. | a medir |

### Mecânica de Injeção e Retomada

- **Injeção somente no primeiro comando:** A variável de injeção vai só no ambiente do primeiro `meister orchestrate`; a retomada (mesmo comando) roda sem ela.
- **Hook `meister/faults.py`:** A variável é lida por `meister/faults.py`, que encerra o processo via sinal `SIGKILL` e registra o evento `fault_injected` em `$MEISTER_LOG_DIR/faults.jsonl`.
- **Comportamento na retomada:**
  - **S3:** Como a queda ocorreu antes do estado da `t1` ser persistido, a retomada reexecuta a `t1` no máximo uma vez e a `t2` uma única vez, integrando ambas na `main` sem duplicação de definições.
  - **S4:** Como ambas as tarefas já haviam sido concluídas e a `main` avançou antes da queda, a retomada não reexecuta nenhuma subtarefa e apenas conclui a persistência do estado e limpeza de recursos.

### Camada Rápida vs. E2E Real

- **Camada rápida (`tests/test_crash_matrix.py`):** Os 13 pontos de queda completos estão na camada rápida com fakes (sem uso de LLMs reais, sem Herdr e sem rede), validando sistematicamente a matriz de consistência contra falhas no CI.
- **E2E real (`tests/e2e/run_safety.sh`):** Cobre exclusivamente os cenários críticos com ferramentas e workers reais: **S2** (queda abrupta por SIGKILL externo durante worker ativo), **S3** (queda após merge antes de persistir estado) e **S4** (queda após fast-forward antes de persistir estado).

### Lista de Checagens Executadas no Script

O script `run_safety.sh` valida as seguintes checagens para os cenários S3 e S4:

#### Cenário S3
- `S3-a kill/crash injetado registrado no JSONL`
- `S3-b retomada terminou com rc 0`
- `S3-c t1 reexecutada no maximo 1x e t2 rodou 1x`
- `S3-d main tem mul exatamente uma vez`
- `S3-e main tem shout exatamente uma vez`
- `S3-f pytest passa na main`
- `S3-g sem worktrees restantes`
- `S3-h sem branches meister/worktree/* e meister/integration/*`
- `S3-i sem tabs worker:* restantes`

#### Cenário S4
- `S4-a kill/crash injetado registrado no JSONL`
- `S4-b retomada terminou com rc 0`
- `S4-c subtarefas nao foram reexecutadas`
- `S4-d main tem mul exatamente uma vez`
- `S4-e main tem shout exatamente uma vez`
- `S4-f pytest passa na main`
- `S4-g sem worktrees restantes`
- `S4-h sem branches meister/worktree/* e meister/integration/*`
- `S4-i sem tabs worker:* restantes`

---

## run_plan.sh: Fluxo de Contrato de Plano e Idempotência

O script `run_plan.sh` valida o fluxo completo baseado em contratos de planos canônicos: conversão a partir do formato Superpowers, validação estrita de esquema e de integridade referencial do grafo de dependências, execução autônoma multi-tarefa com workers locais via Herdr e garantia determinística de idempotência na retomada de execução.

### Pré-requisitos

Os mesmos pré-requisitos gerais da suíte E2E:
1. Sessão ativa no Herdr com `HERDR_ENV=1` e CLI `herdr` disponível.
2. CLI `codex` (Luna) e CLI `agy` (Gemini Flash).
3. `git` e `python3` com as dependências do projeto.

### Comparativo dos Cenários

| Cenário | Descrição | Envolve LLM | Tempo Aprox. |
|---|---|---|---|
| **P0** | Validação sintática e rejeição de planos/tarefas sem escopo ou em texto livre. | Não | a medir |
| **P1** | Importação de plano Superpowers (`mul` + `shout`), validação de esquema, execução pelo orquestrador e retomada idempotente. | Sim (Luna/Gemini Flash) | P0+P1 sem o P2: cerca de 1 minuto em uma medição (2026-09-29); o run completo P0+P1+P2 levou 2 min 17 s em uma medição (2026-09-30) |
| **P2** | Dependência real entre tarefas sem arquivo em comum (`mul` em `calc.py` e `square` em `geo.py` importando `mul`). | Sim (Luna/Gemini Flash) | a medir (run sequencial de 2 tarefas levou cerca de 1 minuto em medição manual em 2026-09-30) |

### O que Cada Cenário Prova

1. **Cenário P0 (Validação Rápida sem LLM):**
   - **Rejeição de tarefas sem escopo:** `meister plan import` rejeita planos onde tarefas não possuam a seção obrigatória `**Files:**` (código de saída != 0), citando explicitamente a tarefa ofensiva no relatório de erro.
   - **Permissão explícita com flag:** A flag `--allow-unscoped` permite a conversão de tarefas sem escopo de arquivos com código de saída 0 e emissão de aviso.
   - **Bloqueio de texto livre no orquestrador:** A execução de `meister orchestrate --task` com texto livre não-canônico falha imediatamente com código `2` sem criar novos registros na tabela `runs` do banco SQLite (`.meister/meister.db`) e sem disparar nenhum evento `worker_spawn` na telemetria JSONL.

2. **Cenário P1 (Fluxo Real com Workers e Retomada Idempotente):**
   - **Passo 1 (Import):** Converte o arquivo Markdown no formato Superpowers (`plan.md`) para o arquivo canônico `plan.json` com resolução de dependência sequencial padrão (`task_2` depende de `task_1`).
   - **Passo 2 (Validate):** Executa `meister plan validate plan.json`, assegurando que o esquema das tarefas é rigorosamente compatível.
   - **Passo 3 (Orchestrate):** Executa `meister orchestrate --plan-file plan.json`, despachando os workers locais configurados (Luna e Gemini Flash) para resolver ordenadamente as subtarefas em worktrees isolados e integrá-las via pipeline determinístico na branch `main`.
   - **Passo 4 (Retomada / Idempotência):** Executa novamente o mesmo comando `meister orchestrate --plan-file plan.json`. Como a representação do plano em JSON canônico deriva o mesmo identificador determinístico de execução (`run_id`) e as subtarefas já constam como concluídas no banco de dados SQLite, o comando termina com código 0 imediatamente sem instanciar nenhum novo worker.

3. **Cenário P2 (Dependência Real entre Tarefas sem Arquivo em Comum):**
   - **Grafo de dependência sequencial com arquivos disjuntos:** Converte o plano onde a `task_2` cria `geo.py` com `square(x)` importando `mul` de `calc.py` (criada pela `task_1`). Valida que `task_1` não possui dependências (`depends_on: []`), `task_2` depende de `task_1` (`depends_on: ["task_1"]`) e os conjuntos de `target_files` são estritamente disjuntos, provando que a dependência não advém de compartilhamento de arquivos.
   - **Ordem temporal determinística de despacho:** Valida via log estruturado (`orchestration_log.jsonl`) que o evento `worker_spawn` da `task_2` ocorreu estritamente após o evento `subtask_completed` da `task_1`.
   - **Integração e testes na main:** Confirma que a branch `main` integra `geo.py` (com `from calc import mul` e `def square`) e `calc.py` (com `def mul` exatamente uma vez), com aprovação da suíte `pytest` na branch `main` e limpeza total de worktrees, branches temporárias e abas de worker.

### Lista de Checagens Executadas no Script

O script `run_plan.sh` valida 26 checagens distribuídas entre os cenários P0, P1 e P2:

#### Cenário P0
- `P0-a import de plano sem Files sai com rc != 0 e cita task`
- `P0-b import com --allow-unscoped sai com rc 0`
- `P0-c orchestrate com texto livre sai com rc != 0 (2)`
- `P0-d orchestrate com texto livre NAO cria run no SQLite`
- `P0-e zero worker_spawn no JSONL`

#### Cenário P1
- `P1-a import rc 0 e plan.json com 2 tarefas e dependencias corretas`
- `P1-b validate saiu com rc 0`
- `P1-c orchestrate saiu com rc 0`
- `P1-d main tem def mul exatamente 1x em calc.py`
- `P1-e main tem def shout exatamente 1x em text.py`
- `P1-f pytest passa na main`
- `P1-g eventos subtask_completed para task_1 e task_2 no JSONL`
- `P1-h worker_spawn de cada task == 1 apos primeiro orchestrate`
- `P1-i retomada (passo 4) saiu com rc 0`
- `P1-j retomada NAO criou novos worker_spawn`
- `P1-k sem worktrees restantes`
- `P1-l sem branches meister/worktree/* e meister/integration/*`
- `P1-m sem tabs worker:* restantes`

#### Cenário P2
- `P2-a import rc 0, plan.json com 2 tarefas, dependencias e target_files disjuntos`
- `P2-b validate rc 0 e orchestrate rc 0`
- `P2-c worker_spawn da task_2 ocorreu apos subtask_completed da task_1`
- `P2-d main tem geo.py contendo from calc import mul e def square`
- `P2-e main tem def mul exatamente 1x em calc.py`
- `P2-f pytest passa na main`
- `P2-g worker_spawn de cada task == 1`
- `P2-h sem worktrees, branches temporarias ou tabs restantes`

