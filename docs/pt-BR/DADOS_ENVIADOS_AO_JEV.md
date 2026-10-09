# Dados enviados ao Jev

O Jev (`typesafe/jev-1.13` via OpenRouter) toma duas decisões: **classify** (por qual via começar)
e **control** (concluir, tentar de novo ou trocar de via). Só essas duas chamadas saem da máquina.
Os workers nunca enviam dados ao OpenRouter; executam pelos harnesses locais.

Esta página lista exatamente o que cada chamada envia. O contrato é travado por
`tests/test_jev_payload_contract.py`, que falha se um campo for acrescentado ou alterado; por isso
esta página precisa ser atualizada junto com a mudança.

Voltar a [comandos avançados](INSTALACAO_E_COMANDOS_AVANCADOS.md#a-chave-do-openrouter).
Versão em inglês: [jev-data-sent.md](../jev-data-sent.md).

## Formato da requisição

As duas chamadas fazem um `POST` com corpo JSON `{model, temperature, state, questions}`.

- `model`: `--model` ou `master.model` (padrão `typesafe/jev-1.13`).
- `temperature`: sempre `0.0`.
- `state`: os fatos sobre os quais o Jev decide.
- `questions`: as escolhas que o Jev deve responder, cada uma com seus critérios permitidos.

A chave de API vai só no cabeçalho `Authorization: Bearer <chave>`. Nunca aparece no corpo e nunca
é impressa ou registrada. Os cabeçalhos também incluem `HTTP-Referer` e `X-Title`.

## `classify`

Enviado por `meister classify` e pelo `orchestrate` para cada subtarefa.

| Campo | Origem | Exemplo |
|---|---|---|
| `state.task_description` | A string de contexto. No `orchestrate`, é montada por `build_jev_context` a partir da tarefa do plano. A CLI envia o `--context` como foi escrito. | `Tarefa: Adicionar filtro\nArquivos (1): src/Filter.tsx\n...` |
| `questions.complexity` | Fixo: critérios `small`, `medium`, `high`, `escalate`. | `{"criteria": {...}}` |
| `questions.recommended_implementer` | Montado a partir de `workers.tier_order`. Cada via recebe uma chave opaca (`lane_a`, `lane_b`, ...) e uma descrição: `<modelo ou harness> via <harness> ($custo/M): <best_for>`. | `"lane_a": "gpt-6-luna via codex ($0.20/M): ..."` |

Os nomes das vias (`tier_1`, ...) nunca são enviados. Eles são trocados por chaves opacas
(`_opaque_lane_keys` em `meister/jev.py`), porque nomes como `tier_N` faziam o Jev subir para vias
mais altas. A resposta do Jev é traduzida de volta para o nome real da via. Uma chave desconhecida
cai na primeira via.

### O que contém `task_description`

`task_description` é o **texto da tarefa do plano**: o título, a lista `Files`, a lista
`Depends on` e o corpo, truncado em `router.context_max_chars` (padrão 4000). As restrições globais
do projeto são omitidas e só o tamanho delas é informado. Tudo o que estiver no corpo da tarefa é
enviado, então não coloque segredos em tarefas de plano.

## `control`

Enviado pelo `orchestrate` após o gate, antes do fast-forward da `main`, e por `meister control`.

| Campo | Origem | Exemplo |
|---|---|---|
| `state.diff_summary` | `--diff-summary`, ou `git diff --stat` no worktree, ou `git diff --stat base..HEAD` na integração. | `src/Filter.tsx \| 12 +++-` |
| `state.test_result` | `pass`, `fail` ou `unknown`. | `pass` |
| `state.attempts_so_far` | Número da tentativa (padrão 1). | `2` |
| `state.security_sensitive` | Flag (`false` por padrão). | `false` |
| `questions.next_action` | Fixo: `COMPLETE`, `RETRY`, `switch_implementer`. | |
| `questions.should_escalate` | Fixo: sim/não. | |
| `questions.switch_implementer` | Fixo: sim/não. | |

### O que contém `diff_summary`

`diff_summary` **inclui caminhos de arquivos**: o `git diff --stat` lista cada arquivo alterado.
Não inclui o conteúdo dos arquivos, mas os caminhos podem revelar a estrutura do projeto.

## Como evitar o envio

- **`router: {mode: first}`** em `meister.config.yaml` pula o Jev no roteamento do `orchestrate`.
  Usa-se a primeira via e o fallback de `tier_order` continua valendo. É a forma de manter o
  `classify` fora da rede.
- **Sem `OPENROUTER_API_KEY`**: a busca da chave falha antes de qualquer requisição. A chamada cai
  em regras determinísticas locais, sem acesso à rede.
- **O `control` continua sendo enviado no fim do `orchestrate`** mesmo com `router.mode: first`,
  porque o modo só muda o roteamento. Sem chave, o `control` cai nas regras locais e não envia nada.

## Logs locais

O evento `classify` gravado no log JSONL local contém os 300 primeiros caracteres do contexto, o
tamanho dele e o SHA-256. O log fica em `MEISTER_LOG_DIR` e o Meister não o envia para lugar nenhum.
