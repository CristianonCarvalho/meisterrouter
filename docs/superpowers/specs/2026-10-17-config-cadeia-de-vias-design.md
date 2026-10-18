# Tela `/config`: cadeia de vias com slots, criação de vias e edição completa

**Status:** desenho aprovado pelo dono em 2026-10-17; plano por escrever. Nada implementado.
**Estende:** `2026-10-11-config-screen-design.md` (a tela `/config` atual, implementada).
**Protótipo de referência:** `2026-10-17-config-cadeia-de-vias-prototipo.html` (arquivo único, dados de exemplo, nada é gravado). O desenho abaixo é o que o protótipo mostra; em caso de dúvida visual, vale o protótipo.
**Origem:** conversa de 2026-10-17 com o dono, durante a run do chronoastro em que o Copilot ficou sem créditos e a cadeia caiu de `tier_1` e `tier_1b` direto para o `tier_2`.

## 1. Problema

A `/config` de hoje é uma tabela de vias com `↑`/`↓`. Três limites aparecem no uso real:

1. **Mover a linha move o nome.** O nome da via carrega significado: a timeline trata `tier_1b` como lateral a `tier_1` (`viaLevel`), o circuit breaker é gravado por nome, e `eligible_classes`, `max_parallel` e `best_for` pertencem à posição. Reordenar linhas reordena esses nomes e quebra as ligações.
2. **A cadeia de fallback não é visível.** Não dá para ver o que acontece quando uma via falha, nem onde há escalonamento e quanto o custo sobe.
3. **Só dá para editar parte da via e não dá para criar vias.** Custos, tentativas, timeouts, classes e `best_for` exigem editar o YAML à mão; uma via nova (`tier_1d`) também.

## 2. Objetivos e não objetivos

**Objetivos**
1. Separar **slot** (posição na cadeia: nome e política) de **conteúdo** (harness, modelo, esforço, custo). O dono arrasta o **conteúdo**; o nome e a política ficam na posição.
2. Mostrar a cadeia em **níveis** com os dois tipos de passagem (lateral `⇄` e escalonamento `⤹`) e o salto de custo entre níveis.
3. **Simular** o caminho de uma tarefa (classe, via inicial, vias que falham) sem executar nada.
4. **Criar e remover vias**, e **editar todos os campos** de uma via.
5. Escolher o modelo numa **árvore** `harness → modelos`, alimentada por um catálogo em camadas, sem gastar créditos.
6. Funcionar com toque (celular em leitura e telas sensíveis ao toque no local): arrastar **ou** tocar e tocar.

**Não objetivos (esta etapa)**
- Edição remota. Continua só local, como na etapa anterior (403 com cabeçalhos do Tailscale); no celular pela tailnet a tela é só leitura.
- Editar roteador, gate, concorrência global e `tolerated_files`.
- Inserir um nível **no meio** da cadeia (ver 5.2).
- Mudar o algoritmo de fallback do motor (`get_next_available_tier`) ou o circuit breaker; a tela só os mostra.

## 3. Modelo de dados

Uma via (`workers.tier_order[]`, `WorkerTier` em `meister/config.py`) tem dois grupos de campos:

| Grupo | Campos | Comportamento |
|---|---|---|
| **Conteúdo** (viaja ao arrastar) | `harness`, `model`, `effort`, `cost_per_m_tokens`, `credit_usd` | trocam de posição entre dois slots |
| **Política do slot** (fica) | `name`, `enabled`, `max_retries`, `max_parallel`, `idle_timeout_seconds`, `max_runtime_seconds`, `eligible_classes`, `best_for` | permanecem na posição |

O **nível** de uma via vem do número no nome (`tier_1`, `tier_1b`, `tier_1c` são o nível 1; `tier_2` é o nível 2), a mesma regra de `viaLevel` na timeline (`timeline.html`). Dentro de um nível, a ordem do arquivo é a ordem de tentativa lateral; entre níveis, é o escalonamento. A ordem do arquivo não pode voltar atrás de nível.

## 4. Desenho da tela

Ver o protótipo. Resumo dos blocos, de cima para baixo:

