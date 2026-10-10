# Hosts de worker, fase 5: o Herdr deixa de ser pré-requisito Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois do plano `2026-10-10-hosts-fase2-adaptador-process.md`** (o host `process` precisa existir para o Meister funcionar sem Herdr). Convém executar depois da fase 3 para o README poder citar `meister logs`. A fase 4 é opcional para esta. Origem: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`, seção 10, fase 5.
>
> **Hot files:** `meister/setup_cmd.py` e `bin/install.sh`. Não rodar junto de outro plano que os modifique.

**Goal:** Tornar o Herdr opcional para quem instala e configura: `bin/install.sh` e `meister setup` deixam de abortar sem ele, mostram qual host será usado, e o README, o diagrama e a metadata do pacote passam a descrever o Meister como independente de terminal, com o Herdr (e o tmux) como opções.

**Architecture:** Só instalação, diagnóstico e texto. O `meister setup` continua fazendo o que já fazia quando o Herdr existe (vincular o plugin e escrever os atalhos); sem Herdr, pula esses dois passos com um aviso e segue para os demais diagnósticos (CLIs de worker, chave do Jev, validade da configuração). Uma função pequena, `describe_host`, diz qual host o `auto` escolheria e por quê, e é usada pelo diagnóstico.

**Tech Stack:** Python ≥ 3.10, `click`, shell POSIX, `pytest`. Nenhuma dependência nova.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| `meister setup` sem Herdr | Não aborta. `check_herdr` devolve `"aviso"` (não `"erro"`) com mensagem nova: o Herdr é opcional; sem ele os workers rodam como processos em segundo plano. Os passos de plugin e de atalhos são pulados com uma linha informativa. O código de saída só é `1` por outros erros. |
| Diagnóstico do host | Novo item `host` no diagnóstico: `process (Herdr não encontrado)`, `herdr (socket acessível)` ou o valor explícito de `runtime.host`. Vem de `describe_host(config)`, em `meister/hosts/select.py`, sem criar processo nem conectar. |
| `install.sh` sem Herdr | Não aborta; imprime um aviso curto e segue. `MEISTER_ALLOW_NO_HERDR=1` continua aceita e passa a não ter efeito (compatibilidade com quem a exporta). O passo que vincula o plugin ao Herdr só roda se o Herdr existir. A mensagem final muda conforme o caso. |
| Textos | O Herdr aparece como "opcional, recomendado para ver os workers ao vivo". Nenhum texto afirma que o Meister "é um plugin do Herdr". |
| Fora deste plano | O `CLAUDE.md` da raiz deste repositório (regra do dono: mudança em arquivo de instrução do projeto só com pedido explícito), `herdr-plugin.toml` (continua descrevendo o plugin do Herdr) e a renomeação do pacote `meister/herdr/`. |

## Global Constraints

- Quem tem o Herdr instalado não percebe diferença: o `meister setup` e o instalador fazem os mesmos passos de antes.
- Sem dependência nova; compatível com Python 3.10.
- Mensagens por `t()` com entradas em **todos** os catálogos de `meister/locales/`; `tests/test_i18n_ratchet.py`, `tests/test_locales_parity.py` e `tests/test_no_hardcoded_models.py` verdes.
- Documentação em inglês (`README.md`, `docs/`) **e** português (`README.pt-BR.md`, `docs/pt-BR/`), com links válidos (`tests/test_docs_links.py`).
- `bin/install.sh` continua válido para `sh -n` e `bash -n`, e sem criar nada fora do que cria hoje.
- `ruff check .` e `mypy meister` limpos; a suíte inteira verde ao fim de **cada** tarefa.
- Testes sem rede, sem tocar `~/.meister` nem `~/.config`.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Descrever o host escolhido

**Files:**
- Modify: `meister/hosts/select.py`
- Modify: `meister/hosts/__init__.py`
- Test: `tests/test_hosts_describe.py`

**Depends on:** none

`describe_host(config, *, socket_path=None) -> tuple[str, str]` devolve o nome do host que `select_host` escolheria (`"herdr"`, `"process"` ou `"tmux"`) e uma chave de motivo (`"auto_socket"`, `"auto_no_socket"`, `"explicit"`), **sem** instanciar adaptador nem abrir conexão (use apenas `is_herdr_available` e `shutil.which`). Para `runtime.host: herdr` sem socket e para `tmux` sem binário, o nome é o pedido e o motivo é `"explicit_unavailable"`. Testes: cada combinação, com `is_herdr_available` e `shutil.which` simulados.

### Task 2: `meister setup` sem Herdr

**Files:**
- Modify: `meister/setup_cmd.py`
- Modify: `meister/locales/en_commands.py`
- Modify: `meister/locales/pt_br_commands.py`
- Test: `tests/test_setup_cmd.py`
- Test: `tests/test_setup_cli.py`

