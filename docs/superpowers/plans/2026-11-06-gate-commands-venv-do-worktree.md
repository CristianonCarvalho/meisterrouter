# Gate com `gate.commands` usa o `.venv` do worktree Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** pendência deixada pela correção de 2026-11-05 (commit `6463958`, "Bug no gate: o Python do pytest ignora o .venv do worktree verificado"). Aquela correção fez `_resolve_python(path)` e `_project_python_bin(python, path)` preferirem o `.venv`/`venv` do worktree verificado. No caminho padrão (`_run_verification_uncached`), `prepare_environment(path)` roda **antes** de `_resolve_python(path)`, então o `.venv` criado pelo `gate.install` (ex.: `uv sync`) já existe e é escolhido. Em `_run_configured_commands`, usado quando `gate.commands` está configurado, a ordem é a inversa.

## O defeito

Em `meister/gate.py`, `DeterministicGate._run_configured_commands` (linha ~918 na `main` em `151a480`):

```
927:  python, python_warning = self._resolve_python(path)      # 1º escolhe o Python
935:  python_bin = self._project_python_bin(python, path)
937:  if self.config.environment.install_dependencies:
938:      installed, install_output = prepare_environment(path, self.config)   # 2º cria o .venv
```

Num worktree novo (sem `.venv`), `_resolve_python(path)` não acha `<worktree>/.venv`, porque ele ainda não existe, e cai no `.venv` do repositório principal (ou no Python do Meister, com aviso). Só depois o `gate.install` cria `<worktree>/.venv`, que fica sem uso. Efeitos:

- `{python}` nos comandos de `gate.commands` é substituído pelo Python do repo principal.
- O `PATH` recebe o `bin` do `.venv` do repo principal, então ferramentas como `pytest`, `ruff` e `mypy` chamadas pelo nome também vêm de lá.
- Num projeto com src layout instalado em modo editable, o `.pth` desse `.venv` aponta para `<repo principal>/src`, e um módulo novo criado pela subtarefa dá `ModuleNotFoundError`. Caso real, no caminho padrão antes da correção anterior: projeto `taxlaw`, `No module named 'taxlaw.guard'`. O reparo do gate "resolveu" com `sys.path.insert` no teste, e o contorno precisou ser removido à mão.

**Como reproduzir:** num projeto Python com src layout, `gate.install: "uv sync --quiet"` e `gate.commands: [{name: tests, run: "{python} -m pytest -q"}]`, rode um plano cuja tarefa crie um módulo novo no pacote e um teste que o importe. O gate reprova com `ModuleNotFoundError`, mesmo com o código certo.

## Correção esperada

**Goal:** em `_run_configured_commands`, o `gate.install` roda antes da escolha do Python, e `{python}` e o `PATH` usam o `.venv` do worktree quando o install o cria.

**Architecture:** Só reordenar `_run_configured_commands`: o bloco `if self.config.environment.install_dependencies: prepare_environment(...)` sobe para antes do `try: _resolve_python(path)`, e o retorno de falha de instalação continua igual. Nenhuma mudança em `_resolve_python`, `_project_python_bin`, `prepare_environment` nem no caminho padrão.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`.

## Global Constraints

- Só `meister/gate.py` e `tests/test_gate_python.py`.
- A ordem das saídas fica como hoje: o bloco `[INSTALL]` vem antes do aviso de fallback do Python (o aviso só aparece quando algum comando usa `{python}`).
- Falha de instalação continua devolvendo `VerificationResult(False, "[INSTALL] ...", infrastructure_error=True)` sem tentar resolver o Python. Python indisponível depois da instalação continua devolvendo o erro `engine.gate.python_unavailable` atual.
- Os testes existentes de `tests/test_gate_python.py` seguem passando sem alteração de expectativa.
- Nada de `sys.path`, `sys.modules`, `skip` ou `xfail` para fazer teste passar.
- Ao fim da tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Instalar antes de escolher o Python em `_run_configured_commands`

**Files:**
- Modify: `meister/gate.py`
- Modify: `tests/test_gate_python.py`

Em `meister/gate.py`, dentro de `_run_configured_commands`, mova o bloco de instalação (`if self.config.environment.install_dependencies:` com `prepare_environment(path, self.config)`, o retorno de falha e o `outputs.append(f"[INSTALL]\n{install_output}")`) para antes do `try: python, python_warning = self._resolve_python(path)`. O resto da função fica igual.

Testes de regressão em `tests/test_gate_python.py`, no estilo dos vizinhos (`_configured_gate`, `_fake_python`, `_executable`, `@needs_unix_shell`):

1. **O `.venv` criado pelo install no worktree é usado por `{python}`:** repo principal com `.venv/bin/python` falso; worktree, um diretório diferente do repo, **sem** `.venv`; `gate.install` aponta para um script que cria `<worktree>/.venv/bin/python` (um `_fake_python` que registra que foi chamado); `gate.commands` com `{python} -m pytest`; `environment.install_dependencies` ativo. Rodar a verificação no worktree deve chamar o Python do worktree, e não o do repo principal.
2. **O `PATH` usa o `bin` do `.venv` criado pelo install:** mesmo cenário, com um comando chamado pelo nome (como em `test_configured_path_uses_venv_before_node_modules_and_original`). A primeira entrada do `PATH` deve ser `<worktree>/.venv/bin`.
3. **Install que falha não resolve o Python:** `gate.install` que sai com código 1, e `gate.python` apontando para um caminho inexistente. O resultado é `[INSTALL] ...` com `infrastructure_error=True`, e não `python_unavailable`.
