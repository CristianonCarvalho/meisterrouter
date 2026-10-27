# Complexidade das funções alteradas no `meister report` Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** estudo de 2026-10-27 da skill `ub-mode` (github.com/unclebob/ub-mode). Decisão do dono: não adotar a skill, mas medir se os workers deixam o código mais complexo. Medida só de leitura: não bloqueia o gate e não muda o prompt. Ponto de partida do projeto (ruff C901, limite 10): 28 de 895 funções do `meister/` acima de 10 (`validate_config` 105, `_parse_config_dict` 65, `orchestrate` 42).
>
> **Regra de decisão depois:** com cerca de 20 tarefas medidas, se "worsened" for raro, não fazer mais nada; se for frequente, propor transformar em regra do gate (só para funções alteradas, nunca global).

**Goal:** depois de integrar cada subtarefa, medir a complexidade das funções Python que ela alterou, gravar um evento no log e mostrar os totais no `meister report`.

**Architecture:** módulo puro `meister/complexity.py` (stdlib `ast` e `git`), um gancho no `bridge.py` que grava o evento `complexity_check` sem nunca afetar a run, e linhas novas no relatório.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`.

## Global Constraints

- Só leitura: nenhuma falha de medição pode falhar, atrasar o gate ou reprovar uma tarefa. Sem chave de configuração nova, sem mudança no prompt do worker, sem dependência nova.
- Complexidade de uma função = 1 + número de `if`/`elif`, `for`, `async for`, `while`, `except`, cada operando extra de `and`/`or`, cada cláusula `if` de compreensão e cada `case` de `match` (`with` e `assert` não contam). Funções aninhadas contam como funções próprias, e o corpo delas não soma na função de fora. Limite de aviso: 10 (constante `COMPLEXITY_THRESHOLD`). Só arquivos `.py`; outras linguagens não são medidas.
- Evento `complexity_check`, um por subtarefa integrada que alterou algum `.py`, com os campos `run_id`, `task_id`, `functions_changed` (funções novas ou com linhas alteradas), `over_threshold` (quantas dessas ficaram acima de 10), `worsened` (quantas subiram de complexidade em relação ao antes, contando função nova acima de 10 como piora), `max_complexity` e `worst` (texto `arquivo::função`). Subtarefa sem `.py` alterado não grava evento.
- Relatório: `report["complexity"]` com `functions_changed`, `over_threshold`, `worsened`, `max_complexity` somados (o máximo é o maior); run sem nenhum evento mostra `not measured`, nunca `0`. Rótulos só em `en` e `pt-BR`, mesma chave nos dois.
- A documentação (CHANGELOG, índice de planos) é escrita pelo orquestrador depois; não a inclua nas tarefas.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Módulo de complexidade das funções alteradas

**Files:**
- Create: `meister/complexity.py`
- Create: `tests/test_complexity.py`

Crie `meister/complexity.py` com `COMPLEXITY_THRESHOLD = 10`, `function_complexities(source: str) -> dict[str, tuple[int, int, int]]` (nome qualificado, como `Classe.metodo`, para `(complexidade, linha inicial, linha final)`; devolve `{}` se o código não compilar) e `changed_function_report(repo_path: str, base: str, head: str) -> Optional[dict]`. Esta usa `git diff -U0 --diff-filter=AM base head -- '*.py'` para achar as linhas alteradas de cada arquivo, lê o arquivo antes (`git show base:path`, vazio se novo) e depois (`git show head:path`), e conta como alterada a função cujo intervalo toca uma linha alterada ou que não existia antes. Devolve o dicionário do evento (`functions_changed`, `over_threshold`, `worsened`, `max_complexity`, `worst`) ou `None` se nenhum `.py` mudou ou se qualquer comando git falhar (nunca levanta exceção). Testes: repositório git temporário com `git init`; função nova simples, função que ganha dois `if`, função que não muda, arquivo que não compila, diff só de `.md` devolve `None`, e os valores exatos de `function_complexities` para um `if/elif/else`, um `and` e um laço.

### Task 2: Gravar o evento ao integrar a subtarefa

**Files:**
- Modify: `meister/engine/bridge.py`
- Test: `tests/test_bridge_complexity_event.py`

**Depends on:** Task 1

Em `meister/engine/bridge.py`, logo antes do `log_event(event_type="subtask_completed", ...)` (perto da linha 2588), quando `integrated_sha_for_task` existir, chame `changed_function_report` numa thread (`asyncio.to_thread`) com o repositório de integração que o `_integration_pipeline` já usa, `base` igual a `<integrated_sha>^1` e `head` igual ao `integrated_sha` (é o commit de merge da subtarefa; o primeiro pai é o estado antes dela). Se devolver um dicionário, grave `log_event(event_type="complexity_check", run_id=active_run_id, task_id=task_id, **dicionário)`. Envolva tudo em `try/except Exception` com `logger.debug`, de modo que nada da medição afete o fluxo. Teste em `tests/test_bridge_complexity_event.py` com `changed_function_report` simulada: grava o evento com os campos certos; quando devolve `None` não grava; quando levanta exceção a subtarefa ainda termina e grava `subtask_completed`.

### Task 3: Linhas de complexidade no relatório

**Files:**
- Modify: `meister/report.py`
- Modify: `meister/locales/en_reports.py`
- Modify: `meister/locales/pt_br_reports.py`
- Test: `tests/test_report.py`

Em `meister/report.py`, onde `report["attempts"]` é montado, acrescente `report["complexity"]`: some `functions_changed`, `over_threshold` e `worsened` dos eventos `complexity_check` da run e tome o maior `max_complexity`; sem nenhum evento, o valor é `None` e a tabela mostra `not measured` (como as linhas do Jev). Na tabela, quatro linhas logo depois de "Tasks with a gate repair": `reports.complexity_functions` ("Functions changed (Python)" / "Funções alteradas (Python)"), `reports.complexity_over` ("Functions above complexity 10" / "Funções acima de complexidade 10"), `reports.complexity_worsened` ("Functions with higher complexity" / "Funções com complexidade maior") e `reports.complexity_max` ("Highest complexity" / "Maior complexidade"). Testes em `tests/test_report.py`: log fabricado com 2 eventos `complexity_check` (somas e máximo corretos), run sem o evento (`not measured`), e as linhas presentes em `table`, `json` e `markdown`.
