# Tela TUI de linha do tempo (Gantt) — Design

Data: 2026-10-05. Origem: pedido do usuário, aprovado em conversa (seções 1 a 4 e a tecla `t`).

## Objetivo

Uma tela de terminal, colorida, que mostra a execução de um projeto pelo MeisterRouter como um
Gantt: uma linha por tarefa, barras no eixo do tempo, estado atual, progresso geral e tarefas
paralelas sobrepostas. Funciona **ao vivo** (run em andamento) e em **replay** (run já concluído).

## Decisões do usuário

| Tema | Decisão |
|---|---|
| Uso | Ao vivo e replay (a comparação de dois runs fica para o E3) |
| Progresso | Só o que é real: nada de percentual por tarefa em andamento. Progresso geral = concluídas/total |
| Animação | A ponta da barra de cada tarefa em andamento pulsa |
| Paralelismo | Tarefas paralelas aparecem sobrepostas; o topo mostra quantas rodam agora (pico e média) |
| Implementação | ANSI puro, sem dependência nova (abordagem A) |
| Cores | Paleta rica; via com cor própria só no rótulo; degradê contínuo na barra geral |
| Atalho | Tecla `t` no overlay atual (`meister dashboard --tui`) abre a tela; `q` volta |

## Fora do escopo

Mouse, versão web, comparação de dois runs lado a lado, qualquer ação de controle (cancelar
tarefa), dependência nova, escrever no log ou no estado do Meister.

## Arquitetura

Quatro unidades pequenas, cada uma com um propósito e testável sozinha:

| Módulo | Faz | Depende de |
|---|---|---|
| `meister/log_tail.py` | Leitura incremental do log (só leitura) | `meister.logger` (caminho do log) |
| `meister/timeline.py` | Modelo puro: eventos → `Timeline` | nada (recebe lista de eventos) |
| `meister/timeline_view.py` | Desenho puro: `Timeline` + opções → string ANSI | `timeline.py` |
| `meister/cli.py` (comando `timeline`) e `meister/herdr/tui.py` (tecla `t`) | Laço de teclas e atualização | os três acima |

### `meister/log_tail.py`

`LogTail(path)` com `poll() -> list[dict]`: devolve só os eventos novos desde a última chamada.
- Abre em modo leitura, nunca escreve, não toma lock; mantém o deslocamento em bytes.
- Linha parcial no fim (sem `\n`) é guardada e só vira evento quando completa.
- Linha que não é JSON é ignorada. Arquivo inexistente: `poll()` devolve `[]` (a tela mostra
  "aguardando o primeiro run"). Arquivo menor que o deslocamento (truncado ou trocado): relê do início
  e sinaliza `reset=True`, para o chamador descartar os eventos acumulados.

### `meister/timeline.py`

`build_timeline(events, run_id, now) -> Timeline`, função pura, determinística.

`Timeline`: `run_id`, `title`, `status` (`running`, `completed`, `failed`), `started_at`, `ended_at`
(`None` em andamento), `rows`, `summary`.

`TaskRow`: `task_id`, `title`, `tier`, `attempts`, `status` (`waiting`, `running`, `completed`,
`failed`), `depends_on`, `segments`, `duration_s`, `cost_usd`, `failure`.

`Segment`: `phase` (`worker`, `gate`, `lock_wait`, `integrate`, `wait`), `start`, `end` (`None` =
em andamento), `inferred` (verdadeiro quando a fase atual foi deduzida).

`summary`: `total`, `completed`, `failed`, `running`, `peak_parallel`, `avg_parallel`, `cost_usd`.

**Regras de derivação** (confirmadas no log real do UFG_TODO):
- Total, lotes, ordem e títulos vêm de `plan_parsed` (`task_ids`, `task_titles`); dependências do
  `depends_on` do plano em `orchestration_start`. Sem `plan_parsed` (logs antigos), as tarefas são as
  que têm ciclo de vida de worker, na ordem natural dos ids.
- Início da tarefa = `ts` de `worker_spawn` (o primeiro da tentativa). Antes disso, a tarefa está
  `waiting`; se tem `depends_on` incompletas, o segmento `wait` vai até o spawn.
