# Conflito de escopo no gate, morte por sinal e resumo de falha Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --deps files`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** relatório `trade_omqs/project/RELATORIO_MEISTER_GATE_ESCOPO.md` (Meister `v0.9.1-663-g369528f`, run `8724f67e45f0c5e7`). Os itens 2.2 a 2.5 foram conferidos no código atual (`main` em `369528f`):
> - 2.2: `meister/worker.py:744` levanta `RuntimeError` para qualquer `exit_code != 0`, inclusive `-9`; no `engine/bridge.py` (ramo `status == "error"`, por volta da linha 2018) isso vira `worker_error`: uma retentativa na mesma via e depois escalada. O caminho `pane_lost` (retenta na mesma via com espera e não escala) já existe e não é usado.
> - 2.3: o escopo é checado antes do gate (`meister/worktree.py:1540`) e o reparo só pode editar `target_files`; um teste antigo fora do escopo que reprova gasta o único reparo sem como passar. Em `engine/bridge.py` (por volta da linha 2205) `can_repair` não olha onde estão os testes que falharam.
> - 2.4: o evento `subtask_rejected` guarda `int_err[:200]`, o começo da saída do gate (ruído do alembic no caso do relatório); `summarize_gate_failure` (`meister/gate.py:94`) já extrai os testes que falharam, mas só alimenta o prompt de reparo.
> - 2.5: `meister plan validate` não consulta a suíte existente.
>
> **Correção ao relatório:** o relatório diz que o código do worker "foi descartado". Não foi: depois da falha, `cleanup_worktree(..., archive_unmerged=True)` arquiva o trabalho solto em `refs/meister/archive/*`; o que falta é a mensagem de falha dizer isso. A diferença entre o harness `antigravity` do log e o `tier_1` de `meister models` é só nome: `antigravity` é o nome interno do harness `agy`.
>
> **Fora do plano:** limite de custo por tarefa (item 3.5 do relatório), que é decisão de produto (BACKLOG).

**Goal:** Quando o gate reprova por um teste fora do escopo da tarefa, a tarefa termina na hora com `scope_conflict`, sem gastar o reparo, e a mensagem diz qual arquivo incluir em `target_files` e onde o trabalho foi arquivado. Um harness morto por sinal (por exemplo `-9`) é tratado como falha de infraestrutura: retenta na mesma via e, esgotadas as tentativas, falha sem subir de modelo. A mensagem de rejeição do gate começa pelos testes que falharam. O `plan validate` avisa sobre testes existentes que citam símbolos dos arquivos da tarefa e ficam fora do escopo.

**Architecture:** Quatro mudanças de código e uma de documentação. Tarefas 1 a 3 mexem em `engine/bridge.py`, `worker.py` e `worktree.py` e nos catálogos `en_engine`/`pt_br_engine`, então rodam em sequência; a tarefa 4 (`plan_analysis.py`, `cli.py` e os catálogos de CLI) é independente.

**Tech Stack:** Python ≥ 3.10, click, `pytest`. Textos novos entram nos catálogos `en` e `pt-BR` (a paridade é testada).

## Decisões fechadas

- Teste que falhou fora do escopo não gasta reparo: a tarefa termina com `reason="scope_conflict"`. Não há opção que deixe o reparo editar o teste (afrouxaria a garantia do escopo).
- Morte por sinal segue o comportamento de `pane_lost`: retenta na mesma via (`retry.pane_lost_attempts`) e, esgotado, falha como infraestrutura sem escalar de via.
- O resumo do `plan validate` é só aviso, nunca bloqueia.

### Task 1: Teste fora do escopo termina como conflito de escopo

