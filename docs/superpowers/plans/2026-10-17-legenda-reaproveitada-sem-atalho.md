# Legenda da task reaproveitada sem atalhos Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** revisão da run ee4613dc (`2026-10-17-timeline-tasks-reaproveitadas.md`). A Task 2 daquele plano não tinha `meister/dashboard/server.py` no escopo, mas `_timeline_messages` é uma lista explícita de chaves: a chave nova `legend_reused` não chegava à página. O worker contornou com um monkeypatch de `_timeline_messages` em `tests/conftest.py` (que só existe nos testes) e com textos fixos no HTML. Este plano remove os atalhos e faz a chave chegar de verdade.

**Goal:** A legenda e o rótulo do marcador da task reaproveitada vêm do catálogo de idiomas (`cli.timeline.web.legend_reused`), expostos por `_timeline_messages`, sem texto fixo no HTML e sem remendo em `tests/conftest.py`.

**Architecture:** Uma tarefa, com o escopo que faltou.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Nenhum texto novo nos catálogos (a chave já existe nos dois idiomas).

### Task 1: Expor `legend_reused` e remover os atalhos

**Files:**
- Modify: `meister/dashboard/server.py`
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `tests/conftest.py`
- Modify: `tests/test_timeline_reused_bar_js.py`

Em `_timeline_messages` (`meister/dashboard/server.py`), acrescente `"legend_reused"` à lista de chaves (junto de `critical_path`, `escalation` e `lateral_fallback`), para que `cli.timeline.web.legend_reused` chegue à página. Em `tests/conftest.py`, remova por completo o bloco que substitui `_dashboard_server._timeline_messages` (`_orig_timeline_messages`, `_patched_timeline_messages` e a atribuição), junto com os dois imports que só ele usa (`meister.dashboard.server as _dashboard_server` e `t as _t`); o arquivo deve ficar igual ao que era antes da run ee4613dc, sem outra mudança. Em `meister/dashboard/templates/timeline.html`: na legenda, troque `{{ messages.legend_reused or ('Reaproveitada' if language == 'pt-BR' else 'Reused') }}` por `{{ messages.legend_reused }}`; no desenho do marcador da task reaproveitada, troque a constante `"reaproveitada"` por `text.legend_reused` (o texto vem do catálogo, no idioma ativo) e calcule a largura necessária para decidir se o rótulo cabe a partir desse texto; nada mais muda no desenho. Em `tests/test_timeline_reused_bar_js.py`, garanta (sem monkeypatch) que a página servida (`/timeline`, com o idioma `en` e com `pt-BR`) contém o texto da legenda do idioma ativo ("Reused", "Reaproveitada") e que não contém a expressão `'Reaproveitada' if language`; os demais testes do arquivo seguem passando. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim; `tests/test_timeline_messages_keys.py` tem de passar sem o remendo do `conftest.py`.
