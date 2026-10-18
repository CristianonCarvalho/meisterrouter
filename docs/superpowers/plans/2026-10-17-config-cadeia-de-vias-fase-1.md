# Tela /config: cadeia de vias, criação e edição completa (fase 1) Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** spec `docs/superpowers/specs/2026-10-17-config-cadeia-de-vias-design.md` (aprovado em 2026-10-17) e o protótipo clicável `docs/superpowers/specs/2026-10-17-config-cadeia-de-vias-prototipo.html`, que traz o desenho e funções de partida (`levelOf`, `swap`, `renderPath`, `validate`, `renderDiff`). Esta é a **fase 1** do spec: slots com troca de conteúdo, cadeia por níveis, simulador, criação e remoção de vias e edição de todos os campos. A árvore de modelos (fase 2) e o estado do circuit breaker (fase 3) ficam para planos seguintes; a documentação é feita pelo orquestrador depois.

**Goal:** A `/config` mostra a cadeia de vias em níveis (com escalonamento e salto de custo), simula o caminho de uma tarefa, troca o conteúdo entre slots, cria, remove e renomeia vias e edita todos os campos de uma via, gravando o `meister.config.yaml` com os comentários preservados.

**Architecture:** Três partes. O servidor (`meister/config_editor.py` e a API do dashboard) passa a aceitar todos os campos e vias novas, removidas e renomeadas. As funções puras de JavaScript ficam no bloco `helpers:begin`/`helpers:end` de `config.html`, testadas por `node`. A página troca a tabela pelo desenho do protótipo, em três etapas sobre o mesmo arquivo.

**Tech Stack:** Python ≥ 3.10, Flask, `ruamel.yaml`, `pytest`, `ruff`, `mypy`, HTML/JS puro com `node` nos testes.

## Global Constraints

- Contrato do pedido `PUT /api/config/lanes` (corpo `{stamp, lanes}`): `lanes` é a lista **inteira** desejada, na ordem desejada. Cada via traz os campos editáveis e, se já existia, `original_name` (o nome que tinha no arquivo; ausente equivale ao próprio `name`). Via sem `original_name` e com nome desconhecido é **nova**; via existente que não aparece na lista é **removida**; `original_name` diferente de `name` é **renomeação**.
- Campos editáveis de uma via: `name`, `enabled`, `harness`, `model`, `effort`, `cost_per_m_tokens`, `credit_usd`, `max_retries`, `max_parallel`, `idle_timeout_seconds`, `max_runtime_seconds`, `eligible_classes`, `best_for`. Conteúdo que viaja na troca entre slots: `harness`, `model`, `effort`, `cost_per_m_tokens`, `credit_usd`; o resto é política do slot e fica na posição.
- O nível de uma via é o número no fim do nome, com uma letra opcional (`tier_1`, `tier_1b` e `tier_1c` são o nível 1). As regras de nome e de ordem de nível valem só para vias **novas ou renomeadas**; vias existentes com nome não padronizado e não renomeadas continuam gravando normalmente.
- Texto novo da página só nos catálogos `en` e `pt-BR` (`cli.config.web.*`), com a chave também na lista de `_config_messages` em `meister/dashboard/server.py`; nenhum texto fixo no HTML ou no JavaScript. Mensagens novas do editor ficam em `_MESSAGES` de `meister/config_editor.py`, no mesmo estilo das existentes.
- Não mudar o motor de fallback, o circuit breaker, a leitura por Tailscale (continua só leitura) nem os cabeçalhos de proteção da escrita.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Editor aceita todos os campos de uma via

**Files:**
- Modify: `meister/config_editor.py`
- Modify: `tests/test_config_editor.py`
- Modify: `tests/test_dashboard_config_api.py`
- Create: `tests/test_config_editor_fields.py`

Em `meister/config_editor.py`, amplie `EDITABLE_FIELDS` com `cost_per_m_tokens`, `credit_usd`, `max_retries`, `idle_timeout_seconds`, `max_runtime_seconds`, `eligible_classes` e `best_for`, e o dataclass `LaneEdit` com os mesmos campos. Em `_lane_field_issues`, valide: `cost_per_m_tokens` número ≥ 0 (não booleano); `credit_usd` nulo ou número ≥ 0; `max_retries` inteiro ≥ 0; `idle_timeout_seconds` e `max_runtime_seconds` nulos ou números > 0; `eligible_classes` lista de valores de `VALID_WORKER_CLASSES` (de `meister.config`); `best_for` lista de textos não vazios. Os erros usam o caminho `lanes[i].campo` e mensagens novas em `_MESSAGES` (en e pt-BR). Em `_update_lane_node`, grave os campos novos no nó existente: valor nulo ou lista vazia remove a chave; comentários e campos que a tela não conhece (`_update_lane_node` só toca nos editáveis) seguem como estão. Esta tarefa não muda criação, remoção nem nomes de vias.

