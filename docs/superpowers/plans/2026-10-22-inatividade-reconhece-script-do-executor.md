# Inatividade reconhece executores iniciados por script Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** verificação do orquestrador após a run 1d94fefd (2026-10-21). `_is_check_command` só olha o primeiro item do comando, mas um `pytest` real roda como `python3 /caminho/.venv/bin/pytest -q ...` (o item 1 é o script) e o mesmo vale para `mypy`, `vitest` e `jest` (`node /caminho/vitest`). Medido com um `pytest` de verdade: `_is_check_command` devolve falso, então a detecção de executores em andamento não funcionaria na prática, embora os testes da run (com comandos simulados) passem.

**Goal:** `_is_check_command` reconhece um executor também quando o primeiro item é um interpretador (`python`, `node`, shell) e o segundo é o script do executor.

**Architecture:** Mudança só em `meister/engine/bridge.py`: a verificação de nome base passa a olhar o item 0 e, quando o item 0 é um interpretador conhecido, também o item 1. Os demais critérios não mudam.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`.

## Global Constraints

- Interpretadores reconhecidos (nome base, sem `.exe`, comparação em minúsculas, aceitando versão no nome como `python3.13`): `python`, `python3`, `python3.N`, `pythonw`, `node`, `nodejs`, `sh`, `bash`, `zsh`. Só nesses casos o item 1 conta como candidato; para qualquer outro item 0 (por exemplo `claude`, `ruff`, `java`) nada muda.
- O item 1 só conta se, ao ser comparado pelo nome base com a lista de executores diretos que já existe, for exatamente um deles. Opções (começam com `-`) e texto longo dentro de um argumento nunca contam: `["claude","-p","rode o pytest"]` e `["python","server.py"]` continuam falsos; `["python3","/x/.venv/bin/pytest","-q"]`, `["node","/x/node_modules/.bin/vitest","run"]` e `["/bin/sh","/x/pytest","-q"]` passam a ser verdadeiros.
- Os critérios existentes (`-m pytest|ruff|mypy`, pares `cargo test`, `npm test`, `mvn test`, `go test`, item 0 direto) ficam como estão, com os mesmos testes.
- Um dos testes novos usa um processo de verdade: execute `sys.executable` com um script chamado `pytest` (arquivo temporário em pasta temporária, que apenas dorme alguns segundos), leia o comando dele com `ps -ww -o command= -p <pid>` (`skipif` fora da POSIX), separe com `shlex.split` e confirme que `_is_check_command` devolve verdadeiro; depois encerre o processo.
- Ao fim da tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Script do executor depois do interpretador

**Files:**
- Modify: `meister/engine/bridge.py`
- Modify: `tests/test_bridge_idle_checks.py`

Em `meister/engine/bridge.py`, acrescente a constante `_INTERPRETERS` (conjunto de nomes base) e a função pura `_is_interpreter(arg) -> bool` (inclui `python3.N` e variantes com versão, por prefixo `python` seguido de dígitos e pontos), e estenda `_is_check_command`: depois do teste do item 0 direto, se `_is_interpreter(argv[0])` e `len(argv) > 1` e `argv[1]` não começar com `-`, devolva verdadeiro quando `_base_cmd(argv[1])` estiver em `_DIRECT_CHECK_COMMANDS`. Nada mais muda. Testes novos em `tests/test_bridge_idle_checks.py`, nos casos listados nas Global Constraints (verdadeiros e falsos), mais o teste com processo de verdade, sem remover nenhum teste existente.
