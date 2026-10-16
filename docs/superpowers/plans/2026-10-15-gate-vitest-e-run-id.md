# Resumo de falha do vitest no gate e run_id do worktree de integração Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** avaliação das runs do projeto chronoastro (somente leitura do log, 2026-10-15). Em 8 reparos de gate, 0 trouxeram `failed_tests` e o resumo entregue ao worker de reparo era o rabo da saída do vitest (dump de DOM e pilha do `@testing-library`). Além disso, o evento `worktree_setup_ok` do worktree de integração saía com o `run_id` da run anterior, o que inflava a duração das runs no relatório.

**Goal:** O worker de reparo recebe, para falhas do vitest, os nomes dos testes e a mensagem de asserção em vez do rabo da saída; o evento de preparo do worktree de integração fica na run certa.

**Architecture:** Três tarefas pequenas em sequência (as duas últimas editam `worktree.py`). Cuidado central: os nomes de teste do vitest NÃO entram em `failed_tests`, porque essa lista alimenta o retry de pytest (`gate.py`, `_rerun_failed_tests`) e a checagem de escopo da ponte (`bridge.py`, `_test_file_path`). Eles vão numa chave nova, `js_failed_tests`.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Esta mudança não cria textos novos; reutiliza chaves existentes dos catálogos.

### Task 1: `summarize_gate_failure` entende a saída do vitest

**Files:**
- Modify: `meister/gate.py`
- Create: `tests/test_gate_vitest_summary.py`

Hoje `summarize_gate_failure` só reconhece pytest, ruff e mypy; para saída do vitest o `excerpt` vira as últimas 15 linhas não vazias (um dump de DOM). Acrescente a chave `js_failed_tests` ao dicionário devolvido, em todos os retornos (inclusive o de saída vazia e o do `except`, com lista vazia). Antes de analisar, tire os códigos de cor ANSI de cada linha. Reconheça linhas de falha do vitest no formato `FAIL  <arquivo> > <suite> > <teste>` (também com `❯` ou `×` no início) e guarde cada uma sem repetição, sem a cor, até 10 itens, na ordem em que aparecem. Quando `failed_tests` estiver vazio, `js_failed_tests` não estiver vazio e a saída não tiver as marcas do pytest (`E   ` nem `short test summary info`), monte o `excerpt` assim: as linhas de falha, depois a primeira linha de cada mensagem de erro (linhas que começam com `AssertionError`, `TestingLibraryElementError`, `Error:` ou `TypeError`, até 10), depois as linhas de contagem `Test Files` e `Tests`; corte em 1500 caracteres como hoje. Em qualquer outro caso o `excerpt` continua exatamente como é hoje. Não altere `failed_tests`, `lint_errors` nem `missing_dependencies`. Testes em `tests/test_gate_vitest_summary.py`, com saídas de exemplo escritas à mão dentro do teste: duas falhas do vitest geram `js_failed_tests` com os dois nomes e `excerpt` com as linhas de falha e a mensagem de asserção, sem o dump de DOM; saída com cores ANSI dá o mesmo resultado; `failed_tests` continua vazio nesses casos; saída de pytest segue com `excerpt` idêntico ao de hoje e `js_failed_tests` vazio; saída vazia devolve a chave com lista vazia; saída do vitest sem linhas de falha reconhecíveis mantém o `excerpt` antigo (últimas 15 linhas). Os testes existentes de `tests/test_gate_failure_summary.py` e `tests/test_gate_missing_dependency.py` seguem passando; ajuste neles apenas comparações de dicionário completo, se houver.

### Task 2: mensagem de falha do worker cita os testes do vitest

**Files:**
- Modify: `meister/worktree.py`
- Modify: `tests/test_worktree_gate_failure_message.py`

**Depends on:** Task 1

Em `meister/worktree.py`, no trecho que monta a mensagem quando o gate do worker reprova (procure `summarize_gate_failure(out)`, em torno da linha 1580), quando `failed_tests` estiver vazio e `summary["js_failed_tests"]` não, acrescente a mesma linha de cabeçalho que hoje existe para `failed_tests`, usando a chave já existente `engine.worktree.worker_gate_failed_tests` com os três primeiros nomes de `js_failed_tests` e o sufixo `(+N)` quando houver mais. Não crie chaves novas nos catálogos, não use `js_failed_tests` em lugar nenhum além deste cabeçalho e não mude a ordem dos demais cabeçalhos (dependência ausente primeiro, testes depois). Testes em `tests/test_worktree_gate_failure_message.py`, no estilo dos que já existem ali: saída de vitest com duas falhas gera a linha de cabeçalho com os nomes; saída de pytest continua gerando o cabeçalho de hoje; saída sem testes reconhecíveis continua sem cabeçalho.

### Task 3: `worktree_setup_ok` do worktree de integração leva o `run_id` da run

**Files:**
- Modify: `meister/worktree.py`
- Create: `tests/test_worktree_setup_run_id.py`

**Depends on:** Task 2

O método `WorktreeManager._prepare_node_worktree(info)` chama `log_event` sem `run_id`; o `log_event` então usa o `current_run.json`, que só o `classify` atualiza, e o worktree de integração de uma run nova (criado antes do primeiro `classify`) grava `worktree_setup_ok` com o `run_id` da run anterior. Dê ao método um parâmetro opcional `run_id: Optional[str] = None` e passe-o como `run_id=run_id` nos dois `log_event` do método (`worktree_setup_ok` e `worktree_setup_failed`). Na chamada feita pelo `IntegrationPipeline` (procure `self.wt_mgr._prepare_node_worktree(info)`, em torno da linha 1388) passe `run_id=self.run_id`. A chamada feita pelo próprio `WorktreeManager` (em torno da linha 440) não muda. Não altere `meister/logger.py`: o `log_event` continua usando o `current_run.json` quando nenhum `run_id` é passado, porque `classify` e `control` rodados à mão dependem disso. Testes em `tests/test_worktree_setup_run_id.py`, com `prepare_environment` e `log_event` substituídos (monkeypatch em `meister.worktree` e `meister.logger`; veja `tests/test_scope_gate_features.py`, perto de `worktree_setup_failed`, como exemplo): com `run_id="abc"` o evento `worktree_setup_ok` sai com `run_id="abc"`; o mesmo vale para `worktree_setup_failed`; sem o parâmetro o evento sai sem `run_id` explícito (como hoje); a preparação do worktree de integração do `IntegrationPipeline` passa o `run_id` da própria run.
