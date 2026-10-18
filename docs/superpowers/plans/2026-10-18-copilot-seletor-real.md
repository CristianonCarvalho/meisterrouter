# Leitura do `/model` do Copilot funciona com a tela real Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** verificação real da run da fase 2 (`2026-10-18-config-modelos-por-harness-fase-2.md`). O comando `meister config refresh-models copilot`, rodado contra o Copilot de verdade, abre o seletor mas não lê nenhum modelo (ele termina com erro em cerca de 10 s e não altera o arquivo de cache). Os testes da run passavam porque usavam um seletor simulado. Com a tela real capturada em `tests/fixtures/copilot_model_selector.raw` (bytes crus do terminal depois de `/model`), `parse_selector(strip_ansi(...))` devolve 0 modelos: a tela real traz o nome colado ao tier (`Claude Haiku 5.5Lightweight   100K       Medium`), um marcador de rolagem `┃` no fim de cada linha, o caractere de controle `\x08` no meio de uma linha, e linhas em branco entre as linhas de modelo.

**Goal:** `parse_selector` lê o seletor real do Copilot: os 18 modelos (`Claude Haiku 5.5`, `GPT-6 Luna`, `GPT-5.6 Terra`, `Claude Sonnet 5.5`, `GPT-5.6 Luna`, `GPT-5.4`, `GPT-5.4 mini`, `GPT-5.3-Codex`, `GPT-5 mini`, `Claude Sonnet 5`, `Claude Haiku 4.5`, `MAI-Code-1.1-Flash`, `Gemini 3.8 Flash`, `Gemini 3.7 Flash`, `Grok 4.7`, `Grok 4.6`, `Grok 4.5`, `Kimi K3`) com grupo, tier, contexto e esforço, os 4 indisponíveis (`gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-astra`, `gpt-5.6-sol`) e os 2 modelos locais (`gpt-oss:20b`, `llama3.1:latest`).

**Architecture:** Uma tarefa de correção do analisador, com um teste que usa a captura real como fixture.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Nenhum texto novo.

### Task 1: Analisar a tela real do seletor do Copilot

**Files:**
- Modify: `meister/copilot_models.py`
- Modify: `tests/test_copilot_models.py`

Em `meister/copilot_models.py`, faça `parse_selector` (e o que ele usa: `strip_ansi`, `_row_fields`, `_row`) aceitar a tela real: depois de remover as sequências ANSI, descarte `\x08` e o marcador de rolagem `┃` (e o que mais for só moldura) de cada linha; aceite o tier colado ao nome (os tiers conhecidos são `Lightweight`, `Versatile` e `Powerful`; ancore neles em vez de separar por espaço), com contexto (`100K`, `272K` ou `—`) e esforço (`Low`, `Medium`, `High` ou `—`) em seguida; mantenha `✓` (selecionado) e `(default)`. As linhas de modelo vêm separadas por linhas em branco. Os grupos são `Recent models`, `Recommended models`, `Other models` e `Unavailable models` (nesta os itens são só o id seguido de `— —`); depois vem o bloco de provedores locais (Ollama), do qual só se aproveitam os ids `nome:tag` para `local_ollama`. Não confunda o texto de ajuda da tela (por exemplo "Auto is 10% off right now", "Optimize for", "Category Tier") com modelos.

Em `tests/test_copilot_models.py` acrescente testes que leem `tests/fixtures/copilot_model_selector.raw` (use `pathlib` a partir do arquivo do teste; leia como bytes e decodifique em UTF-8 com `errors="replace"`) e conferem, sem simulação: exatamente os 18 nomes acima, na ordem da tela, com `GPT-6 Luna` selecionado, `GPT-5.6 Terra` como padrão, os grupos corretos (`recent`: 2, `recommended`: 2, `other`: 14), contexto nulo para `GPT-5.4 mini` e esforço nulo para `Claude Haiku 4.5`, os 4 indisponíveis e os 2 locais; e que o id gerado de cada nome (`model_id_from_name`) é o esperado (por exemplo `claude-haiku-5.5`, `gpt-5.4-mini`, `mai-code-1.1-flash`). Os testes que já existem (com o seletor simulado) continuam passando. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.
