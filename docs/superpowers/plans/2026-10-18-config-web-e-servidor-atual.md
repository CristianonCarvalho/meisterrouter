# `meister config web` e servidor do dashboard sempre atual Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** pedido do usuário (2026-10-18): "uma forma de abrir fora do dashboard" a tela `/config`. Desenho aprovado em conversa: o comando `meister config web` abre a configuração direto, reaproveitando o servidor do projeto. Motivo da checagem de versão: dashboards antigos (portas 5051, 62155, 62297 e 63679) serviam o HTML novo da `/config` com o código velho do servidor, então 80 chaves de texto faltavam e a página mostrava "undefined" e botões sem rótulo.

**Goal:** `meister config web` abre `/config` do projeto da pasta atual (janela ou aba, ou só a URL), sobe um servidor novo quando o que está rodando é de uma versão antiga do código, e a própria página avisa quando o servidor é mais antigo que ela, em vez de mostrar "undefined".

**Architecture:** O estado do dashboard (`.meister/dashboard.json`) passa a guardar um identificador do código que o servidor roda. O `ensure_dashboard` ganha a página a abrir e a opção de exigir o código atual. O comando novo usa as duas. A página trata a falta de textos.

**Tech Stack:** Python ≥ 3.10, `click`, Flask, `pytest`, `ruff`, `mypy`, HTML/JS puro com `node` nos testes.

## Global Constraints

- O `meister orchestrate` e a timeline não mudam de comportamento: o `ensure_dashboard` chamado como hoje reaproveita o servidor vivo, abre `/timeline` e respeita `dashboard.open`. Os testes existentes de `tests/test_dashboard_launcher.py` continuam passando.
- Nenhum processo é encerrado por este código: um servidor de código antigo é só deixado de lado (ele sai sozinho por ociosidade) e um novo é iniciado.
- A edição da `/config` continua só local (403 para pedidos com cabeçalhos do Tailscale), bloqueada com run ativa; nada disso muda.
- Texto novo de comando e mensagens em `meister/locales/en_cli.py` e `pt_br_cli.py`, via `t()`; sem texto fixo no código, exceto o aviso da Task 4 (ver lá). `tests/test_i18n_ratchet.py` não pode piorar.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: O estado do dashboard guarda o código do servidor

