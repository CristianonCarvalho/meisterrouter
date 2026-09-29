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
Valida a robustez e invariantes de segurança do orquestrador sob condições adversas:
- **Cenário S1 (Gate Reprova):** Uma subtarefa válida e outra contendo um teste que falha propositalmente. Garante que o gate determinístico bloqueia a integração, a branch `main` permanece intacta no commit base original, nenhum código inválido é incorporado e todos os worktrees temporários são limpos.
- **Cenário S2 (SIGKILL e Retomada):** Interrompe o orquestrador com sinal `SIGKILL` enquanto uma subtarefa dependente está em execução, e em seguida reexecuta exatamente o mesmo comando. Garante a idempotência da retomada: subtarefas já concluídas não são reexecutadas e o resultado integrado na `main` preserva o trabalho de todas as tarefas.
- **Cenário S3 (Crash após merge):** Injeta `MEISTER_CRASH_AT=after_merge_before_state` e `MEISTER_CRASH_TASK=t1` somente no primeiro `meister orchestrate`, simulando uma queda após o merge e antes da persistência do estado. A retomada roda sem injeção e verifica o evento `fault_injected`, que a t1 é reexecutada no máximo 1 vez (o estado dela não chegou a ser gravado) e a t2 uma só vez, integração única de `mul` e `shout`, `pytest` e limpeza de worktrees, branches e abas.
- **Cenário S4 (Crash após fast-forward):** Injeta `MEISTER_CRASH_AT=after_fast_forward_before_state` somente no primeiro `meister orchestrate`, simulando uma queda após o fast-forward e antes da persistência do estado. A retomada sem injeção valida os mesmos invariantes de S3.

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