Atualize os testes que afirmavam que `cost_per_m_tokens`, `best_for` e `credit_usd` não eram editáveis (o teste parametrizado de `tests/test_config_editor.py` perto de `field_name` e o teste de `tests/test_dashboard_config_api.py` que envia `cost_per_m_tokens: 99`): só um campo realmente desconhecido continua recusado. Em `tests/test_config_editor_fields.py`, teste cada campo novo gravando e relendo o arquivo com os comentários preservados, e cada regra de validação inválida.

### Task 2: Criar, remover e renomear vias no editor

**Files:**
- Modify: `meister/config_editor.py`
- Modify: `tests/test_config_editor.py`
- Create: `tests/test_config_editor_lanes_crud.py`

**Depends on:** Task 1

Siga o contrato dos Global Constraints. Em `_merge_lanes`, identifique cada via do pedido por `original_name` (ou `name`): se existir no arquivo, atualiza (e renomeia quando `name` difere); se não existir e não houver `original_name`, é nova e exige `name`, `harness`, `model` e `cost_per_m_tokens` (erro por campo se faltar; os outros campos seguem o padrão do `WorkerTier` e a chave é omitida). Vias existentes ausentes do pedido são removidas: apague a mensagem `missing_lanes` e a recusa `unknown_lane`, e ajuste os testes `test_unknown_lane_cannot_be_created` e `test_missing_lane_cannot_be_removed` de `tests/test_config_editor.py` ao novo comportamento. `original_name` não conta como campo recusado.

Regras novas de nome, só para vias novas ou renomeadas: expressão `^[a-z][a-z0-9_]*\d[a-z]?$` (aceita `tier_1`, `tier_1b`, `tier_2`; recusa `fast`, `tier_`, `tier_1bb`); nome único; o nível (número no fim do nome) não pode diminuir ao longo da lista, olhando só as vias que têm nível. Mantenha "pelo menos uma via ativa". Em `_tier_order_seq`, localize o nó existente pelo nome **original** (guarde-o num campo interno do dicionário mesclado e remova-o antes de gravar), para que uma via renomeada mantenha seus comentários; vias novas entram por `_new_lane_node` com todas as chaves preenchidas, sem as nulas.

Em `tests/test_config_editor_lanes_crud.py`: criar uma via no meio e no fim; remover; renomear mantendo o comentário do nó; renomear para nome repetido; nomes inválidos; nível decrescente; via nova sem `cost_per_m_tokens`; um config com vias de nome não padronizado (por exemplo `fast`) gravando sem erro quando não renomeadas.

### Task 3: API do dashboard com vias novas, removidas e renomeadas

**Files:**
- Modify: `meister/dashboard/server.py`
- Modify: `tests/test_dashboard_config_api.py`

**Depends on:** Task 2

Confirme que `PUT /api/config/lanes` aceita o contrato dos Global Constraints de ponta a ponta (a lógica está no editor; altere `server.py` só se algo no mapeamento de erros ou no retorno impedir) e que `GET /api/config` e a resposta do `PUT` devolvem todos os campos de cada via. Os erros 422 trazem em `fields` os caminhos `lanes[i].campo` de cada campo novo. Em `tests/test_dashboard_config_api.py`, cubra com o cliente Flask: criar, remover e renomear vias; gravar todos os campos; 422 de nome inválido, de nome repetido, de nível decrescente e de campo numérico inválido; e que 409 (run ativa e carimbo), 403 (cabeçalhos do Tailscale) e 400 (CSRF) continuam iguais.

### Task 4: Funções puras da cadeia no bloco de helpers

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Create: `tests/test_config_js_chain.py`
- Modify: `tests/test_config_js_harness.py`

Dentro do bloco `// helpers:begin` … `// helpers:end` de `config.html` (sem `window` nem `document`), acrescente funções puras, partindo do protótipo: `levelOf(name)` (número no fim do nome, ou `null`); `groupByLevel(lanes)` (grupos consecutivos `{level, indexes}`); `swapContent(lanes, i, j)` (troca só `harness`, `model`, `effort`, `cost_per_m_tokens` e `credit_usd`, sem mutar a entrada); `levelCostSteps(lanes)` (por par de níveis vizinhos, o menor custo das vias ativas de cada um e a razão); `simulatePath(lanes, cls, startName, failedNames)` devolvendo o índice inicial, o índice em que executa, as vias tentadas, se houve escalonamento e o estado (`ok`, `all_failed`, `no_eligible`), seguindo a regra: a via inicial respeita `eligible_classes` (vazio = todas), a queda segue para frente sem filtrar por classe e pula vias desligadas; `nextLaneName(lanes, level)` e `nextLevelName(lanes)`; `newLaneFrom(lane, name)` (cópia marcada como nova); `validateChain(lanes)` com as mesmas regras do servidor (nome, nível decrescente, modelo vazio, números, nenhuma via ativa), devolvendo códigos e índices, sem texto; `diffChain(original, current)` (adicionada, removida, campo alterado, ordem alterada).