- `worker_phase` é gravado ao **terminar** a fase: `fim = ts`, `início = ts − duration_ms`.
- Fase `worker`: de `worker_spawn` até o `ts` do evento `worker_phase` com `phase=worker`.
- Janela final: do fim do `worker` até o `ts` de `subtask_completed` (ou do evento de falha). Dentro
  dela, cada `worker_phase` `gate` e `lock_wait` vira um segmento `[ts − duração, ts]`; o restante da
  janela é `integrate` (merge). O `integrate` gravado inclui os gates e termina junto com a tarefa,
  por isso não é desenhado direto.
- **Fase em andamento (inferida):** como o log só registra a fase ao terminar, o segmento aberto de
  uma tarefa em andamento vai até `now` e sua fase é a seguinte à última registrada
  (nada registrado → `worker`; `worker` terminou → `gate`; `gate` terminou → `integrate`), marcado
  `inferred=True`.
- Retentativa (`worker_retry`/novo `worker_spawn` com `attempt` maior): novos segmentos na mesma
  linha; `attempts` sobe e o rótulo mostra `↻n`. O intervalo entre tentativas é `wait`.
- Falha: `subtask_rejected` ou eventos de falha já usados pelo dashboard (`FAILURE_EVENTS`) com motivo
  em `failure`; a barra termina com `✖`.
- Run sem `orchestration_end` e sem evento recente continua `running` (a tela não adivinha abandono).
- Paralelismo: pico e média por varredura dos intervalos `[spawn, fim]`, ignorando `wait`.
- Custo: soma de `cost_usd` de `subtask_completed`, com o mesmo cálculo do relatório do Meister
  (`compute_run_report`), sem recalcular fórmulas aqui.

### `meister/timeline_view.py`

`render_frame(timeline, *, width, height, now, tick, view, color) -> str`.
- `view`: janela de tempo (`start`, `end`), rolagem de linhas, run selecionado, `paused`.
- `tick`: contador do pulso, vem do chamador (o desenho é determinístico, sem relógio interno).
- `color`: `truecolor`, `256` ou `none` (monocromático com glifos distintos).
- Estrutura: cabeçalho (projeto, run, selo AO VIVO / REPLAY / CONCLUÍDO / FALHOU, intervalo), barra
  geral, régua de tempo, linhas das tarefas, rodapé de teclas e legenda de fases.
- Largura mínima 80 colunas; abaixo disso devolve um aviso curto. Rótulo da tarefa com truncamento
  por `…`. Linhas além da altura rolam; cabeçalho e rodapé são fixos.
- Eixo: em ao vivo, escala automática do início do run até `now`; em replay, o run inteiro cabe na
  largura; zoom e deslocamento por `view`.

### Paleta

| Elemento | Cor |
|---|---|
| `worker` | azul vivo; o trecho pulsando alterna com azul claro |
| `gate` | amarelo |
| `integrate` | magenta |
| `wait` / `lock_wait` | cinza escuro, glifo pontilhado |
| Concluída | verde, `✔` |
| Falha | vermelho com fundo escuro, `✖` |
| Retentativa | laranja, `↻n` |
| Barra geral | degradê contínuo vermelho → amarelo → verde conforme a % |
| Via (só no rótulo) | copilot ciano, agy laranja, codex verde-água, claude lilás, jev branco; via desconhecida cinza |
| Fundo das linhas | tom alternado; em replay, o matiz do run é o mesmo do dashboard web (`210 + 67·índice`) |
| Tarefa mais lenta do run | duração em negrito e vermelho |
| Selos | `▶ AO VIVO` verde pulsando; `⏸ REPLAY` azul; `✔ CONCLUÍDO` verde; `✖ FALHOU` vermelho |

`NO_COLOR` definido, `TERM=dumb` ou `--no-color` → `color=none`. Truecolor se `COLORTERM` é
`truecolor`/`24bit`, senão 256 cores. Em `none`, as fases usam glifos distintos (`█ ▓ ▒ ░`).

## Uso

`meister timeline [--run-id ID] [--log-dir DIR] [--once] [--no-color]`
- Sem argumentos: log do projeto da pasta atual (mesma resolução do dashboard), run mais novo.
- `--once`: imprime um quadro e sai, sem terminal interativo (pipe, teste, screenshot). Largura: a do
  terminal, ou 120 se não for um terminal.
