# Segurança local e documentação Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Origem:** análise global de 2026-10-09 (itens 6, 7 e 10). Este plano toca `meister/cli.py` e os catálogos `meister/locales/*_cli.py`, além de documentação; é independente dos planos `2026-10-09-integridade-do-trabalho.md` e `2026-10-09-ci-e-testes.md`. **Atenção:** o plano `2026-10-08-tiers-neutros.md` também mexe em `meister/cli.py` e nos catálogos; execute-o antes ou depois, nunca ao mesmo tempo.

**Goal:** (1) Documentar e travar com teste exatamente o que o Meister envia ao Jev (OpenRouter). (2) O dashboard deixa de aceitar `--host` fora de loopback sem uma confirmação explícita. (3) README, `ARCHITECTURE.md` e CHANGELOG deixam de afirmar coisas desatualizadas.

**Architecture:** O envio ao Jev já é pequeno e tipado: `classify_task` manda `state = {"task_description": context}` mais as descrições das vias, e `control_cycle` manda resumo do diff (`git diff --stat`, ou seja, caminhos e contagens), resultado dos testes, tentativas e `security_sensitive` (`meister/jev.py`). Falta documentar isso e travar o contrato com um teste. O guarda do dashboard é uma flag nova na CLI. A documentação fica em inglês em `docs/` e `README.md` e em português em `docs/pt-BR/` e `README.pt-BR.md` (o teste `tests/test_docs_links.py` mantém os links válidos nos dois).

**Tech Stack:** Python ≥ 3.10, `click`, `pytest`. Nenhuma dependência nova.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Redação de dados enviados ao Jev | **Fora deste plano.** Há dissidência (risco pequeno para planos comuns). A Tarefa 1 só documenta e trava o contrato; decidir redação depende de revisar a página nova. |
| `--host` fora de loopback | Passa a exigir `--allow-remote`; sem a flag, o comando termina com erro e mensagem explicando. Com a flag, imprime um aviso e sobe. Loopback é `127.0.0.1`, `::1` e `localhost`. Mudança incompatível, registrada no CHANGELOG. |
| Plataformas | Declarar macOS e Linux; Windows não suportado (o código usa `fcntl`, `meister/cli.py` ~977). |

## Global Constraints

- Sem dependência nova; compatível com Python 3.10.
- Strings de interface passam por `t()` com entradas em **todos** os idiomas de `meister/locales/`; `tests/test_locales_parity.py` e `tests/test_i18n_ratchet.py` ficam verdes. `tests/test_no_hardcoded_models.py` continua passando: nenhum nome de modelo em `meister/cli.py`.
- Documentação nova em inglês (`docs/`) **e** português (`docs/pt-BR/`), com links relativos válidos nos dois.
- Testes sem rede, sem tocar `~/.meister`, sem chamar CLI de IA; chamadas HTTP simuladas com `monkeypatch`.
- Suíte, `ruff check .` e `mypy meister` limpos.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Documentar e travar o que é enviado ao Jev

**Files:**
- Create: `tests/test_jev_payload_contract.py`
- Create: `docs/jev-data-sent.md`
- Create: `docs/pt-BR/DADOS_ENVIADOS_AO_JEV.md`
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`

**Depends on:** none

0. **Estado atual do envio (depois do PR #106):** `classify_task` não manda mais o nome das vias ao Jev: manda **chaves opacas** (`lane_a`, `lane_b`... em `_opaque_lane_keys`) com a descrição de cada via (`modelo via harness ($custo/M): best_for`), e traduz a resposta de volta para o nome real. O contrato e a página devem descrever isso (campos, chaves opacas e o motivo: nomes `tier_N` faziam o Jev subir de via).
1. Teste: simule `requests.post` (ou a função de transporte usada por `call_decisions`) e capture o corpo enviado por `classify_task` e por `control_cycle`. Afirme o **conjunto exato** de campos (nomes e tipos) de cada chamada, de modo que acrescentar um campo novo quebre o teste e force revisar a documentação. Não registre nem imprima a chave de API; afirme que ela só aparece no cabeçalho de autorização e nunca no corpo.
2. Documentação (inglês e português): uma página curta com, para `classify` e `control`, uma tabela campo → origem → exemplo, um aviso explícito de que `task_description` é o texto da tarefa do plano e o resumo do diff contém caminhos de arquivos, e como evitar o envio (`router: {mode: first}` e o comportamento sem chave, conforme `docs/advanced.md`). Confira cada afirmação lendo `meister/jev.py` e `meister/jev_context.py`; não descreva nada que o código não faz.
3. Acrescente um link para a página nova em `docs/advanced.md` e em `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`.

### Task 2: Guarda para `meister dashboard --host`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_dashboard_host_guard.py`

**Depends on:** none

Em `meister dashboard` (`meister/cli.py` ~382), o `--host` aceita qualquer endereço e o servidor não tem autenticação. Acrescente a flag `--allow-remote`. Se `--host` não for loopback e a flag não vier, saia com código de erro diferente de zero e uma mensagem (via `t()`) que diga que o dashboard não tem autenticação e como prosseguir. Com a flag, imprima um aviso e siga. O modo `--tui` não sobe servidor e não é afetado. Testes com `CliRunner` e `start_server` simulado (nenhum servidor real sobe): loopback sem flag funciona; `0.0.0.0` sem flag falha; `0.0.0.0` com flag avisa e chama `start_server`.

### Task 3: Corrigir README, ARCHITECTURE e CHANGELOG

**Files:**
- Modify: `README.md`
- Modify: `README.pt-BR.md`
- Modify: `ARCHITECTURE.md`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`

**Depends on:** Task 1, Task 2

1. **README (inglês e português):** o aviso "the CLI's own messages are currently in Portuguese" está desatualizado (o padrão é inglês e `language: pt-BR` troca o idioma; confira em `meister/default_config.yaml` e `meister/i18n.py`). Reescreva-o dizendo isso. Acrescente à seção de pré-requisitos uma linha de plataformas: macOS e Linux; Windows não é suportado (o `meister` usa travas de arquivo POSIX).
1b. **README (inglês e português), tabela de exemplo do `meister models`:** a amostra mostra rótulos em português (`ligada`, `desligada`, `CUSTO/1M`, `ESFORÇO`) e a nota "Column labels are in Portuguese", mas a saída real com o padrão `language: en` é `enabled`/`disabled`, `COST/1M`, `EFFORT` (confira com `meister models`). Atualize a amostra e a nota (inglês no README; no `README.pt-BR.md`, mostre os rótulos de `language: pt-BR`).
2. **`ARCHITECTURE.md`:** remova nomes e preços de modelos fixos no texto e no diagrama (hoje cita Claude 4.5 Haiku, GPT-4o e outros que não batem com a configuração atual) e aponte para `docs/models-and-costs.md` e para `meister models`. Troque nomes de modelos por descrições genéricas ("via de menor custo", "via de raciocínio profundo") ou pelos nomes de via `tier_N` (o plano `2026-10-08-tiers-neutros.md` já foi executado e as vias foram renomeadas). Não mude a lógica descrita (estados `SMALL/MEDIUM/HIGH/ESCALATE`, ações do `control`).
3. **CHANGELOG (inglês e português), seção `[Unreleased]`:** entrada para a flag `--allow-remote` marcada como mudança incompatível, e entrada para a página de dados enviados ao Jev.

Critério de aceite: `tests/test_docs_links.py` e `tests/test_no_hardcoded_models.py` passam; `grep -n "Claude 4.5\|GPT-4o" ARCHITECTURE.md` não retorna nada.
