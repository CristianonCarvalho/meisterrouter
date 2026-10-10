# Hosts de worker, fase 3: experiência para qualquer harness Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois do plano `2026-10-10-hosts-fase2-adaptador-process.md`:** o log por worker (`<log_dir>/workers/`) e o host `process` nascem lá. Origem: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`, seção 10, fase 3.
>
> **Hot files:** `meister/cli.py` (Tarefas 2 e 6), `meister/herdr/bridge.py` (Tarefa 2) e `meister/worker.py` (Tarefa 2). Não rodar junto de outro plano que mexa nesses arquivos, em especial `2026-10-09-seguranca-e-documentacao.md`.

**Goal:** Deixar o Meister utilizável a partir de qualquer harness de linha de comando, sem Herdr: (1) um comando para ler o log de um worker, ao vivo ou parado; (2) um nome de variável de ambiente neutro para marcar o worker, mantendo o antigo; (3) a execução em segundo plano documentada de ponta a ponta, com `meister wait`, `meister timeline --json` e `meister logs`.

**Architecture:** O pedido já existente `meister wait` (espera a run terminar, `--format json`, códigos de saída) e `meister timeline --json` cobrem o acompanhamento do andamento; faltam o log por worker (escrito pelo `ProcessHost` na fase 2) e a leitura dele. A marca de worker passa a ser reconhecida por um único auxiliar (`meister/envvars.py`) que aceita `MEISTER_WORKER=1` e o antigo `MEISTER_IN_PANE=1`; o código que define a variável define as duas, para o guard hook e os modelos de instrução continuarem funcionando em instalações já feitas.

**Tech Stack:** Python ≥ 3.10, `click`, `pytest`. Nenhuma dependência nova.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Nome neutro | `MEISTER_WORKER=1`. `MEISTER_IN_PANE=1` continua definida e reconhecida, sem prazo para remoção (instalações antigas têm o hook gerado com o nome velho). |
| Lista de variáveis do worker | `MEISTER_WORKER` entra em `DEFAULT_SAFE_ENV_VARS` de `meister/worker.py` ao lado de `MEISTER_IN_PANE`; sem isso o harness filho (por exemplo o Claude Code com o guard) não a enxerga. |
| `meister logs` | Sem argumentos mostra as últimas 50 linhas do log de worker mais recente. `--list` lista os logs (run, tarefa, tentativa, tamanho, data). `--run-id`, `--task` e `--lines N` filtram. `--follow`/`-f` acompanha até `Ctrl-C`. Só lê; nunca cria nem altera arquivos. |
| Onde estão os logs | `<get_log_dir()>/workers/<run>_<task>_<tentativa>.log` (fase 2). Sem esse diretório, o comando diz que não há logs de worker (por exemplo, uma run feita no Herdr) e sai com código 2. |
| Segundo plano | Sem flag nova. O manual documenta `nohup`/`setsid`, o id da run impresso na primeira linha (`Plan: ... (run 0123abcd)`) e o `meister wait --run-id`. |
| Fora deste plano | Uma flag `--detach` (decisão adiada), arquivos de instrução específicos para o Copilot, e os arquivos da raiz `AGENTS.md`/`CODEX.md`/`CLAUDE.md` deste repositório (são gerados pelo `meister setup --project`). |

## Global Constraints

- Sem dependência nova; compatível com Python 3.10; `from __future__ import annotations` nos módulos novos.
- Mensagens ao usuário por `t()`, com entradas em **todos** os catálogos de `meister/locales/`; `tests/test_i18n_ratchet.py`, `tests/test_locales_parity.py` e `tests/test_no_hardcoded_models.py` ficam verdes.
- Documentação nova em inglês (`docs/`) **e** português (`docs/pt-BR/`), com links relativos válidos nos dois (`tests/test_docs_links.py`).
- Instalações antigas continuam funcionando: um hook gerado com `MEISTER_IN_PANE` só funciona se essa variável continuar sendo definida.
- `ruff check .` e `mypy meister` limpos; a suíte inteira verde ao fim de **cada** tarefa.
- Testes sem rede, sem tocar `~/.meister`, sem chamar CLI de IA.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Auxiliar da variável de ambiente do worker

