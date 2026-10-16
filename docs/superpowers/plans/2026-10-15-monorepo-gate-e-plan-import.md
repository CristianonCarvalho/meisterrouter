# Gate para monorepo, aviso de dependência ausente, timeout do worker e plan import Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** relatório do projeto chronoastro (monorepo com app Vite na raiz e API Express em `backend/`), verificado no código em 2026-10-15. O gate instalava só a raiz, o `gate.install` não aceitava `&&`, o `meister worker` tinha timeout fixo de 180 s e o `plan import` descartava arquivos em silêncio.

**Goal:** Um monorepo com subpacotes Node passa no gate sem configuração manual; quando faltar dependência, a mensagem diz isso; `meister worker` aceita `--timeout`; `plan import` não perde arquivos em silêncio.

**Architecture:** Cinco tarefas pequenas. As tarefas 1, 3 e 5 são independentes; a 2 e a 4 dependem da 1 porque editam `env_setup.py` e `cli.py`, que a 1 também edita.

**Tech Stack:** Python ≥ 3.10, click, `pytest`, `ruff`, `mypy`. Textos novos entram nos catálogos `en` e `pt-BR` (a paridade é testada).

### Task 1: `gate.install` aceita uma lista de comandos

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/env_setup.py`
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_reports.py`
- Modify: `meister/locales/pt_br_reports.py`
- Create: `tests/test_gate_install_list.py`

Hoje `gate.install` é `Optional[str]` e `env_setup.prepare_environment` faz `shlex.split` sem shell, então `npm ci && npm --prefix backend ci` não funciona. Faça `gate.install` aceitar também uma lista de strings não vazias (cada item é um comando, sem shell), além da string única e de `null`. Em `meister/config.py`, o campo passa a `Union[str, List[str], None]` e a validação (em torno da linha 855) aceita string não vazia, lista não vazia de strings não vazias ou `null`; nos demais casos, erro com uma chave nova de `reports.config` (texto em `en_reports.py` e `pt_br_reports.py`, ex.: "deve ser texto não vazio, lista de textos ou nulo"). Em `meister/env_setup.py`, normalize `config.gate.install` numa lista de comandos e rode-os em sequência no `repo_path`, parando no primeiro que falhar e devolvendo a mensagem de falha existente (`commands.env.install_failed`); a saída devolvida é a concatenação das saídas. Em `meister/cli.py`, o `config show` (em torno da linha 2287) imprime a lista unida por ` && ` e o JSON continua com o valor cru. Não mude `gate.py` nem `worktree.py` (só testam se o campo é verdadeiro). Testes em `tests/test_gate_install_list.py`: string única continua valendo; lista roda os comandos na ordem; falha do segundo comando interrompe e reporta; lista vazia e item não string geram erro de configuração.

### Task 2: Instalar subpacotes Node com lockfile próprio

**Files:**
- Modify: `meister/env_setup.py`
- Create: `tests/test_env_setup_subpackages.py`

**Depends on:** Task 1

Sem `gate.install`, `prepare_environment` instala só o `package.json` da raiz. Depois da instalação da raiz, instale também cada subpacote: diretório abaixo da raiz (até 3 níveis) com `package.json` e lockfile próprio (`package-lock.json`, `pnpm-lock.yaml` ou `yarn.lock`), usando o mesmo comando escolhido por `_node_install_command` com `cwd` no subpacote. Ignore `node_modules`, `.git`, `.venv`, `venv`, `dist`, `build`, `.claude` e `.meister`. Não instale subpacotes quando o `package.json` da raiz tem `workspaces` ou existe `pnpm-workspace.yaml` (a instalação da raiz já cobre). Com `gate.install` definido, nada muda. A ordem é determinística (caminhos ordenados) e a falha de um subpacote devolve a mensagem de falha existente com o comando e o diretório. Testes em `tests/test_env_setup_subpackages.py`, com `subprocess.run` substituído (veja `tests/test_env_setup_no_deps.py`): raiz com `backend/` e lockfile próprio instala os dois; subpacote sem lockfile é ignorado; `node_modules` não é varrido; raiz com `workspaces` não instala subpacotes; `gate.install` definido não instala subpacotes.

