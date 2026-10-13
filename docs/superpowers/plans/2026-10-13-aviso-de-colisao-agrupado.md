# Aviso de colisão de testes agrupado por arquivo Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** verificação do plano `2026-10-13-conflito-de-escopo-e-sinal-9.md`. Num plano real que altera só `meister/gate.py`, o aviso do `plan validate` saiu com 8 linhas para 3 arquivos de teste, uma por par (teste, nome citado). O plano pedia uma linha curta por arquivo de teste.

**Goal:** O aviso de colisão do `plan validate` e do `plan analyze` imprime uma linha por arquivo de teste, com no máximo 3 nomes citados e `(+N)` quando houver mais.

**Architecture:** Uma tarefa só, em `meister/cli.py` (`_echo_test_collisions`), nos dois catálogos de CLI e no teste existente.

**Tech Stack:** Python ≥ 3.10, click, `pytest`. Textos novos entram nos catálogos `en` e `pt-BR` (a paridade é testada).

### Task 1: Uma linha de aviso por arquivo de teste

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_plan_test_collisions.py`

Em `_echo_test_collisions` (`meister/cli.py`), troque o laço interno por nome por uma linha por arquivo de teste (`collision["test"]`): junte até 3 nomes citados (`collision["names"][i]["name"]`, na ordem recebida, sem repetir) entre crases e separados por vírgula, e acrescente ` (+N)` quando houver mais de 3. Mude a chave `cli.plan.test_collision_warning` nos dois catálogos para os parâmetros `test`, `names` (o texto já montado), `task`: o teste X cita `a`, `b` (+N), que a tarefa T altera; se a tarefa muda esse comportamento, inclua o teste em `target_files`. Não mude `find_test_collisions` nem o código de saída. Ajuste os testes existentes que conferem a mensagem e acrescente um: um arquivo de teste que cita 5 nomes gera 1 linha com 3 nomes e `(+2)`; dois arquivos de teste geram 2 linhas.
