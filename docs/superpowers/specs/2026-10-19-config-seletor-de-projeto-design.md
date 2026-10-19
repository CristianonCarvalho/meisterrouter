# Tela /config com seletor de projeto

**Status:** aprovado pelo dono em 2026-10-19; plano em `docs/superpowers/plans/2026-10-19-config-seletor-de-projeto.md`. Nada implementado.
**Origem:** o dono avaliou a configuração global (spec e plano descartados e apagados antes de qualquer merge) e decidiu **manter a configuração por projeto**, mas fazer a tela `/config` mostrar **em qual projeto se está configurando** e permitir **trocar de projeto numa caixa de seleção**. A lista de projetos vem de um **registro automático** mais um botão **"Descobrir projetos"** que varre uma pasta indicada.
**Estende:** `2026-10-17-config-cadeia-de-vias-design.md` (a tela `/config`).

## 1. Problema

A `/config` edita o `meister.config.yaml` do projeto em que o dashboard foi iniciado. Para configurar outro projeto é preciso abrir o dashboard dele (`meister config web` na pasta dele), e a página não mostra de qual projeto é o arquivo que está sendo editado, o que convida a editar o projeto errado. O Meister também não sabe quais projetos existem na máquina: não há registro.

## 2. Objetivos e não objetivos

**Objetivos**
1. A página mostra sempre o projeto em edição (nome e caminho) e uma caixa de seleção para trocar de projeto.
2. A lista de projetos é montada por registro automático (cada comando registra o projeto em que roda) e por um botão "Descobrir projetos" (varre uma pasta, o dono confirma) e "Adicionar projeto" (digitando a pasta).
3. Ler e gravar as vias de outro projeto com as mesmas proteções de hoje (só local, 403 do Tailscale, CSRF, `.bak`, troca atômica, bloqueio com run ativa), **por projeto**.
4. Só projetos registrados podem ser lidos ou gravados pela página.

**Não objetivos:** configuração global (descartada); copiar as vias de um projeto para todos os outros (um botão "replicar" pode vir depois); editar gate, escopo ou outras seções (continua só vias, como na etapa anterior); remover projetos do disco.

## 3. Registro de projetos

Arquivo `~/.meister/projects.json` (`os.path.expanduser("~")`, que no Windows usa `USERPROFILE`), gravado de forma atômica:

```json
{"version": 1, "projects": [{"path": "/abs/projeto", "name": "projeto", "last_seen": "2026-10-19T12:00:00Z"}]}
```

- **Identificador:** os 12 primeiros caracteres hexadecimais do SHA-1 do caminho real (`realpath`). A página e a API só usam o identificador, nunca um caminho.
- **Registro automático:** `ensure_dashboard` (usado pelo `orchestrate` e por `meister config web`) e o início do servidor do dashboard registram a raiz do projeto resolvida. Falha ao registrar nunca derruba o comando.
- **Validade:** um projeto é aceito se o caminho é um diretório absoluto existente que contém `.meister/`, `meister.config.yaml` ou `.git`; a pasta pessoal e `/` são recusadas. Projetos cujo diretório sumiu continuam listados, marcados como ausentes, e não podem ser gravados.
- **Descobrir:** varre uma pasta base absoluta (profundidade até 3, sem seguir links simbólicos, ignorando `node_modules`, `.git`, `.venv`, `venv`, `dist`, `build`, `__pycache__` e `worktrees`), devolve os diretórios com `.meister/` ou `meister.config.yaml`; o dono marca quais registrar.

## 4. API do dashboard

- `GET /api/projects`: `{projects: [{id, name, path, exists, active_run}], current}` (o `current` é o projeto em que o dashboard foi iniciado). Leitura liberada.
- `POST /api/projects` `{path}`: acrescenta um projeto (validações da seção 3). `POST /api/projects/discover` `{base}`: devolve candidatos, sem registrar. `POST /api/projects/register` `{paths: [...]}`: registra os candidatos escolhidos. As três são escritas: mesmos cabeçalhos de proteção (CSRF), 403 para pedidos remotos.
- `GET /api/config?project=<id>` e `GET /api/config/models` continuam; o `GET /api/config` aceita `project` (sem ele, o projeto atual) e devolve também `project: {id, name, path}`.
- `PUT /api/config/lanes` e `POST /api/config/restore-default` aceitam `project` no corpo; o servidor resolve o identificador **pelo registro** (desconhecido: 404 `unknown_project`; ausente do disco: 409 `project_missing`).
- **Run ativa por projeto:** a escrita é recusada (409 `run_active`) se o **projeto escolhido** tem run ativa, lida do log dele (`<projeto>/.meister/logs/orchestration_log.jsonl`) com a mesma lógica de hoje; log ausente ou ilegível conta como sem run.

## 5. Página

Barra no alto: caixa de seleção "Projeto" (nome; caminho como dica), faixa fixa "Configurando: `<caminho>`", botões "Descobrir projetos" (painel com campo de pasta, lista de candidatos com caixas de seleção e "Registrar selecionados") e "Adicionar projeto" (campo de pasta). Trocar o projeto recarrega as vias (com confirmação se houver alterações não salvas); o carimbo, o diff, o simulador, a gaveta e a árvore de modelos passam a valer para o projeto escolhido. A escolha vale só para a sessão da página (não é gravada). Textos nos catálogos `en` e `pt-BR`.

## 6. Riscos

- **Editar o projeto errado:** mitigado pela faixa fixa e pela confirmação ao trocar com alterações pendentes.
- **Gravar fora de um projeto:** mitigado por só aceitar identificadores do registro e validar a pasta ao adicionar.
- **Registro desatualizado:** projetos movidos ou apagados ficam marcados como ausentes; "Descobrir" os reencontra.
- **Testes dependem de `HOME`:** um fixture automático redireciona `HOME` e `USERPROFILE` em todos os testes, para nenhum ler o `~/.meister/projects.json` real do dono.
