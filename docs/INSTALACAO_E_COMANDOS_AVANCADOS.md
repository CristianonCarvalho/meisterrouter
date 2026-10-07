# Instalação alternativa e comandos avançados

Esta página reúne o que **não é necessário para o uso diário**. O caminho simples (instalar o Herdr,
rodar o instalador, `meister setup --project` e pedir o trabalho à sua LLM orquestradora) está no
[README](../README.pt-BR.md).

- [Instalação alternativa](#instalação-alternativa)
- [Atalhos do Herdr à mão](#atalhos-do-herdr-à-mão)
- [A chave do OpenRouter](#a-chave-do-openrouter)
- [Equipar um projeto (`init`, hooks e guard)](#equipar-um-projeto-init-hooks-e-guard)
- [Comandos que o `orchestrate` já usa por você](#comandos-que-o-orchestrate-já-usa-por-você) (`classify`, `control`, `worker`)
- [Plano e execução à mão (`plan`, `orchestrate`, `--resume`)](#plano-e-execução-à-mão-plan-orchestrate---resume)
- [Limpar branches antigas (`clean`)](#limpar-branches-antigas-clean)
- [Dashboard e linha do tempo: opções](#dashboard-e-linha-do-tempo-opções)
- [Configuração avançada](#configuração-avançada)
- [Desenvolvimento e testes](#desenvolvimento-e-testes)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Adaptador GitHub Copilot CLI](#adaptador-github-copilot-cli)

---

## Instalação alternativa

O instalador recomendado (`bin/install.sh`) clona o repositório em `~/.local/share/meisterrouter`, cria a
`.venv`, instala o pacote em modo editável, cria o executável `~/.local/bin/meister` e roda `meister setup`.
Estas são as outras formas de chegar ao mesmo resultado.

### Clone manual (Git / SSH)
```bash
# Via HTTPS:
git clone https://github.com/CristianonCarvalho/meisterrouter.git
cd meisterrouter
./bin/install.sh

# Ou via SSH:
git clone git@github.com:CristianonCarvalho/meisterrouter.git
cd meisterrouter
./bin/install.sh
```

### Fixar uma versão (tag)
O instalador recomendado instala a `main` por padrão. Na instalação por curl, fixe uma tag com `--version`:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash -s -- --version v0.9.0
```
`latest` seleciona a maior tag disponível; `main` seleciona o código mais recente da branch principal:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash -s -- --version latest
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash -s -- --version main
```
Também é possível definir `MEISTER_VERSION=v0.9.0` no ambiente; numa instalação por pipe, por exemplo:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | MEISTER_VERSION=v0.9.0 bash
```
O argumento `--version` prevalece se ambos forem usados. Se `~/.local/share/meisterrouter` já existir como clone, uma tag é buscada e selecionada nesse clone; com `main`, o instalador seleciona `main` e atualiza a branch. Ao executar dentro de um clone local, esse clone não é alterado e uma versão fixa solicitada é ignorada com um aviso.
Para clonar a tag manualmente, use:
```bash
git clone --branch v0.9.0 https://github.com/CristianonCarvalho/meisterrouter.git
cd meisterrouter && ./bin/install.sh
meister --version     # meister 0.9.0 (commit ...)
```
As versões e as notas estão em [Releases](https://github.com/CristianonCarvalho/meisterrouter/releases) e no [`CHANGELOG.md`](../CHANGELOG.md).

### Via Node.js / NPM
```bash
# Instalação global direto do GitHub:
npm install -g git+https://github.com/CristianonCarvalho/meisterrouter.git

# Ou diretamente no diretório clonado:
npm install -g .
```
O runner Node.js em `bin/cli.js` gerencia o runtime e o bootstrap do Python.

### Manual com Python (pip / venv)
```bash
git clone https://github.com/CristianonCarvalho/meisterrouter.git
cd meisterrouter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```
Depois rode `meister setup` para ligar o plugin ao Herdr e cadastrar os atalhos.

### Ligar o plugin ao Herdr à mão
```bash
herdr plugin link /caminho/para/meisterrouter      # ou ~/.local/share/meisterrouter
herdr plugin action list                           # confere as ações registradas
```
O plugin (`dev.meisterrouter.orchestrator`) registra as ações `auto-orchestrate`, `classify-task` e
`verify-gate`, e sobe o daemon junto com o Herdr.

---

## Atalhos do Herdr à mão

O `meister setup` grava os atalhos num bloco gerenciado do `~/.config/herdr/config.toml`. Se preferir
cadastrá-los você mesmo, adicione ao arquivo (troque `meister` pelo caminho completo, por exemplo
`~/.local/bin/meister`, se ele não estiver no `PATH` do Herdr) e depois rode `herdr config check` e
`herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+m"                      # orquestrar
type = "shell"
command = "meister herdr-action orchestrate"

[[keys.command]]
key = "prefix+shift+m"                # dashboard (TUI) em popup
type = "popup"
command = "meister dashboard --tui"
width = "85%"
height = "85%"

[[keys.command]]
key = "prefix+t"                      # linha do tempo (Gantt) em popup
type = "popup"
command = "meister timeline"
width = "85%"
height = "85%"
```

| Atalho | O que faz |
|---|---|
| `prefix+m` ou `ctrl+alt+m` | Inicia a orquestração autônoma no workspace |
| `prefix+shift+m` ou `ctrl+alt+shift+m` | Abre o **dashboard** (TUI) em popup |
| `prefix+t` ou `ctrl+alt+t` | Abre a **linha do tempo** (Gantt) em popup |

As versões `ctrl+alt+...` funcionam sem passar pelo `prefix`: repita os blocos com a outra tecla.
Dentro do dashboard em TUI, `t` abre a linha do tempo e `q` fecha o popup.

---

## A chave do OpenRouter

Só o **Jev** usa o OpenRouter (os workers rodam nas suas assinaturas). O Jev procura a chave, nesta ordem:
1. a variável de ambiente `OPENROUTER_API_KEY`;
2. um arquivo `.env` no diretório atual;
3. `~/.meister/.env`;
4. o `.env` do repositório do MeisterRouter.

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."          # ou
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.meister/.env
```
Sem chave, use `router: {mode: first}` no `meister.config.yaml`: a primeira via é escolhida sem rede.

---

## Equipar um projeto (`init`, hooks e guard)

`meister setup --project` já faz isto. O comando por baixo é:
```bash
meister init --target /caminho/do/seu/projeto [--hooks] [--force]
```
Cria no projeto, sem sobrescrever arquivos existentes:
- `CLAUDE.md` (instruções para Claude Code), `CODEX.md` (OpenAI Codex) e `AGENTS.md` (Cursor, Copilot e subagentes);
- o diretório de telemetria `.meister/logs/`.

Hooks são opcionais e só são instalados com `--hooks`. O hook Git `pre-commit` resume as alterações em
stage e executa os testes disponíveis (`npm test`, `pytest` ou `cargo test`); pode bloquear o commit se
falharem. Hooks existentes que não pertençam ao MeisterRouter são preservados; `--force` sobrescreve.
`--no-hooks` continua aceito por compatibilidade, sem efeito.

`--hooks` também instala os hooks do **Claude Code** em `.claude/`, incluindo o **guard**: ele impede que
o Claude edite código direto no projeto, porque o método de execução é sempre `meister orchestrate` (ou
`meister worker`). O modo fica em `.meister/guard_mode`:

| Modo | Efeito |
|---|---|
| `block` (padrão) | recusa a edição e orienta a usar `meister orchestrate` |
| `ask` | pede confirmação a cada edição de código |
| `off` | desliga o guard |

Documentação (`docs/**`, `*.md`, `*.mdx`, `*.txt`) é sempre liberada, e os workers do Meister
(`MEISTER_IN_PANE=1`) não são afetados. Detalhes em [`MANUAL_DE_EXECUCAO.md`](MANUAL_DE_EXECUCAO.md).

---

## Comandos que o `orchestrate` já usa por você

Você não precisa deles no dia a dia; servem para testar ou depurar uma peça isolada.

### `classify`
O `orchestrate` chama o Jev para cada subtarefa e escolhe a via inicial. Manualmente:
```bash
meister classify --context "Adicionar filtro de busca por data no painel"
```
Retorna JSON tipado com a complexidade (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) e o implementador
recomendado entre as vias configuradas em `tier_order`.

### `control`
O `orchestrate` chama o Jev no fim, antes do fast-forward da `main`. Manualmente:
```bash
meister control --diff-summary "Adicionado input e testes no componente Filter.tsx" --test-result pass
```
Retorna a ação autorizada: `COMPLETE` (pode comitar e concluir), `RETRY` (tentar de novo) ou
`switch_implementer` (trocar para o próximo modelo da hierarquia).

### `worker`
O `orchestrate` executa os workers por funções internas; o comando `worker` roda **uma tarefa isolada**, sem plano:
```bash
# Execução pela via escolhida:
meister worker --model copilot_luna --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"

# Se o modelo falhar ou estiver inativo, escale para a próxima via:
meister worker --model codex_luna --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
```
> ⚠️ **Zero implementação direta pelo orquestrador.** Modelos arquitetos (Claude, Codex) não devem escrever
> código de implementação quando houver um worker configurado. Se a via recomendada falhar, a regra é o
> escalonamento em cascata pela ordem de `workers.tier_order`. O arquiteto só implementa se **todas** as vias
> estiverem comprovadamente inacessíveis.

### Outros comandos de apoio
```bash
meister report [--run-id ID] [--format table|json|markdown]   # custo, tempo e tentativas por run (somente leitura)
meister replay RUN_ID [--json]          # replay e auditoria determinística dos eventos de um run
meister config show | validate          # exibe e valida a configuração ativa
meister daemon --status                 # estado do daemon (sobe sozinho com o Herdr)
meister install-hooks --target . --claude --git   # só os hooks, sem o init
```

---

## Plano e execução à mão (`plan`, `orchestrate`, `--resume`)

No uso direto a sua LLM orquestradora faz isto por você (veja o [README](../README.pt-BR.md)). À mão, o fluxo é:
```bash
meister plan import plano.md -o plano.json     # Markdown (formato do Superpowers) -> JSON canônico
meister plan validate plano.json               # confere esquema, dependências e chaves proibidas
meister plan analyze plano.json                # paralelismo previsto e aviso de plano serial
meister orchestrate --plan-file plano.json     # executa; --quiet suprime o progresso e o resumo
```
O formato do Markdown, as regras de `Files:`/`Depends on:` e o JSON canônico estão em
[`FORMATO_DO_PLANO.md`](FORMATO_DO_PLANO.md). `plan import` aceita `--deps sequential|files` e `--allow-unscoped`.

O `orchestrate` mostra o progresso de cada tarefa e um resumo final em stderr; a frase de resultado continua em
stdout. Para reaproveitar as tarefas concluídas de um run anterior depois de editar o plano, use `--resume`
(escolhe o run FAILED/RUNNING elegível mais recente do mesmo diretório) ou informe o identificador:
```bash
meister orchestrate --plan-file plano.json --resume
meister orchestrate --plan-file plano.json --resume 0123456789abcdef
```
Só são retomadas tarefas com descrição e dependências inalteradas, commit existente e escopo compatível. Sem
`--resume`, o plano inteiro é executado e o Meister avisa quando há um run anterior que pode ser reaproveitado.

Também aceita `--task '<texto ou JSON do plano>'` e `-c <config.yaml>`; o formato legado (texto livre) exige
`--allow-freeform` e não valida dependências nem esquema. Dentro do Herdr, `prefix+m` inicia a orquestração.

---

## Limpar branches antigas (`clean`)

A cada run o Meister já remove os worktrees e as abas órfãos e apaga as refs de arquivo com mais de 7 dias.
O `meister clean` serve para apagar as branches `meister/integration/*` e `meister/worktree/*` antigas de
runs que falharam ou foram abandonados. Sem `--apply` ele só mostra o que seria removido; protege a branch
atual, as branches abertas em worktrees e os runs informados com `--keep`:

```bash
meister clean
meister clean --repo /caminho/do/projeto --apply
meister clean --apply --archive-and-delete --close-stale-runs
```
Branches integradas, equivalentes ao base ou já arquivadas podem ser removidas com `--apply`. Commits não
integrados ficam preservados, a menos que `--archive-and-delete` crie antes uma ref em
`refs/meister/archive/`. A operação aborta com código 3 se detectar `meister orchestrate`, `run-task` ou
`worker` em execução; `--force-busy` ignora a trava. `--base BRANCH` escolhe a base, `--keep PREFIXO` protege
runs e `--json` devolve o resultado estruturado.

---

## Dashboard e linha do tempo: opções

### Dashboard web
```bash
meister dashboard                              # http://localhost:5050
meister dashboard --port 5077 --log-dir /caminho/para/logs
```
Seleciona o run mais recente por padrão e informa o arquivo JSONL lido; `--log-dir` aponta para outro
diretório de logs sem alterar `MEISTER_LOG_DIR`. Custos dos workers só aparecem quando registrados; economia
percentual não é estimada sem um baseline medido. O custo do Copilot vem dos **créditos** informados pelo
CLI (créditos × `credit_usd`, 1 crédito = US$ 0,01), não da estimativa por tokens. Na visão de todos os runs,
a tabela tem a coluna **Run** (uma cor por run) e os runs mais novos vêm primeiro.

### Dashboard em TUI
```bash
meister dashboard --tui
```
No Herdr, o atalho de popup abre essa tela sobre o layout; `q` fecha, `o` abre o dashboard web e `t` abre a
linha do tempo.

### Linha do tempo (Gantt)
```bash
meister timeline                    # ao vivo: o run mais novo, atualizando sozinho
meister timeline --once             # imprime um quadro e sai
meister timeline --run-id 3f9a1c    # um run específico (prefixo de 6+ caracteres)
meister timeline --all              # todos os runs
meister timeline --no-color         # sem cores
```
Uma barra por fase (worker, gate, integração) de cada tarefa, a via e o modelo reais, a espera do Jev numa
linha própria e o custo. Somente leitura: não altera o log. Teclas: `[` e `]` run mais antigo/mais novo, `l`
volta ao ao vivo, `a` um run/todos, `p` pausa, `+` e `-` zoom, setas rolam e andam no tempo, `?` ajuda, `q`
sai. Mostra também as tarefas rodadas por `meister worker`. Um run sem fim e sem eventos além de
`max_runtime_seconds` + 5 min aparece como `⚠ SEM SINAL`.

---

## Configuração avançada

Os padrões estão em `meister/default_config.yaml`; o `meister.config.yaml` do projeto os sobrescreve (listas
como `tier_order` **substituem** a lista inteira). Escolher e ordenar as vias está no README; aqui o resto.

**Roteador**
- `router.mode`: `jev` (padrão) ou `first` (sempre a primeira via, sem rede, operação offline e determinística).
- `router.timeout_seconds` (10) limita cada chamada ao Jev; `router.max_attempts` (2) define as tentativas;
  `router.unavailable_cooldown_seconds` (300) define por quanto tempo novas chamadas são evitadas após uma falha.
- `router.context_max_chars` (4000, mínimo 500) limita o contexto enviado ao Jev: o cabeçalho da tarefa e seus
  arquivos/dependências é preservado e o corpo é truncado quando necessário. Restrições globais do projeto são
  resumidas, não enviadas ao classificador.
- `workers.tier_order[].eligible_classes`: classes (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) que a via aceita. Se o
  Jev recomendar uma via inelegível, o Meister usa a via elegível mais próxima (a anterior na lista, senão a
  seguinte, senão mantém a escolha). Por padrão o Sonnet só recebe `ESCALATE`.
- `workers.tier_order[].credit_usd`: preço em dólar de 1 crédito da via (Copilot: `0.01`), usado no custo.
- `workers.tier_order[].max_parallel`: quantas subtarefas começam por via; o fallback posterior não transfere a reserva.

**Retentativas e tempo**
- `retry.pane_lost_attempts` (1; `0` desativa): retentativas extras na mesma via quando um pane desaparece ou
  encerra sem resultado. `retry.pane_lost_backoff_seconds` (5) é a espera inicial; dobra a cada retry, até 60 s.
  Esses retries preservam o worktree e não consomem `workers.tier_order[].max_retries` nem escalam de via.
- `workers.idle_timeout_seconds` (600) encerra workers sem sinais de atividade; `workers.max_runtime_seconds`
  (3600) é o teto total. Ambos aceitam `0` para desligar e podem ser sobrescritos por via em `tier_order`. A
  atividade é detectada por mudanças no estado Git do worktree ou no conteúdo do pane; o teto não é estendido por
  atividade. Em timeout, alterações e commits existentes seguem o fluxo normal de escopo, gate e integração; um
  worker sem trabalho é retentado na mesma via e depois escala pela cadeia configurada.

**Gate e escopo**
- `gate.commands`: verificações próprias (Go, Node/pnpm...); veja `meister.config.example.yaml`. Sem testes
  detectados o gate reprova, a menos que `gate.allow_unverified: true`.
- `scope.tolerated_files`: artefatos gerados tolerados fora dos `target_files` (a lista substitui a padrão).
- `gate.cache` (padrão `true`): o gate guarda só as passagens, por conteúdo do código e dos comandos, e não repete
  uma verificação idêntica; `gate.cache: false` desliga.
- `environment.install_dependencies`: instala as dependências Node nos worktrees só quando há o que instalar.

---

## Desenvolvimento e testes

A suíte inteira roda **em paralelo** (`pytest-xdist`, `-n auto`) sozinha quando o xdist está instalado
(`pip install -e ".[dev]"`): cerca de 50 s em vez de 3,5 min. Escolher arquivos ou testes mantém a execução em
série; use `-n0` ou `MEISTER_TEST_PARALLEL=0` para forçar série (depuração) ou `MEISTER_TEST_PARALLEL=4` para
fixar o número de processos. Sem o xdist nada quebra: roda em série. `pytest -v --durations=15` lista os 15
testes mais lentos. A suíte interrompe cada teste após 180 segundos por padrão; ajuste com
`MEISTER_TEST_TIMEOUT` ou use `0` para desativar o watchdog (em plataformas com `SIGALRM`). Uma proteção
autouse detecta e remove refs de branches e worktrees criados acidentalmente no checkout real; testes que
executam o orquestrador devem usar um repositório Git temporário. O job de testes do CI tem limite total de
20 minutos.

Versionamento: SemVer, `0.x` até o contrato estabilizar. A versão fica em `meister/__init__.py` (e nos literais
de `package.json` e `herdr-plugin.toml`, conferidos por teste); veja o [`CHANGELOG.md`](../CHANGELOG.md).

---

## Estrutura do projeto

```
meisterrouter/
├── bin/
│   ├── meister                    # executável CLI Python
│   ├── install.sh                 # bootstrap e instalação rápida
│   └── cli.js                     # wrapper Node.js para npm / npx
├── meister/
│   ├── cli.py                     # interface de linha de comando
│   ├── config.py, default_config.yaml  # configuração e catálogo de modelos
│   ├── plan.py, plan_adapters/    # plano canônico e importadores
│   ├── plan_analysis.py           # análise de paralelismo do plano
│   ├── state.py                   # SQLite, máquina de estados, disjuntores
│   ├── jev.py, jev_context.py     # cliente do Jev (OpenRouter) e contexto enviado
│   ├── worker.py                  # execução dos workers (harnesses e tabs do Herdr)
│   ├── worktree.py                # worktrees, integração e fast-forward
│   ├── gate.py, env_setup.py      # gate determinístico e preparo de dependências
│   ├── logger.py, usage.py        # telemetria JSONL, tokens, créditos e custo
│   ├── report.py, progress.py     # relatório e progresso no terminal
│   ├── timeline*.py, log_tail.py  # linha do tempo (Gantt) em TUI, somente leitura
│   ├── clean.py, hooks.py         # limpeza de branches; hooks Git/Claude e guard
│   ├── faults.py                  # injeção de falhas para os testes de queda
│   ├── herdr/                     # bridge.py (orquestrador), dag.py, workers.py, client.py, events.py, tui.py
│   ├── dashboard/                 # servidor Flask e interface web
│   └── templates/                 # CLAUDE.md, CODEX.md, hooks
├── tests/                         # suíte de testes (pytest)
├── docs/                          # manual, diagramas, modelos e custos, esta página
├── herdr-plugin.toml              # manifesto do plugin para o Herdr
├── package.json, pyproject.toml, setup.py   # pacotes npm e Python
├── CHANGELOG.md                   # histórico de versões
└── CLAUDE.md, CODEX.md, AGENTS.md # regras para os agentes
```

---

## Adaptador GitHub Copilot CLI

> ℹ️ **STATUS: VERIFICADO (GitHub Copilot CLI 1.0.88)**
> O adaptador Copilot CLI (`copilot`) utiliza as flags oficiais `-p`, `--allow-all` e `--no-ask-user` para execução não-interativa autônoma.
> Sua presença e posição na cadeia são definidas por `meister/default_config.yaml` e podem ser alteradas em `meister.config.yaml`; variáveis de ambiente de seleção de modelos foram removidas.
