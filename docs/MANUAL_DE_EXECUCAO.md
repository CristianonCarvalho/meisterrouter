# Manual de execução do MeisterRouter

Para quem usa o MeisterRouter num projeto. Atualizado em 2026-10-02 (main até o PR #28). Itens marcados **AÇÃO** exigem algo seu; os marcados **(temporário)** deixam de ser necessários quando a correção indicada for mesclada, e então a linha é removida. O diagrama do fluxo está em [FLUXO_MEISTERROUTER.md](FLUXO_MEISTERROUTER.md).

## 1. Uma vez por máquina
- **Instalação:** o `meister` já está instalado (modo editável a partir do repositório). Em outra máquina: `git clone`, `python3 -m venv .venv && .venv/bin/pip install -e .`, `herdr plugin link <pasta do repo>`.
- **AÇÃO: chave do OpenRouter** (para o Jev): `~/.meister/.env` com `OPENROUTER_API_KEY=...`. Hoje o Jev a encontra por acaso no `.env` do repositório do MeisterRouter; em outra máquina, sem esse arquivo, o Jev fica indisponível e o roteamento cai na primeira via (com aviso).
- **AÇÃO: CLIs dos workers** instalados e logados: `copilot` (via principal), `agy`, `claude`; `codex` só se for usá-lo.
- **AÇÃO: manter o repositório do MeisterRouter na branch `main`.** O plugin do Herdr roda o código que estiver na pasta; numa branch de trabalho você roda código não mesclado. Atualizar: `git checkout main && git pull`.
- Plugin do Herdr: `herdr plugin list` deve mostrar `dev.meisterrouter.orchestrator` apontando para a pasta do repo. O daemon sobe com o Herdr; reinicie com `meister daemon --stop` e `meister daemon --start` se atualizar o código.

## 2. Por projeto
- **AÇÃO: o projeto precisa ser um repositório git com pelo menos um commit** (os workers usam worktrees e a integração termina na `main`).
- **Arquivos iniciais:** `meister init` (na raiz do projeto) cria `AGENTS.md`, `CLAUDE.md` e `CODEX.md` sem modelos fixos e **nunca sobrescreve** o que já existe (`--force` para sobrescrever). Hooks são opcionais (`--hooks`).
- **Configuração (opcional):** `meister.config.yaml` na raiz sobrescreve `meister/default_config.yaml`. Conferir com `meister config show` e `meister config validate`. A lista `workers.tier_order` do seu arquivo **substitui** a padrão; as demais seções mesclam. Exemplos comentados em `meister.config.example.yaml`.
- **O Codex vem desligado** (`codex_luna`, créditos limitados). Para usá-lo no roteamento automático, ligue-o no seu `meister.config.yaml` (`enabled: true`); para um uso pontual, `meister worker --model codex_luna --task "..."`.
- **Projeto Node/TypeScript:** nada a fazer. O MeisterRouter instala as dependências em cada worktree (`pnpm install --frozen-lockfile`, `npm ci` ou `yarn install --frozen-lockfile`, conforme o lockfile) e o portão usa os binários locais (`node_modules/.bin`), sem `npx`. Para desligar: `environment.install_dependencies: false`.
- **Projeto Python:** crie uma vez `.venv` na raiz (`python3 -m venv .venv`); o portão a detecta também nos worktrees. Exemplo:
  ```yaml
  gate:
    commands:
      - {name: ruff,   run: "ruff check app tests", timeout_seconds: 120, required: true}
      - {name: mypy,   run: "mypy app",             timeout_seconds: 180, required: true}
      - {name: pytest, run: "{python} -m pytest -q", ok_exit_codes: [0, 5], timeout_seconds: 300, required: true}
  ```
  Faça commits do scaffold (`app/__init__.py` e um teste mínimo) antes das tarefas; sem eles `mypy app` reprova por diretório inexistente. Tarefas que mudam `requirements*.txt` não reinstalam o venv.
- **Projeto que não é Python/Node/Rust** (Go, Java...): declare o portão no `meister.config.yaml`, por exemplo `gate.commands: [{name: unit, run: "go test ./...", timeout_seconds: 300, required: true}]` (sem shell). Sem isso o portão não detecta teste e reprova; `gate.allow_unverified: true` aceita sem verificar (não recomendado).
- **Arquivos gerados** (lockfiles, `*.tsbuildinfo`, `.vitest/**`) já são tolerados no escopo; acrescente outros em `scope.tolerated_files` (lista **substitui** a padrão: copie a padrão e some os seus).
- Contexto para os workers: se quiser, escreva no `AGENTS.md` do **projeto** a stack, o comando de testes e as convenções.

## 3. Rodar um plano
1. Dentro do Herdr (os workers abrem em tabs `worker:*`):
   `meister plan import --format superpowers plano.md -o plano.json` e `meister plan validate plano.json`
2. Antes de executar, use `meister plan analyze plano.json` para ver lotes, dependências e conflitos de arquivos. A estimativa por arquivo ignora dependências semânticas; `--max-workers N` altera o teto usado nos passos estimados.
3. `meister orchestrate --plan-file plano.json`
4. **AÇÃO: não passe a saída por pipe** (`| head`, `| tee` sem `pipefail`): o código de saída que você vê passa a ser o do pipe, não o do orquestrador. Falha = código diferente de zero.
5. Planos do superpowers encadeiam as tarefas por padrão (`--deps sequential`). Para paralelismo real, use `**Depends on:**` explícito ou `--deps files`. Tarefas sem `target_files` rodam isoladas, uma por vez.
6. `**Files:**` aceita glob (`src/text/*.ts`, `drizzle/*`). Declare todos os arquivos que a tarefa pode criar ou alterar; o que sair do escopo reprova a tarefa.
7. No fim, o resultado é integrado na `main` do projeto por fast-forward; nada entra sem passar pelos gates.

## 4. Quando algo falha: o que você precisa fazer
`meister orchestrate` mostra progresso por tarefa e o resumo final em stderr; a frase de sucesso/falha
continua em stdout. Use `--quiet` (ou `-q`) para omitir progresso e resumo.

| Situação | O que fazer |
|---|---|
| Run falhou ou foi interrompido (notebook fechado, tab do worker fechada, queda) | **Rodar o MESMO comando de novo**: as tarefas concluídas são puladas. A tab de um worker fechada é detectada em ~5 s. |
| Você **editou o plano** (ex.: acrescentou um arquivo em `Files:`) depois de tarefas concluídas | Rode `meister orchestrate --plan-file plano.json --resume` (ou `--resume <run_id>`). Tarefas concluídas, integradas e inalteradas (descrição e dependências iguais, commit existente, dentro do escopo novo) são reaproveitadas; as demais rodam de novo. Sem `--resume` o run é novo e refaz tudo (o comando avisa quando há run aproveitável). |
| `Violação de escopo` | A mensagem indica o arquivo. Declare-o em `**Files:**` (aceita glob) ou em `scope.tolerated_files`, edite o plano e rode com `--resume`. |
| `ERRO DE INFRAESTRUTURA no portão` (instalação, binário local ausente, timeout) | O código do worker **não** foi reprovado e o trabalho dele foi preservado em commit. Corrija o ambiente (ex.: rodar `pnpm install` no projeto, conferir o `pnpm`/`node` no PATH) e rode o mesmo comando. |
| Você **fechou a janela** do Herdr (ou desanexou com `ctrl+b q`) | Nada. O Herdr mantém um servidor em segundo plano: os panes, os workers e o `meister orchestrate` continuam rodando. Reabra com `herdr`. |
| Você **desligou o computador** ou parou o servidor (`herdr server stop`) | O run morre junto (o `orchestrate` é um processo comum e o Herdr não preserva processos, só o layout). Rode o mesmo comando (ou `--resume` se editou o plano). Suspender o notebook (tampa fechada) em geral não derruba os processos, mas uma queda de rede pode afetar os workers. |
| A **tab/pane de um worker sumiu** (fechada à mão ou o agente caiu) | Automático: o MeisterRouter tenta de novo **uma vez**, na mesma via e no mesmo worktree (`retry.pane_lost_attempts`, `retry.pane_lost_backoff_seconds`; `0` desliga). Se a retentativa também falhar, o run falha com "retentativas esgotadas"; rode o mesmo comando. |
| Cota do worker esgotada | Automático: abre o disjuntor da via (60 s) e usa a próxima via. Se todas esgotarem, o run falha; reexecute depois. |
| Jev indisponível (OpenRouter fora do ar ou sem chave) | Automático: usa a primeira via por 5 min sem esperar de novo. Sem chave, `meister config validate` avisa. |

## 5. Onde olhar
- **Eventos:** JSONL em `~/.meister/logs` (ou `MEISTER_LOG_DIR`); `meister replay` reconstrói a linha do tempo. `meister dashboard` abre o painel agrupado por tarefa, com seleção de run, análise filtrável/paginada de eventos e indicação explícita do arquivo lido. Use `meister dashboard --log-dir CAMINHO` para apontar outro diretório. O painel não estima economia sem baseline medido, e custos de workers não registrados aparecem como “não medido”. Eventos úteis: `subtask_reused`/`subtask_not_reused` (retomada), `gate_infrastructure_error`, `worktree_setup_ok`/`worktree_setup_failed`.
- **Relatório de runs:** `meister report --run-id ID` mostra tarefas, custo, fases e overhead; repita `--run-id` para comparar runs. Use `--group A=ID1,ID2` para mediana/mínimo/máximo de um grupo, `--format json|markdown` para exportar ou `--log-dir CAMINHO` para ler outro `orchestration_log.jsonl`. Prefixos de run precisam ter ao menos 6 caracteres; métricas ausentes em logs antigos aparecem como “não medido”. O custo do Copilot vem de créditos × `credit_usd`.
- **Linha do tempo (Gantt):** `meister timeline` abre, no terminal (ex.: numa tab do Herdr), um Gantt colorido das tarefas do run mais novo: uma barra por tarefa no eixo do tempo, cor por fase (worker, gate, integração, espera), a faixa Jev para as chamadas de decisão, a ponta pulsando nas tarefas em andamento e o paralelismo visível (tarefas sobrepostas, pico e média no topo). Um run sem fim e sem eventos além de `max_runtime_seconds` + 5 min aparece como `⚠ SEM SINAL`. Teclas: `a` alterna entre um run e todos os runs empilhados, `[`/`]` trocam de run (replay), `l` volta ao ao vivo, `p` pausa, `+`/`-` zoom, `←`/`→` tempo, `↑`/`↓` linhas, `?` ajuda, `q` sai; `meister timeline --all` abre direto em todos os runs. `--once` imprime um quadro e sai (serve para pipe); `--no-color` ou `NO_COLOR=1` desligam as cores; precisa de 80 colunas. Dentro de `meister dashboard --tui`, a tecla `t` abre a mesma tela. Só lê o log; a fase corrente de uma tarefa em andamento é inferida (o log registra a fase ao terminar) e não há percentual por tarefa.
- **Estado:** `.meister/meister.db` no projeto (runs, subtarefas, disjuntores). Um run retomado guarda `resumed_from`; o de origem recebe `superseded_by`.
- **Trabalho rejeitado nunca é perdido:** commits e mudanças ainda não commitadas ficam em `refs/meister/archive/*` (estas últimas usam refs com sufixo `-uncommitted`). Recupere um diff com `git diff <base>..refs/meister/archive/<ref>` ou um arquivo com `git checkout <ref> -- <arquivo>`; commits reaproveitados numa retomada ficam fixados em `refs/meister/resume/*`.
### Sobras

Após uma queda ou retomada, use `meister clean` para simular a remoção de branches antigas `meister/integration/*` e `meister/worktree/*`; `meister clean --apply` aplica as remoções seguras. Commits não integrados são mantidos por padrão; `--archive-and-delete` cria uma ref em `refs/meister/archive/` antes de apagá-los. Branches abertas em worktrees, a branch atual, refs de arquivo e runs passados em `--keep` são protegidos. A trava impede aplicar enquanto `orchestrate`, `run-task` ou `worker` estiver executando (`--force-busy` ignora). `--close-stale-runs` cancela runs sem processo, panes ou branch de integração existentes. Não use `git branch -D` manualmente para limpar branches do MeisterRouter.

## 6. Lembrete de manutenção deste manual
A cada PR que remova uma linha marcada **(temporário)**, remover a linha daqui. Atualize a data e o PR no topo.
