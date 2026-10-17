# Reexecução de teste instável do vitest, prefixo da timeline e nome da run Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** avaliação das runs do projeto chronoastro (somente leitura do log, 2026-10-15 e 2026-10-16). Gates paralelos reprovaram testes de timing do vitest (`ShareCard.test.tsx:95`, `Tela1.test.tsx:54`) que passam quando rodam de novo; o `gate.flaky_retries` só reexecuta testes do pytest, então o reparo foi gasto e a run exigiu `--resume`. Continuação de `2026-10-15-gate-vitest-e-run-id.md`, que criou o campo `js_failed_tests`. Os dois ajustes pequenos saíram da revisão desse trabalho.

**Goal:** O gate reexecuta só os arquivos de teste do vitest que falharam antes de reprovar; o motivo na timeline não traz o marcador `❯ `; a run lançada sem `--name` ganha um nome legível a partir do arquivo do plano.

**Architecture:** Três tarefas independentes (arquivos diferentes), que rodam em paralelo.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Esta mudança não cria textos novos nos catálogos.

### Task 1: `gate.flaky_retries` também reexecuta arquivos do vitest

**Files:**
- Modify: `meister/gate.py`
- Create: `tests/test_gate_flaky_vitest.py`

Hoje `DeterministicGate._run_with_flaky_retry` só reexecuta quando `summarize_gate_failure(result.output)["failed_tests"]` tem ids do pytest. Estenda assim: quando `failed_tests` estiver vazio e `js_failed_tests` (campo já existente do resumo) não estiver, extraia o arquivo de cada item (o trecho antes do primeiro ` > `, sem repetição, na ordem em que aparecem) e reexecute só esses arquivos com o vitest local do projeto: `node_modules/.bin/vitest run <arquivos>` (no Windows, o `.cmd` da mesma pasta), com `cwd` no repositório do gate, o mesmo ambiente e `PATH` que `_rerun_failed_tests` monta, `shell=False`, `timeout=180` e a variável `CI=1`. Se o binário do vitest não existir em `node_modules/.bin`, devolva o resultado original sem reexecutar. O laço de tentativas (`gate.flaky_retries`, padrão 2), a exigência de uma verificação completa verde ao final e o preenchimento de `flaky_tests` (com os itens de `js_failed_tests`) seguem como no caminho do pytest; crie um método irmão `_rerun_failed_js_files(path, files)` em vez de alterar `_rerun_failed_tests`, e deixe o caminho do pytest idêntico ao de hoje (ele tem prioridade quando `failed_tests` não está vazio). Não crie opção de configuração nova: `gate.flaky_retries` vale para os dois. Testes em `tests/test_gate_flaky_vitest.py`, no estilo de `tests/test_gate_flaky.py` (veja como ele substitui `subprocess.run` e a verificação completa): saída do vitest com uma falha cuja reexecução passa gera aprovação com `flaky_tests` preenchido; reexecução que continua falhando mantém a reprovação original; sem `node_modules/.bin/vitest` não há reexecução; `flaky_retries=0` desliga; o comando reexecutado traz só os arquivos que falharam (sem repetição); saída do pytest continua usando o caminho antigo.

### Task 2: O motivo da timeline não traz o marcador do frame

**Files:**
- Modify: `meister/timeline.py`
- Modify: `tests/test_timeline_gate_reason.py`

Em `_summary_reason` (`meister/timeline.py`), a linha escolhida do `failure_summary` pode começar com o marcador de frame do vitest (`❯ ` ou `× `), que aparece no tooltip como `❯ src/screens/tela1/Tela1.test.tsx:54:34`. Tire esse prefixo (e os espaços depois dele) da linha escolhida antes de aplicar `_clip_detail`, nos três casos de escolha (linha `FAIL`, arquivo de teste com linha e linha de erro). A linha que começa com `FAIL` continua inteira (o `FAIL` é parte do motivo). Não mude `_failed_tests_reason`, `_js_failed_tests_reason` nem a ordem de escolha. Testes em `tests/test_timeline_gate_reason.py`: um `failure_summary` cuja linha de arquivo de teste começa com `❯ ` gera o motivo sem o prefixo; o mesmo com `× `; a linha `FAIL ...` não perde o `FAIL`; os testes existentes do arquivo seguem passando.

### Task 3: Nome da run a partir do arquivo do plano

**Files:**
- Modify: `meister/cli.py`
- Modify: `tests/test_run_name.py`

Quando `meister orchestrate` recebe `--plan-file` e não recebe `--name`, a run hoje é nomeada pela primeira linha da primeira tarefa (`derive_run_name`), que costuma ser só `Task 1: ...` e descreve uma tarefa, não a run. Em `meister/cli.py`, no comando `orchestrate`, quando `--name` não foi passado, houver `--plan-file` e o título da primeira tarefa do plano começar com `Task` seguido de número (expressão `^Task\s+\d+\b`, sem diferenciar maiúsculas), passe a `run_name` o nome derivado do arquivo: o nome do arquivo sem extensão, sem um prefixo de data `AAAA-MM-DD-`, com `-` e `_` trocados por espaço, espaços repetidos reduzidos a um e a primeira letra maiúscula (`checkout-sem-conta.json` vira `Checkout sem conta`). Se o nome derivado ficar vazio ou com menos de 3 caracteres, não passe nada e mantenha o comportamento de hoje. Com `--name` informado, ou com o título da primeira tarefa que não comece com `Task N`, nada muda. Crie a função pura `run_name_from_plan_file(path)` em `meister/cli.py` para o nome do arquivo e use-a no ponto descrito; não mude `derive_run_name` nem o formato do plano. Testes em `tests/test_run_name.py`, no estilo dos que já existem ali: `checkout-sem-conta.json` gera `Checkout sem conta`; `2026-10-15-gate-vitest.json` gera `Gate vitest`; nome curto demais devolve vazio; com a primeira tarefa `Task 1: ...` e sem `--name` o `run_name` passado ao ciclo é o derivado do arquivo; com `--name` ele prevalece; com a primeira tarefa sem o padrão `Task N` ele não é usado.