### Task 3: Mensagem do gate distingue dependência ausente de teste reprovado

**Files:**
- Modify: `meister/gate.py`
- Modify: `meister/worktree.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Create: `tests/test_gate_missing_dependency.py`

Em `summarize_gate_failure` (`meister/gate.py`), acrescente a chave `missing_dependencies`: lista (sem repetição, até 5) dos nomes achados na saída nos padrões `Cannot find package 'X'`, `Cannot find module 'X'`, `Failed to resolve import "X"` e `No module named 'X'` (aspas simples ou duplas). Os demais campos não mudam. Em `meister/worktree.py`, onde a mensagem de falha do gate do worker inclui `Failing tests:` (em torno da linha 1582), acrescente uma linha própria quando `missing_dependencies` não for vazia: dependência ausente no ambiente (nomes), possivelmente instalação incompleta de um subpacote, com a dica de configurar `gate.install`. A linha vem antes da lista de testes. Chaves novas nos catálogos `engine` em `en` e `pt-BR`. Não mude o fluxo de reparo nem o código de saída. Testes em `tests/test_gate_missing_dependency.py`: cada um dos quatro padrões é detectado; saída sem esses padrões gera lista vazia e mensagem igual à atual; nomes repetidos aparecem uma vez.

### Task 4: `meister worker --timeout`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Create: `tests/test_worker_timeout_option.py`

**Depends on:** Task 1

O `meister worker` chama `run_worker_in_herdr_tab` e `run_worker_in_herdr_pane` (em `meister/cli.py`, em torno das linhas 1030 a 1050) sem timeout, e as duas funções usam o padrão fixo de 180 s. Acrescente a opção `--timeout` (segundos, `click.FloatRange(min=1)`, padrão 180) ao comando `worker` e repasse o valor às duas chamadas. Ajuste a mensagem `cli.worker.timeout` (nos dois catálogos) para dizer que o worker pode ter deixado trabalho parcial, que o Meister o arquiva em `refs/meister/archive/*` e que `git for-each-ref refs/meister/archive` lista o que foi guardado; cite também a opção `--timeout`. Não mude `meister/worker.py`. Testes em `tests/test_worker_timeout_option.py`: com `run_worker_in_herdr_tab` substituído, `--timeout 600` chega à função com 600 e sem a opção chega 180; a mensagem de timeout cita `--timeout` e `refs/meister/archive`.

### Task 5: `plan import` não descarta arquivos em silêncio

**Files:**
- Modify: `meister/plan_adapters/superpowers.py`
- Modify: `meister/locales/en_misc.py`
- Modify: `meister/locales/pt_br_misc.py`
- Create: `tests/test_plan_import_files_lines.py`

Em `_extract_files_block`, a linha de `**Files:**` só vale com um único caminho entre crases e nada depois; outras formas somem sem aviso. Mude assim: aceite os verbos `Create`, `Modify`, `Test`, `Rewrite` (vale como `Modify`) e `Delete` (o arquivo entra em `target_files` como os demais); em cada linha com verbo conhecido, todo trecho entre crases é um caminho (normalizado e validado como hoje, inclusive o sufixo `:N-M`), e o texto fora das crases é ignorado (ex.: `(append ao final)`). Linha com verbo conhecido e nenhum trecho entre crases não é descartada em silêncio: imprima um aviso em stderr com chave nova `misc.plan_adapter.files_line_ignored` (texto em `en_misc.py` e `pt_br_misc.py`) citando a tarefa e a linha. Verbo desconhecido continua sendo erro. Atualize a lista `_KNOWN_VERBS` e a regex; não mude o resto do importador. Testes em `tests/test_plan_import_files_lines.py`: `- Create: \`a.ts\`, \`b.ts\`` gera os dois caminhos; `- Modify: \`docker/init.sql\` (append ao final)` gera o caminho; `Rewrite` e `Delete` entram; linha sem crases gera o aviso e não derruba a importação; verbo desconhecido continua dando erro; os testes existentes de `tests/test_plan.py` seguem passando.