1. **Simulador "Caminho de uma tarefa".** Classe da tarefa (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`), via inicial (a que o Jev escolheria; o dono escolhe entre as vias ativas que aceitam a classe) e a cadeia em fila. Tocar numa via simula falha (`risca` a via e passa à seguinte). A frase final diz onde a tarefa executa, o que falhou antes e se houve escalonamento; "todas falharam" e "nenhuma via aceita a classe" também aparecem.
2. **Cadeia por níveis.** Cada nível mostra a ordem de tentativa escrita (`tier_1 ⇄ tier_1b ⇄ tier_1c`). Entre níveis, uma faixa de escalonamento em linguagem simples com o salto de custo (menor custo ativo de cada nível, razão em `×`).
3. **Slot.** Nome, "editar", chips de política (ativa, tentativas, classes, paralelo) e o chip de **estado** da via (disponível ou "sem créditos"/em cooldown, vindo do circuit breaker, ver 6.3). Dentro do slot, o **cartão de conteúdo** (harness, modelo, esforço, custo) com contorno âmbar quando difere do arquivo.
4. **Trocar conteúdo.** Arrastar um cartão sobre outro slot **troca** o conteúdo dos dois; ou tocar num cartão e depois no cartão de outro slot. A política fica onde está.
5. **Criar.** "+ via neste nível" acrescenta uma alternativa lateral (`tier_1d`, `tier_2b`...), copiando o conteúdo da última via do nível; "+ novo nível" acrescenta o próximo nível no fim (`tier_4`). As duas abrem a gaveta de edição. Remover pede segundo toque.
6. **Gaveta de edição** (painel lateral; tela cheia no celular), dois grupos nomeados como na seção 3, todos os campos editáveis. O modelo tem a árvore e um campo de texto livre.
7. **Painel de alterações.** Diff campo a campo (`via.campo`), vias novas, vias removidas e mudança de ordem; erros de validação em vermelho. "Gravar" fica desabilitado enquanto houver erro; "Desfazer tudo" volta ao arquivo.

## 5. Regras e decisões

### 5.1 Validação (além da já existente)
- Nome: `^[a-z][a-z0-9_]*$`, terminando em número com letra opcional; único; o nível não pode retroceder na ordem do arquivo.
- Modelo não vazio; esforço válido para o harness (`harness_effort.py`); pelo menos uma via ativa; números não negativos (`max_parallel` e timeouts aceitam vazio = sem limite).
- Toda a validação roda **no servidor** (a mesma lógica de `meister config validate`); a página repete só para o retorno imediato.

### 5.2 Novo nível só no fim
Criar um nível no meio exigiria renumerar as vias seguintes, e o nome é a identidade da via em logs, timeline, breaker e histórico. Nesta etapa, "+ novo nível" só acrescenta no fim. "Renumerar" (com aviso de que o histórico não migra) fica como evolução.

### 5.3 Renomear
Permitido (o campo `name` já é editável no editor atual), com o aviso na gaveta: "renomear não migra o histórico". Renomear muda o nível se mudar o número; a validação de ordem cobre o resultado.

### 5.4 Elegibilidade na simulação
O Jev só pode atribuir a uma via as classes de `eligible_classes` (vazio = todas). A **queda** para a via seguinte não filtra por classe (`get_next_available_tier` só olha `enabled` e o circuit breaker). A simulação reproduz isso: a via inicial respeita a classe; a cadeia daí em diante anda para frente sem filtro. Verificar no código ao planejar; se mudar, ajustar a simulação, não o motor.

### 5.5 Gravar a lista inteira
Como hoje, gravar `tier_order` substitui a lista padrão inteira no arquivo do projeto, com `.bak`, troca atômica e carimbo de conteúdo; "Restaurar padrão" continua existindo. Vias novas entram no arquivo com todos os campos preenchidos.

## 6. Componentes

### 6.1 `meister/config_editor.py`
- `EDITABLE_FIELDS` passa a cobrir todos os campos do grupo "conteúdo" e "política" da seção 3.
- `_merge_lanes`/`validate_lanes`/`apply_lanes` aceitam **vias novas e removidas** (hoje combinam o pedido com as vias existentes) e identificam cada via pelo nome. Preservam os comentários do YAML (`ruamel.yaml`), a ordem recebida e os campos que a tela não conhece.
- Regras da seção 5.1 em `_lane_field_issues`/`validate_lanes`, reaproveitando `ConfigIssue`.

### 6.2 Catálogo de modelos (`meister/model_catalog.py`, novo)
Provedor `list_models(harness)` que combina, **em camadas** e sem consumir créditos:
1. **Comando do harness**, quando existe (`agy models`);
2. **Catálogo do repositório**: `docs/modelos_e_custos.csv` (modelo, preço combinado) e uma lista curada de aliases (`claude`: `haiku`, `sonnet`, `opus`);
3. **Uso recente**: modelos que aparecem no config do projeto e nos logs.

Cada item traz `{model, cost_per_m_tokens, source}`; a página usa a origem como rótulo. Cache em memória com TTL, **timeout curto**, e falha de uma camada nunca quebra a resposta (as outras valem). `codex`, `copilot` e `claude` não têm comando de listagem: dependem das camadas 2 e 3. Escolher um modelo do catálogo preenche `cost_per_m_tokens`; o dono pode digitar qualquer outro nome.

### 6.3 Servidor (`meister/dashboard/server.py`)
- `GET /api/config/models?harness=...`: a lista do catálogo (somente leitura, liberada para qualquer origem).
- `GET /api/config`: acrescenta por via `status` (`available`, `cooldown`, `no_credits`) lido do circuit breaker do projeto (`state.get_circuit_breaker`, por nome da via e por harness). Se o estado não puder ser lido, `status` vem ausente e a tela mostra "desconhecido".
- `PUT /api/config/lanes`: mesmo contrato e proteções da etapa anterior (409 run ativa/carimbo, 403 Tailscale, 422 validação, CSRF por cabeçalho e `Content-Type`); o corpo passa a poder ter vias novas, removidas e todos os campos das seções 3 e 5.1.

### 6.4 Página (`meister/dashboard/templates/config.html`)
Substitui a tabela pelo desenho da seção 4. Helpers puros (nível por nome, troca de conteúdo, simulação do caminho, custo do salto, diff, validação local) num bloco `helpers:begin`/`helpers:end` testado via `node`, como na timeline. Textos nos catálogos `en` e `pt-BR` (`t()`); sem texto fixo no HTML. O protótipo traz esses helpers prontos como ponto de partida (`levelOf`, `swap`, `renderPath`, `validate`, `renderDiff`).

## 7. Riscos
- **Estado do breaker pode enganar.** O breaker de hoje dura 60 s por via e é gravado só pelo nome da via. O chip mostra o que existe; mudar o cooldown ou gravar por harness é outro assunto (registrado na conversa de 2026-10-17, não decidido).
- **Catálogo defasado.** Modelos novos podem faltar na lista; o texto livre cobre. Mostrar a origem de cada item evita passar por "ao vivo" o que é do repositório.
- **Dependência do nome.** Nomes fora do padrão `tier_<n><letra>` quebram a regra de nível da timeline e da tela; a validação os recusa.
- **Run ativa lê o config no início.** Continua valendo o bloqueio de escrita durante a run.
- **Mobile.** Arrastar com o dedo não funciona nesse tipo de página; o "tocar e tocar" é o caminho no toque.

## 8. Testes
- `tests/test_config_editor.py`: vias novas e removidas; todos os campos; ordem; comentários preservados; validação de nome, nível, modelo, esforço, números e "nenhuma via ativa"; `.bak`, atômico, carimbo.
- `tests/test_model_catalog.py`: camadas, falha de uma camada, timeout, cache, ordenação e custo; sem chamar nada que gaste crédito (o comando é simulado nos testes).
- `tests/test_dashboard_config_api.py`: `status` por via, `GET /api/config/models`, `PUT` com vias novas/removidas e os códigos 409/403/422/CSRF.
- JavaScript (`node`): troca de conteúdo deixa a política no lugar; nível por nome; simulação (via inicial por classe, falhas, escalonamento, "todas falharam"); custo do salto; validação e diff.
- Paridade dos catálogos `en`/`pt-BR` e `tests/test_i18n_ratchet.py`; `tests/test_timeline_messages_keys.py` se a tela reutilizar chaves da timeline.
- **Verificação real:** dashboard num diretório temporário com um `meister.config.yaml` de teste; criar, editar, trocar e remover vias pela página e por `curl`; conferir o arquivo e os comentários; conferir o 403 com o cabeçalho do Tailscale; abrir no celular pela tailnet e confirmar a leitura.

## 9. Divisão sugerida em tarefas (muitas e pequenas)

**Fase 1: slots, criação e edição completa**
1. `config_editor`: todos os campos editáveis e validação de nome, nível e números.
2. `config_editor`: vias novas e removidas em `_merge_lanes`/`apply_lanes`, com teste de comentários.
3. API: corpo do `PUT` com vias novas/removidas e campos completos; testes de contrato.
4. Helpers JS puros (nível, troca de conteúdo, simulação, custo do salto, diff, validação) com testes `node`.
5. `config.html`: cadeia por níveis, slots e cartões, simulador.
6. `config.html`: gaveta de edição, criação e remoção, painel de diff, toque e arrastar.
7. Textos `en`/`pt-BR`, documentação (`docs/advanced.md` e pt-BR) e CHANGELOG.

**Fase 2: catálogo de modelos**
8. `model_catalog` (camadas 2 e 3) e testes.
9. Camada 1 (`agy models`) com timeout e cache.
10. `GET /api/config/models` e a árvore na gaveta.

**Fase 3: estado das vias**
11. `status` por via no `GET /api/config` (circuit breaker) e o chip no slot.