**Files:**
- Create: `meister/envvars.py`
- Test: `tests/test_envvars.py`

**Depends on:** none

Crie `WORKER_ENV_NAMES = ("MEISTER_WORKER", "MEISTER_IN_PANE")`, `worker_env_assignments() -> dict[str, str]` (devolve as duas com valor `"1"`, para quem define o ambiente) e `is_worker_env(env: Mapping[str, str] | None = None) -> bool` (verdadeiro se qualquer uma das duas for `"1"` no `env`, ou em `os.environ` quando omitido). Testes: nenhuma definida; só a nova; só a antiga; as duas; valor diferente de `"1"`; `env` explícito não lê `os.environ`.

### Task 2: O código define e reconhece as duas variáveis

**Files:**
- Modify: `meister/worker.py`
- Modify: `meister/cli.py`
- Modify: `meister/herdr/bridge.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_worker_process_exit.py`
- Test: `tests/test_worker_execution.py`

**Depends on:** Task 1

Em `meister/worker.py`: acrescente `MEISTER_WORKER` ao conjunto `DEFAULT_SAFE_ENV_VARS` (~90) e use `worker_env_assignments()` onde hoje se monta `MEISTER_IN_PANE=1` para o painel (~822 e ~1031). Em `meister/cli.py`: `in_pane = is_worker_env()` (~767). Em `meister/herdr/bridge.py`: onde `env_vars = ["MEISTER_IN_PANE=1"]` e `task_dict["env"]` são montados (~1039), inclua as duas. Os testes que fixam o prefixo exato da linha de comando (`command_str`) ou o dicionário `env` devem ser ajustados **somente** para esperar também `MEISTER_WORKER=1`; nenhuma outra asserção muda. Vários testes isolam o ambiente com `monkeypatch.delenv("MEISTER_IN_PANE", raising=False)`; em vez de editá-los um a um, faça a fixture autouse `isolate_test_environment` de `tests/conftest.py` remover também `MEISTER_WORKER` (e `MEISTER_IN_PANE`) do ambiente de cada teste. Acrescente um caso em `tests/test_worker_execution.py` provando que o ambiente filtrado do harness (`build_safe_worker_env`) preserva `MEISTER_WORKER`.

### Task 3: Hooks aceitam as duas variáveis

**Files:**
- Modify: `meister/templates/en/claude_guard_hook.sh.template`
- Modify: `meister/templates/pt-BR/claude_guard_hook.sh.template`
- Modify: `meister/templates/en/claude_prompt_hook.sh.template`
- Modify: `meister/templates/pt-BR/claude_prompt_hook.sh.template`
- Test: `tests/test_hooks.py`

**Depends on:** none

No guard (linha 10), acrescente `[ "${MEISTER_WORKER}" = "1" ] ||` ao lado do teste de `MEISTER_IN_PANE`. No hook de prompt (linha 10), o texto passa a dizer "If MEISTER_WORKER=1 (or the older MEISTER_IN_PANE=1) is set..." (e equivalente em português). Mantenha o restante dos hooks idêntico. Em `tests/test_hooks.py`, afirme que os dois modelos aceitam cada uma das variáveis (execute o guard com `bash` e `MEISTER_WORKER=1`, depois com `MEISTER_IN_PANE=1`, e confirme que ambos permitem a edição; sem nenhuma, o guard bloqueia como antes).

### Task 4: Modelos de instrução mencionam o nome neutro

