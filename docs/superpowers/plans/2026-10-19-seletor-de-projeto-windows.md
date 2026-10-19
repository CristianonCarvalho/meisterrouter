# Seletor de projeto: ajuste dos testes no Windows Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push.
>
> **Origem:** a CI do PR #187 reprovou 2 testes no Windows; o código de produção está certo, os testes é que supõem caminhos do Linux e do macOS.

**Goal:** Os 2 testes novos do seletor de projeto passam também no Windows, sem mudar o código de produção.

**Architecture:** Só os dois arquivos de teste mudam.

**Tech Stack:** Python ≥ 3.10, `pytest`.

## Global Constraints

- Não mudar nada em `meister/`. Só os dois arquivos de teste listados na tarefa.
- No Windows, `os.path.expanduser("~")` usa `USERPROFILE`, não `HOME`; e o mesmo diretório pode aparecer com maiúsculas diferentes (`C:\Users\RUNNER~1` contra `C:\Users\runneradmin`).
- Ao fim: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Testes do seletor independentes de sistema operacional

**Files:**
- Modify: `tests/test_dashboard_projects_api.py`
- Modify: `tests/test_projects_registration.py`

Em `tests/test_dashboard_projects_api.py`, a fixture `projects_env` define só `HOME` para apontar a pasta pessoal para `tmp_path / "home"`; defina também `USERPROFILE` com o mesmo valor (`monkeypatch.setenv("USERPROFILE", str(home))`), para o caso parametrizado `forbidden` valer no Windows.

Em `tests/test_projects_registration.py`, o teste `test_start_server_registration_failure_still_writes_state` compara o primeiro argumento de `write_state` com `str(project.resolve())` por igualdade exata. Troque por uma comparação que normalize os dois lados com `os.path.normcase(os.path.realpath(...))` (importe `os` no topo se ainda não estiver), pois no Windows a mesma pasta pode vir com maiúsculas diferentes.

Não altere os demais testes desses arquivos nem o comportamento do que é testado.

**Verification:** `pytest tests/test_dashboard_projects_api.py tests/test_projects_registration.py -q` passa; depois a suíte inteira.