**Depends on:** Task 1

Em `check_herdr` (~425), troque o retorno de ausência para `DiagnosticItem("herdr", "aviso", t("commands.setup.herdr_optional"))` (chave nova: o Herdr é opcional, sem ele os workers rodam como processos em segundo plano; manter a chave antiga `commands.setup.herdr_missing` só se algum teste a citar, senão removê-la). Em `run_setup` (~587), remova o encerramento antecipado quando o Herdr falta: pule `ensure_herdr_plugin` e a escrita dos atalhos com uma linha informativa traduzida e siga para os demais diagnósticos. Acrescente o item `host` (de `describe_host`) à lista de diagnósticos, com mensagem traduzida por motivo. O código de saída só é `1` se houver item `"erro"`. Os testes existentes do caminho com Herdr passam sem mudar asserções; acrescente: sem Herdr o setup sai com 0 e imprime o aviso e o host `process`; com Herdr o fluxo é o de antes; `--dry-run` sem Herdr não tenta vincular nada.

### Task 3: `install.sh` sem Herdr

**Files:**
- Modify: `bin/install.sh`
- Test: `tests/test_install_version.py`

**Depends on:** none

Na verificação prévia (linhas ~96–106), troque o aborto por um aviso curto (o Herdr é opcional; link de instalação) e prossiga; `MEISTER_ALLOW_NO_HERDR` deixa de ser consultada, mas o script ainda a aceita sem erro. Execute o passo que vincula o plugin ao Herdr e os atalhos **somente** se `command -v herdr` for verdadeiro; senão imprima a linha "pulado: Herdr não encontrado". A mensagem final (~220) passa a dizer como começar sem o Herdr (`meister setup --project` e `meister orchestrate --plan-file ...`) e, se o Herdr existir, mantém a linha atual. Veja em `tests/test_install_version.py` como o script é exercitado hoje (ambiente de teste com `PATH` controlado) e acrescente casos: sem Herdr no `PATH` o script termina com sucesso e não chama `herdr`; com um `herdr` falso no `PATH` o comportamento é o anterior.

### Task 4: README

**Files:**
- Modify: `README.md`
- Modify: `README.pt-BR.md`

**Depends on:** none

Reescreva o primeiro parágrafo e a tabela de pré-requisitos: o Herdr passa de "Required" para "Optional (recomendado para ver cada worker ao vivo)" e a linha do harness de IA e a do Python/Git ficam como estão; remova o bloco `> [!IMPORTANT]` que afirma que o Herdr é pré-requisito e que a instalação aborta sem ele; na seção de instalação, troque "aborts … if Herdr is not installed" por "avisa e segue". Acrescente uma seção curta "Where workers run" com `runtime.host` (`auto`, `process`, `herdr`, `tmux`), a frase de que sem Herdr os workers rodam em segundo plano, e como acompanhar (`meister timeline`, `meister dashboard`, `meister logs -f`, `meister wait`). Nos dois idiomas, com os mesmos links.

### Task 5: Diagramas e fluxo

**Files:**
- Modify: `docs/diagrams.md`
- Modify: `docs/pt-BR/DIAGRAMAS.md`
- Modify: `docs/flow.md`
- Modify: `docs/pt-BR/FLUXO_MEISTERROUTER.md`

**Depends on:** none

Procure por `grep -n -i "herdr"` nesses quatro arquivos e ajuste onde o texto ou o diagrama (Mermaid) apresenta o Herdr como o único lugar onde os workers rodam: acrescente o "host de worker" como caixa genérica (com Herdr, processo local e tmux como opções) no diagrama de componentes e na sequência de execução, e corrija frases como "sem Herdr o Meister não roda". Não altere os diagramas de estados, de roteamento nem de gates. Mantenha os dois idiomas equivalentes.

### Task 6: Metadata do pacote e CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `setup.py`
- Modify: `package.json`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`

**Depends on:** Task 3

Atualize as descrições (`pyproject.toml`, `setup.py` e `package.json`) para algo como "Multi-model orchestration for AI coding CLIs: runs workers as background processes, in Herdr or in tmux" (mesma frase nos três). No CHANGELOG, seção `[Unreleased]`, registre em inglês e em português: o novo `runtime.host`, o adaptador `process`, o adaptador `tmux`, `meister logs`, a variável `MEISTER_WORKER` (com `MEISTER_IN_PANE` mantida), e a mudança de que `meister setup` e `install.sh` não exigem mais o Herdr (marque `MEISTER_ALLOW_NO_HERDR` como sem efeito). Confira `tests/test_version.py` e os testes de higiene do CHANGELOG (`tests/test_changelog_hygiene.py`), que devem continuar passando.