**Files:**
- Create: `meister/code_id.py`
- Modify: `meister/dashboard/state.py`
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_code_id.py`
- Modify: `tests/test_dashboard_state.py`

Crie `meister/code_id.py` com `current_code_id() -> str`: a versão do pacote (`meister.__version__`) e, quando o pacote vive num checkout git, o commit curto de `HEAD` (por exemplo `0.11.0+f4361aa`; sem git, só `0.11.0`). Use `git rev-parse --short HEAD` com timeout curto e devolva só a versão se falhar; guarde o resultado em cache de processo. Em `meister/dashboard/state.py`, acrescente a `ServerState` o campo `code_id: str = ""`; `read_state` aceita arquivos antigos sem o campo (valor vazio) e `write_state` o grava. Em `start_server` (`meister/dashboard/server.py`, os dois lugares que criam `ServerState`), grave `code_id=current_code_id()`. Testes: `current_code_id` com e sem git (mockando o subprocesso), ida e volta do estado com o campo, leitura de um `dashboard.json` antigo sem `code_id`, e que `start_server` grava o código atual (sem subir servidor de verdade, como os testes existentes de `tests/test_dashboard_state.py` já fazem).

### Task 2: `ensure_dashboard` abre qualquer página e pode exigir o código atual

**Files:**
- Modify: `meister/dashboard/launcher.py`
- Modify: `tests/test_dashboard_launcher.py`
- Create: `tests/test_dashboard_launcher_view.py`

**Depends on:** Task 1

Em `ensure_dashboard` acrescente os parâmetros só-nomeados `view: str = "timeline"`, `require_current_code: bool = False` e `url_only: bool = False`; com os valores padrão o comportamento atual não muda. `view="config"` abre `<url>/config` em vez de `/timeline` (generalize `_format_timeline_url`, mantendo o nome antigo funcionando). Para uma `view` diferente de `timeline`, o atalho "janela já aberta, não abre nada" não vale: sempre abre a página pedida. Com `require_current_code=True`, um servidor vivo cujo `state.code_id` difere de `current_code_id()` (inclusive vazio) **não é reaproveitado**: inicia um servidor novo (mesma exclusão por lock) e espera aparecer um estado vivo com `code_id` igual ao atual, sem confundir com o estado do servidor antigo; o prazo de espera sobe de 2 s para 8 s nesse caso. O `Outcome` ganha o campo opcional `fresh: bool = False`, verdadeiro quando um servidor novo foi iniciado por causa do código antigo. Com `url_only=True`, ou com `dashboard.open: never` quando `view != "timeline"`, o servidor é garantido e devolve `Outcome(kind="url_only", url=...)` sem abrir janela. Sem tela (`has_display` falso) continua `url_only`. Testes (sem subir servidor de verdade, no estilo de `tests/test_dashboard_launcher.py`): URL de `/config`; janela já aberta mas página `config` abre mesmo assim; servidor com código diferente inicia outro e marca `fresh`; servidor com código igual é reaproveitado; estado antigo sem `code_id` conta como diferente; `url_only`; `open: never`; os testes existentes seguem passando.

### Task 3: Comando `meister config web`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Create: `tests/test_cli_config_web.py`

**Depends on:** Task 2

Acrescente ao grupo `config` o comando `web` com as opções `--config-path/-c` (mesmo significado dos outros comandos do grupo) e `--no-open`. Ele resolve a raiz do projeto a partir da pasta atual (`resolve_project_root`, como o `orchestrate`), carrega a configuração (seção `dashboard`) e chama `ensure_dashboard(project_root, config, view="config", require_current_code=True, url_only=<--no-open>)`. Saída por tipo de resultado, todas via `t()`: janela ou aba aberta (mostra a URL); só URL (sem tela, `--no-open` ou `open: never`: imprime a URL numa linha para copiar); falha (mensagem no stderr e código de saída 1). Quando `outcome.fresh` for verdadeiro, avise numa linha que o dashboard anterior era de uma versão antiga do código e que um novo foi iniciado (o antigo sai sozinho por ociosidade). Textos de `--help` e mensagens nos dois catálogos. Testes em `tests/test_cli_config_web.py` com `ensure_dashboard` simulado (o padrão `_DEFAULT_ENSURE_DASHBOARD` do `cli.py` mostra como): argumentos repassados, cada tipo de resultado e o texto, `--no-open`, falha com saída 1, o aviso de servidor novo, e que o comando aparece em `meister config --help` nos dois idiomas.

### Task 4: A página avisa quando o servidor é mais antigo

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `tests/test_dashboard_config_page.py`
- Create: `tests/test_config_js_fallback.py`

Hoje, se o servidor não manda as chaves de texto que a página usa, o JavaScript mostra "undefined" e botões sem rótulo. No bloco `helpers:begin`/`helpers:end` de `config.html`, acrescente uma função pura `withTextFallback(text, requiredKeys)` que devolve o objeto de textos com cada chave ausente preenchida pelo próprio nome da chave, e a lista `missingTextKeys(text, requiredKeys)`. Liste em `requiredKeys` as chaves que o JavaScript da página consulta (`text.<chave>`; extraia-as do próprio arquivo, e um teste confere que a lista cobre todas as chaves usadas). Se faltar alguma, a página mostra no alto um aviso e continua funcional, com os nomes das chaves no lugar dos textos. O aviso é a única exceção à regra de não ter texto fixo: como o servidor velho não pode fornecê-lo, ele vem no próprio HTML, em inglês e em português lado a lado ("Restart the dashboard: the server is older than this page." e "Reinicie o dashboard: o servidor é mais antigo que esta página."). Testes: `withTextFallback` e `missingTextKeys` via `run_js` de `tests/config_js.py`, a cobertura das chaves usadas e, em `tests/test_dashboard_config_page.py`, que a página servida contém o aviso nos dois idiomas e que, com todas as chaves presentes, ele fica oculto.