**Files:**
- Modify: `meister/engine/bridge.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Modify: `tests/test_gate_repair.py`
- Modify: `tests/test_gate_repair_messages_i18n.py`
- Test: `tests/test_bridge_scope_conflict.py`

No ramo do gate reprovado de `engine/bridge.py` (onde `failed_tests` e `can_repair` são calculados, por volta da linha 2195), determine, para cada id de `failed_tests` no formato `caminho::teste`, o arquivo (parte antes de `::`, com separadores normalizados). O arquivo está fora do escopo se não está em `scope_files` (os `target_files` mais os arquivos das ancestrais, já calculados acima) e não casa com `self.config.scope.tolerated_files` (use o mesmo critério de casamento do escopo, em `meister/worktree.py`, sem duplicar a lógica). Quando `err_code == "gate"` e há pelo menos um teste fora do escopo, `can_repair` fica falso: não gaste o reparo, siga para o encerramento da tarefa com `reason="scope_conflict"` no evento `subtask_rejected` (o `reason` do evento e o do `self.last_failure_reason`). A mensagem vem de uma chave nova `engine.bridge.scope_conflict` nos dois catálogos e diz: o id do teste, o arquivo, que ele está fora de `target_files` (inclua-o no plano ou ajuste o plano) e que o trabalho do worker ficou arquivado em `refs/meister/archive/*` (`git for-each-ref refs/meister/archive/`). Quando todos os testes que falharam estão dentro do escopo, nada muda (o reparo continua como hoje). Tarefas sem `target_files` (sem escopo) nunca geram `scope_conflict`. Atenção: `tests/test_gate_repair.py` (`test_gate_repair_once_then_passes`, `test_exhaust_repair_attempts_bounded`) e `tests/test_gate_repair_messages_i18n.py` simulam um gate reprovando em testes como `tests/test_core.py::test_gate_fail` que NÃO estão em `target_files` e esperam reparo; ajuste esses testes para que o teste simulado que falha fique dentro do `target_files` da tarefa simulada, mantendo a cobertura do reparo (a primeira tentativa desta tarefa falhou por esses 4 testes). Teste: gate falha em `tests/test_x.py::test_a` fora de `target_files`: a tarefa fica FAILED com `scope_conflict` e nenhum `gate_repair`; o mesmo gate falhando num teste dentro de `target_files` ainda faz o reparo.

### Task 2: Harness morto por sinal é erro de infraestrutura

**Depends on:** Task 1

**Files:**
- Modify: `meister/worker.py`
- Modify: `meister/engine/bridge.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_worker_killed_by_signal.py`

Em `meister/worker.py:744`, quando `exit_code < 0` (processo morto por sinal, por exemplo `-9` SIGKILL), levante uma exceção própria `HarnessKilledError(RuntimeError)` com o número do sinal e a mensagem do catálogo (chave `engine.worker.killed_by_signal`: o harness, o sinal e a causa provável, como falta de memória ou encerramento externo); `exit_code > 0` continua `RuntimeError` comum. O resultado de erro gravado em `result.json` (`_early_error_result` e o ramo `except` perto da linha 1313) deve carregar um campo `error_kind: "signal"` quando a causa for essa exceção. No `engine/bridge.py`, no ramo `prompt_result.get("status") == "error"` (por volta da linha 2018), se `error_kind == "signal"` use o caminho de `pane_lost`: retente na mesma via enquanto `pane_lost_retries_used < retry.pane_lost_attempts` (evento `worker_retry` com `reason="signal_killed"`), sem tocar em `worker_error_retries`, e, esgotadas as tentativas, termine a subtask FAILED com evento `worker_error` de `status="infrastructure_error"`, sem escalar de via. Erros que não são sinal seguem o caminho `worker_error` atual. Teste unitário de `worker.py`: `exit_code=-9` levanta `HarnessKilledError` e grava `error_kind: "signal"`; `exit_code=1` continua `RuntimeError` sem esse campo. Teste do `bridge`: dois resultados `error_kind: "signal"` seguidos terminam FAILED sem trocar de via e sem incrementar `worker_error_retries`.

### Task 3: Mensagem de rejeição do gate começa pelos testes que falharam

**Depends on:** Task 2

**Files:**
- Modify: `meister/worktree.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_worktree_gate_failure_message.py`

Em `meister/worktree.py:1581` (a mensagem `engine.worktree.worker_gate_failed`), calcule `summarize_gate_failure(out)` (de `meister/gate.py`) e coloque na primeira linha da mensagem os 3 primeiros ids de `failed_tests`, com `(+N)` quando houver mais, por uma chave nova `engine.worktree.worker_gate_failed_tests` nos dois catálogos. Depois dessa linha mantenha a saída completa como hoje, porque o `bridge` reaplica `summarize_gate_failure` sobre a mensagem para o prompt de reparo e para o evento. Sem testes identificados (só lint, por exemplo), a mensagem fica exatamente como hoje. Teste: saída com `FAILED tests/a.py::t1` e ruído de alembic antes: a primeira linha da mensagem contém `tests/a.py::t1`, e `summarize_gate_failure` sobre a mensagem devolve os mesmos `failed_tests` de antes; saída só com lint: mensagem inalterada.

### Task 4: `plan validate` avisa sobre testes existentes fora do escopo

**Files:**
- Modify: `meister/plan_analysis.py`
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_plan_test_collisions.py`

Em `meister/plan_analysis.py`, crie `find_test_collisions(tasks, repo_root)`: para cada tarefa com `target_files` Python, leia esses arquivos com `ast` e colete os nomes públicos de funções e classes de primeiro nível (ignore nomes com menos de 5 caracteres); procure, nos arquivos `test_*.py` e `*_test.py` do repositório que não estão em `target_files` da tarefa, referências a esses nomes (palavra inteira). Devolva, por tarefa, até 3 arquivos de teste e os nomes citados. Em `meister/cli.py`, mostre o resultado como aviso em `plan validate` e `plan analyze` (junto de `serial_plan_warning`, onde já são impressos avisos), com texto em chave nova nos dois catálogos de CLI: o teste X cita `nome` de `arquivo`, que a tarefa altera; se a tarefa muda esse comportamento, inclua o teste em `target_files`. É só aviso: não muda o código de saída e não roda nada se `repo_root` não for um repositório ou se um alvo ainda não existir. Atenção ao `mypy` do gate: não reutilize o mesmo nome de variável para tipos diferentes e anote os dicionários (por exemplo `Dict[Path, str]` para o texto lido de cada arquivo); a primeira tentativa desta tarefa falhou com `Incompatible types in assignment` e `Invalid index type "Path" for "dict[str, str]"` em `plan_analysis.py`. Teste: repositório temporário com `src/m.py` (função `calcular_total`) e `tests/test_m.py` que a cita; plano cuja tarefa altera só `src/m.py` gera o aviso; se `tests/test_m.py` está em `target_files`, não gera.

### Task 5: Changelog e documentação

**Depends on:** Task 3, Task 4

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`
- Modify: `docs/execution-manual.md`
- Modify: `docs/pt-BR/MANUAL_DE_EXECUCAO.md`
- Modify: `docs/plan-format.md`
- Modify: `docs/pt-BR/FORMATO_DO_PLANO.md`

Em `### Fixed` de `[Unreleased]` (em português, `### Corrigido`): teste fora do escopo que reprova o gate encerra a tarefa com `scope_conflict` sem gastar o reparo; harness morto por sinal vira falha de infraestrutura sem subir de modelo; a mensagem de rejeição do gate começa pelos testes que falharam. Em `### Added` (`### Adicionado`): aviso do `plan validate`/`plan analyze` sobre testes existentes que citam símbolos dos arquivos da tarefa. No manual de execução, uma entrada para `scope_conflict` (o que significa, como achar o trabalho em `refs/meister/archive/`, e que a saída é incluir o teste em `target_files` e rodar de novo com `--resume`) e uma para o harness morto por sinal (causas prováveis e o que fazer). No formato do plano, uma regra: inclua em `target_files` os testes existentes cujo comportamento a tarefa muda de propósito. Mantenha as versões em inglês e português equivalentes.