- Tecla `t` no overlay `meister dashboard --tui`: abre a mesma tela no painel; `q` volta ao overlay.

### Teclas

| Tecla | Ação |
|---|---|
| `[` / `]` | run anterior / próximo |
| `l` | voltar ao ao vivo (segue o run mais novo) |
| `p` | pausar a tela (o log continua sendo lido) |
| `+` / `-` | zoom no eixo de tempo |
| `←` / `→` | andar no tempo (com zoom) |
| `↑` / `↓` | rolar as linhas |
| `?` | ajuda |
| `q` | sair |

### Laço interativo

- Tela alternada (`\033[?1049h`), cursor oculto, `termios` em cbreak; restaura tudo em `finally`
  (inclui `Ctrl+C` e erro).
- Redesenha com o cursor no topo (`\033[H`) e limpa até o fim de cada linha, sem limpar a tela inteira.
- Dados: `LogTail.poll()` a cada 1 s e `build_timeline` sobre os eventos acumulados; desenho a 4
  quadros por segundo só para o pulso (`tick`). Em replay (run concluído), sem polling.
- Redimensionamento: relê o tamanho do terminal a cada quadro.
- "Ao vivo" segue o run mais novo, inclusive se um run novo começar; escolher outro run com `[`/`]`
  sai do modo ao vivo até `l`.

## Erros e casos de borda

Log inexistente ou vazio → "aguardando o primeiro run". Run inexistente em `--run-id` → mensagem e
código de saída 2. Log com linha inválida → ignorada. Eventos fora de ordem → ordenados por `ts`
(com desempate pela posição no arquivo, como o dashboard). Tarefa sem `worker_spawn` mas com
`subtask_completed` (log antigo) → barra única do início do run ao fim, rotulada sem fases.
Terminal sem cor ou estreito → modo monocromático ou aviso, nunca erro.

## Testes (sem rede)

- **Modelo:** início da fase = `ts − duração`; fase inferida (nada gravado → `worker`; após `worker` →
  `gate`; após `gate` → `integrate`); sobreposição de tarefas paralelas e `peak_parallel`;
  dependências e `wait`; retentativa e falha; run sem fim; log antigo sem `plan_parsed`; eventos fora
  de ordem; custo igual ao de `compute_run_report` para o mesmo log.
- **Desenho:** quadros em 80 e 120 colunas comparados sem ANSI (remover as sequências e conferir o
  texto); `color=none`; rolagem e zoom; pulso: `tick` par e ímpar geram quadros diferentes só na ponta;
  largura abaixo de 80.
- **Leitura incremental:** linha parcial; arquivo que encolhe (`reset`); arquivo inexistente; várias
  chamadas somando eventos.
- **CLI:** `--once` ponta a ponta contra um log sintético; `--run-id` inexistente.
- **Overlay:** a tecla `t` chama a tela e `q` volta, com o laço de teclas substituído por entrada
  simulada.
- **Mutações obrigatórias:** (a) início da fase = `ts` em vez de `ts − duração`; (b) fase inferida
  sempre `worker`; (c) `LogTail` não guarda a linha parcial. Cada uma deve falhar em pelo menos um teste.

## Verificação real

`meister timeline --once` no log do UFG_TODO (somente leitura, `stat` do log antes e depois),
conferindo duração, concluídas e custo contra o dashboard e o `meister report`. Depois, ver um run
real ao vivo numa tab do Herdr com 3 workers paralelos (gasta alguns créditos do Copilot; o usuário é
avisado antes).

## Entrega

- **PR 1:** `log_tail.py`, `timeline.py`, `timeline_view.py` e `meister timeline --once`, com testes.
- **PR 2:** modo interativo (teclas, ao vivo e replay), a tecla `t` no overlay e a seção no
  `docs/MANUAL_DE_EXECUCAO.md`.
- Implementação por worker Copilot em worktree, sem push; verificação independente (testes, mutações
  em cópia, chamada real) antes de qualquer commit.

## Limitações conhecidas

- O percentual de uma tarefa em andamento não existe; só estado e tempo decorrido.
- A fase corrente de uma tarefa em andamento é inferida (o log registra a fase ao terminar); o marcador
  `▶` indica isso.
- Logs anteriores à E1 não têm `worker_phase`: nesses a barra não distingue fases.
