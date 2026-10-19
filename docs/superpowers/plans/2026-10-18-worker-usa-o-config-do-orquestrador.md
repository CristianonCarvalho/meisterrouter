# O worker usa o mesmo meister.config.yaml que o orquestrador carregou Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** run `adb208df` do projeto chronoastro (somente leitura do log, 2026-10-18). A `tier_2b` (Claude Haiku 5.5) adicionada ao `meister.config.yaml` de lá falhou nas duas tentativas com o erro "Unknown lane 'tier_2b'. Valid lanes: tier_1, tier_1b, tier_1c, tier_2, tier_3 (disabled; explicit selection only: tier_3b)": essa lista é exatamente o catálogo padrão empacotado. O arquivo do chronoastro não está no git, então não existe no worktree (um checkout do último commit) onde o worker roda, e o worker, sem arquivo, usa o padrão. O orquestrador, que roda na pasta principal, lê o arquivo e escolhe a `tier_2b`, mas o worker nunca a vê (nem timeouts, nem `eligible_classes`, nem o resto). O código que monta a tarefa do worker (`meister/engine/bridge.py`, perto de `resolved_config_path`) já tenta repassar `getattr(self.config, "config_path", None)`, mas `MeisterConfig` não tem esse atributo: o valor é sempre nulo e o caminho do arquivo carregado nunca chega ao worker.
>
> Segundo achado, na mesma run: o `tier_2` (agy) falhou com "exit code 3", sem a causa. O harness imprimia que os créditos acabaram (o texto está em `tests/fixtures/agy_credits_exhausted.txt`, capturado de uma chamada real); a mensagem da exceção do worker só diz o código de saída.

**Goal:** O worker lê o mesmo arquivo de configuração que o orquestrador carregou, mesmo que o arquivo não esteja no worktree; e quando um harness sai com erro e a saída dele tem sinal de cota ou de crédito esgotado, a mensagem de falha do worker traz essa linha em vez de só o código de saída.

**Architecture:** Duas tarefas em arquivos diferentes: `meister/config.py` (o config registra de que arquivo veio) e `meister/worker.py` (a mensagem de falha).

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Nenhum texto novo nos catálogos de idioma.

### Task 1: `MeisterConfig` registra o caminho do arquivo carregado

**Files:**
- Modify: `meister/config.py`
- Create: `tests/test_config_path_propagation.py`

Em `meister/config.py`, acrescente a `MeisterConfig` o campo `config_path: Optional[str] = None` (ao lado de `config_source`) e faça `load_config` preenchê-lo com o caminho **absoluto** do arquivo carregado (o mesmo `target_path` já usado para `config_source`), qualquer que seja a origem: `config_path` explícito, `MEISTER_CONFIG_PATH`, `cwd/meister.config.yaml` ou `./meister.config.yaml`; sem arquivo (padrão empacotado), fica `None`. O resto do comportamento de `load_config` não muda. Não é preciso mexer em `meister/engine/bridge.py`: ele já lê `getattr(self.config, "config_path", None)` ao montar a tarefa do worker (confira que o valor entra no arquivo de tarefa como `config_path`). Em `tests/test_config_path_propagation.py` teste, com arquivos em diretórios temporários: caminho absoluto nas quatro origens, `None` quando não há arquivo, e que um caminho relativo vira absoluto. Procure em `tests/` se algum teste do bridge lê o arquivo de tarefa gravado (`_task.json`) e, havendo, acrescente a ele a conferência de `config_path`; senão, cubra o repasse com o menor teste possível. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.

### Task 2: A falha do worker traz a linha de cota ou crédito esgotado

**Files:**
- Modify: `meister/worker.py`
- Create: `tests/test_worker_exit_message.py`

Em `meister/worker.py`, no ponto em que o worker levanta `RuntimeError(f"Harness {self.harness} failed with exit code {exit_code}")` (saída diferente de zero e não por sinal), use `find_quota_match` de `meister/engine/workers.py` sobre a saída capturada do harness (`captured_output`) e, havendo trecho, acrescente-o à mensagem: `Harness antigravity failed with exit code 3: <trecho>` (o trecho já vem cortado em 200 caracteres por `find_quota_match`). Sem sinal de cota, a mensagem continua exatamente como hoje. Não mude o motor de fallback, o circuit breaker nem a classificação de eventos do bridge. Em `tests/test_worker_exit_message.py` teste: com a saída de `tests/fixtures/agy_credits_exhausted.txt` a mensagem traz o trecho `RESOURCE_EXHAUSTED`; com uma saída comum a mensagem é a de hoje; com saída vazia também; e que o código de saída continua na mensagem. Use a função ou o ponto de teste mais simples que o código já oferecer (veja como `tests/test_worker*.py` exercitam falhas de harness). Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.
