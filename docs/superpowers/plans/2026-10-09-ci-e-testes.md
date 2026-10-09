# CI e testes herméticos Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Origem:** análise global de 2026-10-09 (itens 5 e 8). Este plano só toca `tests/test_cli.py`, `.github/workflows/ci.yml`, `pyproject.toml` e `requirements.txt`; pode rodar em paralelo com os outros dois planos de 2026-10-09. A entrada do CHANGELOG é escrita pelo orquestrador no fim.

**Goal:** (1) `test_cli_init` deixa de depender do `python3` do `PATH` ter as dependências instaladas. (2) O CI passa a cobrir macOS, mede cobertura e roda varredura de segurança, sem bloquear o merge enquanto os resultados são avaliados.

**Architecture:** Duas mudanças pequenas. O teste passa a invocar `bin/meister` com `sys.executable` (o `bin/meister` tem shebang `#!/usr/bin/env python3`). O workflow ganha uma entrada de matriz para macOS, `pytest-cov` (só relatório, sem limite mínimo), `bandit` e `pip-audit` com `continue-on-error: true`.

**Tech Stack:** GitHub Actions, Python ≥ 3.10, `pytest`, `pytest-cov`, `bandit`, `pip-audit`.

## Global Constraints

- Nenhuma mudança em `meister/`; só testes, workflow e dependências de desenvolvimento.
- Dependências novas ficam em `[project.optional-dependencies].dev` do `pyproject.toml` e em `requirements.txt`, com limite inferior de versão.
- Os passos de lint (`ruff check .`), tipos (`mypy meister`) e testes (`pytest -v --durations=15`) existentes continuam, sem relaxar nenhum.
- O workflow precisa ser YAML válido (verifique com `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`); não há como rodar o Actions localmente, então descreva no relatório o que ficou sem verificação.
- Suíte, `ruff check .` e `mypy meister` limpos localmente.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Tornar `test_cli_init` hermético

**Files:**
- Modify: `tests/test_cli.py`

**Depends on:** none

`test_cli_init` (`tests/test_cli.py` ~59 a ~70) executa `BIN_MEISTER` diretamente; o shebang usa o `python3` do `PATH`, que pode não ter `flask` e as demais dependências (falha reproduzida rodando a suíte de um venv não ativado). Invoque `[sys.executable, BIN_MEISTER, "init", ...]`. Verifique todos os outros usos de `BIN_MEISTER` no repositório (`grep -rn BIN_MEISTER tests/`) e corrija os que executam o script diretamente. Critério de aceite: o teste passa quando a suíte roda com `/caminho/do/venv/bin/python -m pytest` **sem** ativar o venv.

### Task 2: CI em macOS, cobertura e varredura de segurança

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

**Depends on:** Task 1

1. Matriz: acrescente `macos-latest` com Python 3.12 (uma versão só, para limitar minutos), mantendo a matriz atual do Ubuntu (3.10 a 3.13) e `fail-fast: false`. Reestruture com `include`/`os` conforme necessário; os passos de lint, tipos e testes rodam nas duas plataformas.
2. Cobertura: adicione `pytest-cov` às dependências de desenvolvimento e rode `pytest --cov=meister --cov-report=term-missing` (apenas relatório, sem `--cov-fail-under`).
3. Segurança: adicione `bandit` e `pip-audit` às dependências de desenvolvimento e dois passos novos, `bandit -r meister -q` e `pip-audit`, ambos com `continue-on-error: true` e nomes que indiquem "informativo".
4. Se `pytest-xdist` e `--cov` interferirem no `-n auto` usado localmente, documente em comentário no workflow a decisão tomada.

Critério de aceite: o YAML carrega sem erro; `pip install -e ".[dev]"` instala as dependências novas; localmente `pytest --cov=meister -q` passa; `bandit -r meister -q` e `pip-audit` rodam (a saída delas não precisa estar limpa, mas deve ser resumida no relatório final com a contagem de achados por severidade).
