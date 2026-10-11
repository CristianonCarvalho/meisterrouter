# Tela `/config` do dashboard: ver e editar as vias dos workers

**Status:** implementado (2026-10-11). Ficou fora desta etapa: edição de custos, do roteador e do gate (inclusive concorrência e `tolerated_files`), edição remota (pelo celular ou pela tailnet, só leitura), criação ou remoção de vias e autenticação. Documentação: `docs/advanced.md` e `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`.
**Base observada:** `main` em `87ba1e6` + PR #141. Números de linha são aproximados; prefira buscar pelo nome da função.
**Origem:** conversa de 2026-10-10/11 com o dono. Decisões do dono: a primeira etapa edita **só as vias**; a edição é **bloqueada com run ativa**; a gravação **preserva os comentários** do `meister.config.yaml` (`ruamel.yaml`); a edição é **só local** (o celular, pela tailnet, só lê); há um **link para `/config` na timeline**.

## 1. Problema

Hoje as vias (`workers.tier_order`) só mudam editando o YAML à mão ou com `meister models --enable/--disable` (que só liga e desliga). Para trocar a **ordem**, o **modelo**, o **harness** ou o **esforço** de uma via é preciso abrir o arquivo, copiar a lista inteira do padrão (listas **substituem**, não mesclam) e não errar nada. Além disso, a timeline descreve as vias pelo config atual, então ver a configuração em uso exige abrir o arquivo.

## 2. Objetivos e não objetivos

**Objetivos**
1. Mostrar na tela as vias **efetivas** (padrão + o que o projeto sobrescreve), com origem do arquivo.
2. Editar: **ligar e desligar, reordenar, modelo, harness, esforço e `max_parallel`**.
3. Gravar sem destruir os comentários do `meister.config.yaml`, com `.bak` e troca atômica.
4. Validar antes de gravar com a **mesma lógica** do `meister config validate`.
5. Bloquear a escrita com run ativa e para acesso remoto.
6. Link de ida e volta entre a timeline e `/config`.

**Não objetivos (esta etapa):** editar custos, roteador, gate, concorrência e `tolerated_files` (só leitura ou fora da tela); edição pelo celular; criar ou remover vias do catálogo (só as existentes); autenticação.

## 3. Componentes

### 3.1 `meister/config_editor.py` (lógica pura, sem Flask)
- `load_effective_lanes(config_path) -> LaneDocument`: lê o `meister.config.yaml` do projeto em round-trip (`ruamel.yaml`) e devolve as vias efetivas, o caminho do arquivo, se `tier_order` existe no arquivo e um **carimbo** (hash do conteúdo) para detectar mudança no disco.
- `validate_lanes(document, lanes) -> list[ConfigIssue]`: monta o documento resultante **em memória** e passa por `_parse_config_dict` e `validate_config` (`meister/config.py`). Pelo menos uma via ligada; nomes únicos; harness e esforço válidos (`meister/harness_effort.py`); modelo não vazio. Nada é gravado.
- `apply_lanes(document, lanes, expected_stamp) -> LaneDocument`: confere o carimbo; grava `workers.tier_order` (a lista inteira); acrescenta o comentário "gerado pela tela /config"; faz `.bak` antes; troca atômica com restauração se algo falhar (mesmo padrão de `meister/lane_toggle.py`); mantém o modo (permissões) do arquivo.
- Campos editáveis: `name` (só leitura), `harness`, `model`, `effort`, `enabled`, `max_parallel`, e a **ordem**. Os demais campos de cada via (`cost_per_m_tokens`, `credit_usd`, `best_for`, `eligible_classes`, `max_retries`...) são **preservados** como estão.

### 3.2 Servidor (`meister/dashboard/server.py`)
- `GET /config`: a página.
- `GET /api/config`: `{lanes, source, stamp, editable, reason}`. `editable` é falso, com o motivo, quando há run ativa (`_has_active_run()`) ou o pedido veio pelo Tailscale.
- `PUT /api/config/lanes`: corpo `{stamp, lanes}`.
  - **409** com run ativa ("em andamento") ou carimbo diferente ("o arquivo mudou no disco");
  - **403** se o pedido trouxer cabeçalhos do Tailscale (`Tailscale-User-Login` e afins; o `tailscale serve` entrega as requisições de `127.0.0.1`, então "só localhost" não basta);
  - **422** com as mensagens da validação, por campo;
  - exige `Content-Type: application/json` e o cabeçalho `X-Meister-Config: 1` (proteção contra CSRF de outra página aberta no navegador); rejeita campos fora da lista editável.