**Files:**
- Modify: `meister/templates/en/AGENTS.md.template`
- Modify: `meister/templates/pt-BR/AGENTS.md.template`
- Modify: `meister/templates/en/CODEX.md.template`
- Modify: `meister/templates/pt-BR/CODEX.md.template`
- Modify: `meister/templates/en/CLAUDE.md.template`
- Modify: `meister/templates/pt-BR/CLAUDE.md.template`
- Test: `tests/test_init_templates.py`

**Depends on:** none

Nas frases que hoje dizem que o agente é um worker "se `MEISTER_IN_PANE=1` estiver definido", passe a dizer "`MEISTER_WORKER=1` (ou, em instalações antigas, `MEISTER_IN_PANE=1`)". Onde os modelos dizem que o Meister só roda "com Herdr" ou "sem Herdr", troque por "sem um host de worker disponível" (o Herdr deixa de ser a única opção); não mude regras de delegação. O teste existente de modelos continua passando e ganha uma asserção de que os seis modelos citam `MEISTER_WORKER`.

### Task 5: Leitura dos logs de worker

**Files:**
- Create: `meister/logs_cmd.py`
- Test: `tests/test_logs_cmd.py`

**Depends on:** none

Funções puras e sem efeito colateral: `list_worker_logs(log_dir) -> list[WorkerLog]` (`WorkerLog(path, run_id, task_id, attempt, size, mtime)`, interpretando o nome `<run>_<task>_<tentativa>.log` com tolerância a nomes que não casam, que são ignorados); `select_log(logs, run_id=None, task_id=None)` (prefixo de run com pelo menos 6 caracteres como em `meister timeline`; sem filtros devolve o de `mtime` mais recente); `read_tail(path, lines)` (lê só o final do arquivo, sem carregar tudo; tolera arquivo grande e sem quebra de linha final); `follow(path, *, poll=0.5, stop=None)` (gerador que devolve as linhas novas; `stop()` opcional encerra). Testes com arquivos em `tmp_path`: nome inválido ignorado, prefixo ambíguo, arquivo vazio, arquivo de 50 MB lido em tempo curto, e `follow` vendo linhas acrescentadas depois.

### Task 6: Comando `meister logs`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_cli_logs.py`

**Depends on:** Task 2, Task 5

Registre `meister logs` com `--run-id`, `--task`, `--lines` (padrão 50), `--list`, `--follow/-f` e `--log-dir` (mesma semântica de `meister timeline`), usando `meister/logs_cmd.py`. Textos de ajuda e mensagens em `t()` (chaves `cli.logs.*`) nos dois idiomas. Sem diretório de logs de worker ou sem logs: mensagem traduzida e código de saída 2. `--follow` sai limpo com `Ctrl-C` (código 130). Testes com `CliRunner` e arquivos em `tmp_path`: padrão mostra o log mais recente; `--list`; filtros; prefixo ambíguo (código 2 e candidatos listados); sem logs; `--follow` com um gerador que termina; `meister --help` lista o comando.

### Task 7: Manual de execução e comandos avançados

**Files:**
- Modify: `docs/execution-manual.md`
- Modify: `docs/pt-BR/MANUAL_DE_EXECUCAO.md`
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`

**Depends on:** Task 6

No manual (seção 3, "Rodar um plano"), acrescente a subseção "Rodar em segundo plano e de qualquer harness": comando `nohup meister orchestrate --plan-file plano.json > .meister/orchestrate.log 2>&1 &` (e a alternativa com `setsid`), como ler o id da run na primeira linha (`Plan: ... (run 0123abcd)`), `meister wait --run-id <id>` com os códigos de saída, `meister timeline --json --once` e `meister logs -f`. Em "Onde olhar" (seção 5) inclua `<log_dir>/workers/`. Em `docs/advanced.md`, documente `runtime.host` (`auto`, `process`, `herdr`, `tmux`), a variável `MEISTER_WORKER` (com a nota sobre `MEISTER_IN_PANE`) e `meister logs`. Tudo nos dois idiomas, com os mesmos links.