Ajuste `buildLanesBody` para enviar todos os campos editáveis e `original_name` das vias que já existiam (vias marcadas como novas não enviam `original_name`), mantendo `lanesChanged`, `moveLane` e o resto como estão, porque a página atual continua funcionando nesta tarefa. Atualize `tests/test_config_js_harness.py` se ele afirmar o formato antigo do corpo. Em `tests/test_config_js_chain.py`, teste cada função via `run_js` (de `tests/config_js.py`), inclusive a simulação: via inicial por classe, falhas encadeadas, escalonamento, todas falharam e nenhuma via aceita a classe.

### Task 5: Página com a cadeia por níveis e o simulador

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_dashboard_config_page.py`

**Depends on:** Task 4

Troque a tabela de vias pelo desenho do protótipo, só de exibição nesta tarefa. Painel "Caminho de uma tarefa": seleção da classe, da via inicial (as ativas que aceitam a classe) e a fila de vias, em que tocar numa via simula falha; a frase de resultado diz onde executa, o que falhou e se houve escalonamento (usa `simulatePath`). Abaixo, um bloco por nível, com a ordem de tentativa escrita e, entre níveis, a faixa de escalonamento com o salto de custo (`levelCostSteps`). Cada slot mostra o nome, os chips de política (ativa ou desligada, tentativas, classes, paralelo) e o cartão de conteúdo (harness, modelo, esforço, custo). Mantenha os avisos (run ativa, somente leitura, arquivo mudou, erro de carga), os botões Salvar, Reverter e Restaurar padrão e o estado atual da página (`state`, `applyData`, `load`, `write`); a edição das vias volta nas tarefas 6 e 7.

Todo texto novo entra nos dois catálogos (`cli.config.web.*`) e na lista de `_config_messages`; nada de texto fixo no HTML. Atualize `tests/test_dashboard_config_page.py` para conferir os textos novos nos dois idiomas e que a página servida traz o bloco de helpers uma única vez.

### Task 6: Gaveta de edição de todos os campos

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_dashboard_config_page.py`

**Depends on:** Task 5

Cada slot ganha o botão **editar**, que abre uma gaveta lateral (tela cheia em tela estreita) com dois grupos nomeados: "Conteúdo, viaja ao arrastar" (harness, esforço conforme o harness, modelo em campo de texto, custo por 1M de tokens, crédito) e "Política do slot, fica na posição" (nome com o aviso de que renomear não migra o histórico, ativa, tentativas, em paralelo, tempo ocioso máximo, duração máxima, classes que o Jev pode atribuir e `best_for` separado por vírgula). As edições atualizam o estado e a cadeia na hora. Os erros do cliente (`validateChain`) e do servidor (`mapFieldErrors`, caminhos `lanes[i].campo`) aparecem junto do campo. "Remover via" pede um segundo toque para confirmar. Com a edição bloqueada (run ativa ou acesso remoto) a gaveta abre só para leitura, com os campos desabilitados e sem remover.

Textos novos nos dois catálogos e em `_config_messages`; atualize `tests/test_dashboard_config_page.py` para conferir os textos da gaveta nos dois idiomas.

### Task 7: Troca de conteúdo, criação de vias e painel de alterações

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_dashboard_config_page.py`

**Depends on:** Task 3, Task 6

Arrastar o cartão de conteúdo de um slot para outro troca o conteúdo dos dois (`swapContent`); como alternativa de toque, tocar num cartão (que ganha contorno) e depois no cartão de outro slot faz a mesma troca, e tocar de novo no mesmo cartão cancela. Nada disso funciona com a edição bloqueada. O cartão cujo conteúdo difere do arquivo ganha contorno âmbar.

Criação: **+ via neste nível** acrescenta ao fim do nível uma cópia da última via (`newLaneFrom`, nome de `nextLaneName`) e abre a gaveta; **+ novo nível** acrescenta no fim da cadeia uma via com o nome de `nextLevelName` e abre a gaveta (não há inserção de nível no meio). Ao carregar, cada via guarda o `original_name`; vias criadas na página são marcadas como novas.

Painel "Alterações a gravar": lista `diffChain` (vias novas, removidas, campos alterados, ordem) e os erros de `validateChain`; **Salvar** fica desabilitado enquanto houver erro do cliente ou nenhuma alteração; **Reverter** volta ao arquivo; depois de gravar com sucesso a página recarrega o estado do servidor. Textos novos nos dois catálogos e em `_config_messages`; atualize `tests/test_dashboard_config_page.py` para os textos novos nos dois idiomas.
