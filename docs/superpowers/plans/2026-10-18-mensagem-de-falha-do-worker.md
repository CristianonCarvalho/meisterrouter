# A falha do worker mostra a causa em vez de só o código de saída Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** run `adb208df` do projeto chronoastro (somente leitura do log, 2026-10-18). A via do agy falhou duas vezes com a mensagem "Harness antigravity failed with exit code 3", sem a causa; o harness imprimia um aviso de esgotamento de cota na própria saída. Esta é a segunda metade do plano `2026-10-18-worker-usa-o-config-do-orquestrador.md`: a primeira tarefa dele (o config registrar o caminho do arquivo) já foi concluída e integrada. A primeira tentativa desta tarefa foi descartada por um falso positivo do detector de cota: o texto do plano e a saída do worker citavam o próprio sinal que a tarefa trata, e o Meister interpretou isso como cota esgotada nas vias Claude. Por isso este plano **não cita o texto do sinal**, e os testes montam a amostra em tempo de execução.

**Goal:** Quando um harness sai com código diferente de zero e a saída capturada dele contém um sinal de esgotamento de cota reconhecido por `find_quota_match`, a mensagem de falha do worker traz esse trecho depois do código de saída.

**Architecture:** Uma tarefa em `meister/worker.py`, com testes que montam a amostra sem escrever o sinal por extenso.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Nenhum texto novo nos catálogos.

### Task 1: Mensagem de falha do worker com o trecho de cota

**Files:**
- Modify: `meister/worker.py`
- Create: `tests/test_worker_exit_message.py`

Em `meister/worker.py`, no ponto em que o worker levanta `RuntimeError(f"Harness {self.harness} failed with exit code {exit_code}")` (saída diferente de zero e não por sinal), chame `find_quota_match` de `meister/engine/workers.py` sobre `captured_output` (a saída capturada do harness) e, havendo trecho, acrescente-o à mensagem no formato `Harness <harness> failed with exit code <código>: <trecho>` (o trecho já vem limitado a 200 caracteres). Sem trecho, a mensagem continua exatamente igual à de hoje. Não mude o motor de fallback, o circuit breaker nem a classificação de eventos do `bridge`.

Em `tests/test_worker_exit_message.py` teste: com uma saída que `find_quota_match` reconhece, a mensagem contém o código de saída e o trecho devolvido por `find_quota_match` para essa mesma saída; com uma saída comum, e com saída vazia, a mensagem é a de hoje; o código de saída fica sempre na mensagem. **Importante:** não escreva por extenso, nos testes nem em comentários, nenhuma frase que o detector de cota reconheça; monte a amostra em tempo de execução juntando pedaços (por exemplo, o nome do código de erro do provedor dividido em duas partes concatenadas) e confira o resultado comparando com `find_quota_match(amostra)`. Não abra nem imprima nenhum arquivo de `tests/fixtures/` que não seja necessário, e veja como `tests/test_worker*.py` exercitam falhas de harness para escolher o ponto de teste mais simples. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.
