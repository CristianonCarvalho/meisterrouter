# Integridade do trabalho dos workers Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Origem:** análise global de 2026-10-09 (itens 1, 2, 3 e 13). Este plano não toca `meister/cli.py`, `.github/`, `README*.md`, `ARCHITECTURE.md` nem `CHANGELOG*.md`; pode rodar em paralelo com `2026-10-09-ci-e-testes.md` e `2026-10-09-seguranca-e-documentacao.md`. As entradas do CHANGELOG deste plano são escritas pelo orquestrador no fim.

**Goal:** (1) Provar ou refutar, com testes, que algum caminho de integração do `worktree.py` descarta conteúdo único do worker sem arquivá-lo, e corrigir só o que for provado. (2) Falhas de limpeza deixam de ser silenciosas: aparecem como aviso e como contagem no `meister report`. (3) `current_run.json` passa a ser gravado de forma atômica.

**Architecture:** A limpeza por tarefa (`WorktreeManager.cleanup_worktree`) **já arquiva** alterações soltas (`_archive_uncommitted`) e commits não integrados (`_archive_branch_if_unmerged`). Os caminhos ainda sem arquivamento são o `rollback_merge` (`reset --hard` e `clean -fd`, `worktree.py` ~468), a reutilização da integração em `start_integration` (`reset --hard HEAD`, `clean -fd` e `rmtree`, ~1193) e o `branch -D` do retry de `create_worktree` (~340). A hipótese a verificar é que nenhum deles perde conteúdo do worker (o worker tem o próprio branch arquivável); por isso a Tarefa 1 só caracteriza e a Tarefa 2 só corrige o que falhar.

**Tech Stack:** Python ≥ 3.10 (CI roda 3.10 a 3.13), `pytest`, `git`. Nenhuma dependência nova.

## Global Constraints

- Sem dependência nova; compatível com Python 3.10.
- Reaproveitar `_archive_uncommitted` e `_archive_branch_if_unmerged`; não criar outro mecanismo de arquivamento. Se o arquivamento falhar, o passo destrutivo **não** roda (mesmo failsafe de `cleanup_worktree`).
- Strings de interface e de log passam por `t()` com entradas em `meister/locales/en_engine.py` **e** `meister/locales/pt_br_engine.py` (ou `*_reports.py`); `tests/test_locales_parity.py` e `tests/test_i18n_ratchet.py` ficam verdes.
- Testes sem rede, sem tocar `~/.meister`, sem chamar CLI de IA; repositórios git temporários em `tmp_path` (veja `tests/test_worktree_archive_uncommitted.py` para o padrão).
- Suíte, `ruff check .` e `mypy meister` limpos.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Caracterizar a perda de trabalho nos caminhos de integração

**Files:**
- Create: `tests/test_worktree_integration_data_loss.py`

**Depends on:** none

Escreva testes que verificam a propriedade: **nenhum commit ou arquivo que exista só no worktree ou no branch afetado deixa de ser alcançável** (por branch, por ref `refs/meister/archive/*` ou por arquivo preservado) depois de cada caminho abaixo.

1. `WorktreeManager.rollback_merge`: integração com um branch de worker mesclado; o rollback volta ao SHA anterior. O commit do worker continua alcançável. Inclua também um arquivo não rastreado e uma alteração rastreada soltos no worktree de integração e registre no teste se sobrevivem ou são arquivados.
2. `IntegrationPipeline.start_integration` com reutilização (branch de integração já tem commits além da base): com alteração rastreada e arquivo novo soltos no worktree de integração, chame de novo com o mesmo `run_id` e registre o que sobrevive. Cubra também o ramo em que o worktree não está registrado e é removido com `shutil.rmtree`.
3. `WorktreeManager.create_worktree` com falha transitória de `git worktree add` (simule com `monkeypatch` em `_run_git`, usando um dos padrões de `_transient_patterns`): se existir um branch homônimo com commit exclusivo, o commit continua alcançável depois do retry.

Para cada cenário cujo teste **falhar** hoje, marque o teste com `@pytest.mark.xfail(strict=True, reason="<qual conteúdo se perde>")`; cenários que passam ficam como guarda de regressão, sem marca. Não altere `meister/worktree.py` nesta tarefa. Ao terminar, imprima uma tabela cenário → "preserva" ou "perde: <o quê>".

### Task 2: Proteger o que a caracterização provou vulnerável

**Files:**
- Modify: `meister/worktree.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_worktree_integration_data_loss.py`

**Depends on:** Task 1

Para cada teste marcado `xfail(strict=True)` na Tarefa 1: antes do comando destrutivo correspondente, arquive o conteúdo com `_archive_uncommitted` e/ou `_archive_branch_if_unmerged`; se o arquivamento falhar, não execute o comando destrutivo e registre `logger.error` com chave nova `engine.worktree.*` (inglês e português). Depois remova o `xfail` do teste, que deve passar. Se **nenhum** teste foi marcado `xfail`, não altere `meister/worktree.py`: apenas imprima "nenhuma correção necessária" e liste os cenários guardados.

Critério de aceite: `grep -n '"reset", "--hard"\|"clean", "-fd"\|"branch", "-D"' meister/worktree.py` só retorna chamadas precedidas de arquivamento ou comprovadamente sem conteúdo único (justifique cada uma em comentário curto no código), e nenhum teste da Tarefa 1 permanece `xfail`.

### Task 3: Falhas de limpeza em `warning`, com contagem no relatório

**Files:**
- Modify: `meister/worktree.py`
- Modify: `meister/report.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Modify: `meister/locales/en_reports.py`
- Modify: `meister/locales/pt_br_reports.py`
- Test: `tests/test_report.py`
- Test: `tests/test_worktree.py`

**Depends on:** Task 2

Hoje a falha de `git worktree remove` em `cleanup_worktree` e a falha de `reset`/`clean` em `start_integration` são registradas só com `logger.debug` (`worktree.py` ~870 e ~1196), e `cleanup_worktree` ainda cai em `shutil.rmtree(..., ignore_errors=True)`.

1. Troque `logger.debug` por `logger.warning` nesses dois pontos (mantendo as chaves `t()` existentes).
2. Emita um evento de telemetria `cleanup_failure` (use a API de eventos de `meister/logger.py`, `log_event`) com `task_id`, `path` e o motivo, em cada falha de limpeza que hoje é engolida.
3. Em `meister report`, mostre a contagem de `cleanup_failure` da run (linha nova na tabela de métricas, com rótulos em inglês e português; run sem eventos mostra 0).
4. Testes: uma falha simulada de `worktree remove` gera um aviso e o evento; o relatório de uma run com o evento mostra a contagem; sem eventos mostra 0.

Não mude o comportamento de sucesso/falha retornado por `cleanup_worktree`.

### Task 4: Escrita atômica de `current_run.json`

**Files:**
- Modify: `meister/logger.py`
- Test: `tests/test_logger.py`

**Depends on:** none

`save_current_run` grava `current_run.json` com `open(path, "w")` direto, em dois lugares (`logger.py` ~94 a ~114). Uma interrupção no meio deixa o arquivo truncado e `get_current_run` perde a correlação da run. Escreva em um arquivo temporário no mesmo diretório e use `os.replace`; mantenha o `except Exception` externo (a gravação continua best-effort) e o `.gitignore` de `.meister`. Teste: simule falha durante a escrita (`monkeypatch` em `json.dump`) e verifique que o arquivo anterior permanece íntegro e legível por `get_current_run`.