- A leitura continua liberada para qualquer origem.

### 3.3 Página `meister/dashboard/templates/config.html`
Mesmo estilo da timeline (tema claro e escuro, mobile). Tabela de vias com subir e descer, liga e desliga, `harness` (lista), `model` (texto), `effort` (lista conforme o harness) e `max_parallel`; faixas "Bloqueado: run em andamento" e "Somente leitura (acesso remoto)"; botões **Salvar** e **Reverter**; mensagens de validação por campo. Textos pelos catálogos `en` e `pt-BR` (`t()`).

### 3.4 Links
Botão **Config** no cabeçalho de `timeline.html`, ao lado de "Dashboard". `config.html` tem links para a timeline e para o dashboard.

## 4. Fluxo
1. A página chama `GET /api/config` e mostra as vias, com `editable` e `stamp`.
2. O usuário edita localmente (JavaScript) e clica em Salvar.
3. A página manda `PUT /api/config/lanes` com `stamp` e as vias.
4. O servidor revalida tudo (nunca confia no navegador), grava, devolve o documento novo e o carimbo novo.
5. O dashboard relê o config a cada requisição: a timeline passa a mostrar as vias novas sem reiniciar. (A run seguinte também; uma run ativa **já carregou** o config e não é afetada, e por isso a escrita fica bloqueada durante ela.)

## 5. Decisões e riscos
- **`ruamel.yaml`** vira dependência de runtime (entra em `pyproject.toml` e `requirements.txt`; verificar compatibilidade com Python 3.10 a 3.13 no CI). O `lane_toggle` continua com PyYAML e fica como está; avaliar migrá-lo depois, para unificar.
- **Sem autenticação:** a proteção é por origem (cabeçalhos do Tailscale) e CSRF. Quem tiver acesso local ao Mac pode escrever. Aceito nesta etapa.
- **Cabeçalhos do Tailscale** podem ser forjados por quem alcance a porta local diretamente; mitigado porque o servidor só escuta em `127.0.0.1` (`--allow-remote` desligado).
- **Config copiado do padrão:** ao gravar `tier_order`, a lista passa a **substituir** o padrão e deixa de acompanhar mudanças futuras do padrão (novas vias, preços). Mostrar isso na tela ("esta lista agora é do projeto") e oferecer **Restaurar padrão** (remove `tier_order` do arquivo).
- **Cinco comentários** do `meister.config.yaml` do dono devem sobreviver ao round-trip: teste de regressão com o arquivo real (cópia).
- **Timeline:** a raia só mostra harness e modelo se a via estiver no config atual (defeito já registrado no backlog); a tela `/config` ajuda a ligar a `tier_3b`, mas a correção definitiva (metadados no evento `worker_spawn`) é outro item.

## 6. Testes
- `tests/test_config_editor.py`: round-trip preserva comentários e campos não editáveis; validação recusa (nenhuma via ligada, nome repetido, esforço inválido para o harness, modelo vazio); `.bak`, troca atômica e restauração em falha; carimbo diferente recusa; permissões preservadas; "Restaurar padrão".
- `tests/test_dashboard_config_api.py` (cliente Flask): 409 com run ativa, 409 de carimbo, 403 por cabeçalho do Tailscale, 422, CSRF (sem o cabeçalho ou com outro Content-Type), campo fora da lista, leitura liberada com cabeçalho do Tailscale.
- JavaScript da página (reordenar, montar o corpo, estados de bloqueio): helpers testados via `node`, como `tests/timeline_js.py`.
- Teste de paridade dos catálogos e `tests/test_i18n_ratchet.py`.
- **Verificação real:** subir o dashboard com um `meister.config.yaml` de teste num diretório temporário, editar uma via por `curl` e conferir o arquivo; e conferir o 403 pelo Tailscale com o cabeçalho.

## 7. Divisão sugerida em tarefas (para o plano; muitas e pequenas)
1. `ruamel.yaml` como dependência e `config_editor.load_effective_lanes` com teste de round-trip.
2. `config_editor.validate_lanes`.
3. `config_editor.apply_lanes` (`.bak`, atômico, carimbo) e "Restaurar padrão".
4. API: `GET /api/config` e as regras de bloqueio (`editable`).
5. API: `PUT /api/config/lanes` (409, 403, 422, CSRF).
6. Página `config.html` (somente leitura) + link na timeline.
7. Página: edição, reordenação e salvar (JavaScript e `node`).
8. Textos nos dois catálogos, documentação (`docs/advanced.md` e pt-BR) e CHANGELOG.
