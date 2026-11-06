# opencode: rodar no diretório do worker Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** a verificação real da run `5932871f` (plano `2026-11-05-harness-opencode.md`) achou um defeito. O `opencode run` escolhe o diretório de trabalho pela variável de ambiente `PWD`, não pelo diretório real do processo. Rodado com `subprocess.run(cmd, cwd=<pasta>)` a partir de outro diretório, ele escreveu o arquivo no diretório do processo pai (o repositório principal), e não em `<pasta>`. O flag `--dir <pasta>` corrige: com ele o arquivo foi para a pasta certa. Um worker do Meister que escrevesse no repositório principal em vez do worktree da subtarefa quebraria o isolamento da run.

**Goal:** toda execução do `opencode` pelo Meister passa `--dir` com o diretório do worker.

**Architecture:** só dois ramos que já existem ganham o flag; nada novo.

## Global Constraints

- O comando fica `[opencode, "run", "--auto", "--dir", <cwd>, "-m", <modelo>, <prompt>]`; o `-m` continua só quando há modelo diferente de `default`, e o prompt continua sempre o último argumento.
- Sem `cwd` (vazio ou `None`), nenhum `--dir` é acrescentado.
- Nenhum teste chama o `opencode` de verdade. Nenhuma chave de configuração nova.
- A documentação é escrita pelo orquestrador depois; não a inclua nas tarefas.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: `--dir` no comando do `meister worker`

**Files:**
- Modify: `meister/worker.py`
- Modify: `tests/test_worker_opencode.py`

Em `build_harness_command` (`meister/worker.py`), no ramo `HARNESS_OPENCODE`, passe a montar `cmd = [cli_bin, "run", "--auto"]` e, quando `cwd` for uma string não vazia, acrescente `"--dir", cwd` logo em seguida; o resto (modelo e prompt por último) não muda. Atualize os testes que comparam o comando exato em `tests/test_worker_opencode.py` e acrescente: com `cwd` o par `--dir <cwd>` aparece antes de `-m`; com `cwd` vazio não aparece; o prompt continua sendo o último elemento.

### Task 2: `--dir` no comando do motor de orquestração

**Files:**
- Modify: `meister/engine/workers.py`
- Modify: `tests/test_engine_workers_opencode.py`

Em `meister/engine/workers.py`, dê a `resolve_command` um parâmetro opcional `cwd: Optional[str] = None` (depois dos atuais, sem quebrar quem já chama) e use-o só no ramo do `opencode` com descrição: depois de `["run", "--auto"]`, acrescente `"--dir", cwd` quando `cwd` for uma string não vazia. Em `spawn_worker_pane` e `spawn_worker_tab`, passe o `cwd` que eles já recebem para `resolve_command`. Os outros harnesses ignoram o parâmetro. Atualize os testes que comparam o comando exato em `tests/test_engine_workers_opencode.py` e acrescente: `resolve_command(..., cwd="/x")` traz `--dir /x` no modo com descrição, sem `cwd` não traz, o modo interativo (sem descrição) não traz, e um harness diferente (ex.: `kiro-cli`) devolve o mesmo comando de antes com ou sem `cwd`.
