# 🔮 MeisterRouter

**Framework de Orquestração Multi-Modelo Autônoma para Claude Code e OpenAI Codex.**

O **MeisterRouter** permite transferir a disciplina de desenvolvimento multi-modelo e custo otimizado para qualquer projeto em **Claude Code**, **OpenAI Codex** ou outros agentes baseados em terminal.

Ele combina o poder de decisão probabilística do **TypeSafe Jev Decisions API** (`typesafe/jev-1.13`) com a matriz de eficiência extrema de 2026:
- 🌙 **GPT-6 Luna (medium)** como Worker Primário (Custo: **$0.077/M tokens** — 90% mais barato que o Haiku com o dobro de inteligência)
- ✨ **Gemini 3.8 Flash** como Agente de Escalonamento e Código Difícil (Índice de Inteligência **40** no Artificial Analysis)
- 🏛️ **Claude Sonnet 5** e **OpenAI Codex** como Arquitetos e Orquestradores Gerais
- ⚖️ **TypeSafe Jev-1.13** como Máquina de Estados e Portão de Saída

---

## 🚀 Instalação Rápida

> [!NOTE]
> Como este repositório é **privado**, o comando `curl` público sem autenticação retorna `404 Not Found`. Utilize o comando oficial autenticado via **GitHub CLI (`gh`)** abaixo:

### ⚡ 1. Instalação em 1 Comando (GitHub CLI — Recomendado)
Se você possui a [GitHub CLI](https://cli.github.com/) instalada e logada (`gh auth status`):
```bash
gh api -H "Accept: application/vnd.github.raw" repos/CristianonCarvalho/meisterrouter/contents/bin/install.sh | bash
```

> **O que este script faz automaticamente:**
> 1. Clona/atualiza o repositório em `~/.local/share/meisterrouter` usando suas credenciais do GitHub.
> 2. Cria o ambiente virtual Python (`.venv`) e instala as dependências em modo editável.
> 3. Cria o executável global `meister` em `~/.local/bin/meister`.
> 4. Se o [Herdr](https://herdr.dev) estiver instalado, vincula o plugin nativamente (`prefix+m` e `prefix+M`).

---

### 📦 2. Instalação via Clone Manual (Git / SSH)
```bash
# Via GitHub CLI:
gh repo clone CristianonCarvalho/meisterrouter
cd meisterrouter
./bin/install.sh

# Ou via Git SSH:
git clone git@github.com:CristianonCarvalho/meisterrouter.git
cd meisterrouter
./bin/install.sh
```

---

### 🌐 3. Caso o Repositório Seja Público (ou com curl)
Se a visibilidade do repositório for pública (ou após `gh repo edit CristianonCarvalho/meisterrouter --visibility public`):
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash
```

---

### 🟢 4. Via Node.js / NPM
Se preferir utilizar através do wrapper Node.js:
```bash
# Instalação global a partir do repositório privado via SSH:
npm install -g git+ssh://git@github.com/CristianonCarvalho/meisterrouter.git

# Ou diretamente no diretório clonado:
npm install -g .
```
*(O runner Node.js em `bin/cli.js` gerencia o runtime e bootstrap do Python automaticamente)*.

---

### 🔌 5. Como Plugin Oficial do Herdr
O MeisterRouter possui integração nativa de primeira classe com o multiplexador de agentes **Herdr**:
```bash
# Vincular repositório local ao Herdr:
herdr plugin link /caminho/para/meisterrouter

# Ou se instalado via script:
herdr plugin link ~/.local/share/meisterrouter
```
**Atalhos no Herdr:**
- `prefix + m`: Ativa a orquestração autônoma de tarefas no workspace.
- `prefix + M`: Abre o Dashboard de Telemetria e Custos em TUI (painel overlay).

---

### 🐍 6. Instalação Manual com Python (Pip / Venv)
```bash
git clone git@github.com:CristianonCarvalho/meisterrouter.git
cd meisterrouter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

---

### 🔑 Configurar a Chave da API
Copie `.env.example` para `.env` ou configure sua chave do OpenRouter no shell:
```bash
cp .env.example .env
# Adicione sua OPENROUTER_API_KEY
```
Ou exporte diretamente nas suas variáveis de ambiente:
```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

---

## 🛠️ Como Usar

### 1. Inicializar as Regras em Qualquer Repositório
Para equipar qualquer projeto (ex: ChronoAstro, LicitAI, etc.) com as regras para Claude Code e Codex em 1 comando:
```bash
meister init --target /caminho/do/seu/projeto
```
Isso cria no projeto, sem sobrescrever arquivos de regras existentes:
- `CLAUDE.md` (instruções completas para Claude Code)
- `CODEX.md` (instruções completas para OpenAI Codex)
- `AGENTS.md` (diretivas unificadas para Cursor, Copilot e subagentes)
- Diretório de telemetria `.meister/logs/`

Hooks são opcionais e só são instalados com `--hooks`. O hook Git `pre-commit` resume
as alterações em stage e executa automaticamente os testes disponíveis (`npm test`,
`pytest` ou `cargo test`); ele pode bloquear o commit se os testes falharem. Hooks
existentes que não pertençam ao MeisterRouter são preservados. Use `--force` para
sobrescrever arquivos ou hooks existentes. `--no-hooks` continua aceito por
compatibilidade; sem `--hooks`, nenhum hook é instalado.

---

### 2. Comandos do Jev Decisions

O roteador padrão é `jev`: ele escolhe a via inicial de cada subtarefa. A configuração
fica em `meister.config.yaml`; para operação offline e determinística, defina
`router: {mode: first}`, que sempre usa a primeira via sem chamar o Jev.
`router.timeout_seconds` limita cada chamada, `router.max_attempts` define as tentativas
e `router.unavailable_cooldown_seconds` define por quanto tempo novas chamadas são
evitadas após uma falha. Os padrões são `10`, `2` e `300`, respectivamente.
Na seção `retry`, `pane_lost_attempts` define retentativas extras na mesma via quando um
pane desaparece ou encerra sem resultado (padrão `1`; use `0` para desativar). O
`pane_lost_backoff_seconds` define a espera inicial antes do retry (padrão `5`); ela dobra
a cada retry e é limitada a `60` segundos. Esses retries preservam o worktree e não
consomem as tentativas `workers.tier_order[].max_retries` nem escalam de via.

#### Classificar uma Tarefa (`classify`):
```bash
meister classify --context "Adicionar filtro de busca por data no painel"
```

Retorna JSON tipado com complexidade (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) e o implementador recomendado entre as vias configuradas em `tier_order`.

O portão aceita verificações `gate.commands` próprias e `scope.tolerated_files` para artefatos gerados;
veja `meister.config.example.yaml` para exemplos Go e Node/pnpm.

#### Avaliar e Controlar o Loop (`control`):
```bash
meister control --diff-summary "Adicionado input e testes no componente Filter.tsx" --test-result pass
```
Retorna a ação autorizada:
- `COMPLETE`: Pode comitar e concluir a resposta.
- `RETRY`: Tentar novamente.
- `switch_implementer`: Trocar para o próximo modelo da hierarquia.

### 3. Execução e Delegação de Workers (`worker`)

O MeisterRouter executa workers por harnesses locais declarados em `meister/default_config.yaml` ou sobrescritos em `meister.config.yaml`:

```bash
# Execução pela primeira via configurada:
meister worker --model copilot_luna --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"

# Se o modelo falhar ou estiver inativo, escale imediatamente:
meister worker --model codex_luna --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
```

> ⚠️ **DIRETIVA CRÍTICA: ZERO IMPLEMENTAÇÃO DIRETA PELO ORQUESTRADOR**
> Modelos arquitetos (Claude Sonnet 5, OpenAI Codex, GPT-4o) **NUNCA** devem escrever código de implementação diretamente quando um worker estiver configurado.
> Se o modelo primário recomendado falhar ou estiver inacessível, a regra estrita é a **decaída/escalonamento em cascata**:
> A sequência de fallback segue a ordem configurada em `workers.tier_order`; customize-a no arquivo do projeto.
> O arquiteto só pode implementar diretamente se **todos** os modelos da cadeia estiverem comprovadamente inacessíveis.

---

### 4. Orquestração Autônoma Multi-Agente (`orchestrate`)
Execute um ciclo de orquestração autônoma com decomposição em DAG e verificação por portão determinístico:
```bash
meister orchestrate --task "Refatorar camada de cache e cobrir com testes"
```
O comando mostra o progresso de cada tarefa e um resumo final em stderr; a frase de resultado
continua em stdout. Use `--quiet` (ou `-q`) para suprimir essas linhas de progresso e o resumo.
Para reaproveitar tarefas concluídas de um run anterior após editar o plano, use `--resume`
para selecionar automaticamente o run FAILED/RUNNING elegível mais recente do mesmo diretório,
ou informe seu identificador após a opção:
```bash
meister orchestrate --task '[{"id":"step-2","description":"...","target_files":["src/"],"depends_on":[]}]' --resume
meister orchestrate --task '[{"id":"step-2","description":"...","target_files":["src/"],"depends_on":[]}]' --resume 0123456789abcdef
```
Somente tarefas com descrição e dependências inalteradas, commit existente e escopo compatível
são retomadas. Sem `--resume`, a orquestração mantém o comportamento de executar o plano inteiro
e avisa quando há um run anterior que pode ser reaproveitado.

---

### 5. Painel de Telemetria ao Vivo (Dashboard)

#### Interface Web:
Acompanhe os custos, economia gerada e distribuição de modelos no seu navegador:
```bash
meister dashboard
```
Acesse em: **`http://localhost:5050`**

#### Interface Terminal (TUI Overlay):
Ideal para uso dentro do terminal ou integrado ao Herdr:
```bash
meister dashboard --tui
```

---

### 6. Consultar Catálogo de Modelos & Benchmarks
```bash
meister models
```
Exibe na hora as métricas oficiais do Artificial Analysis e a precificação vigente por 1M de tokens.

---

## 📁 Estrutura do Projeto

```
meisterrouter/
├── bin/
│   ├── meister                  # Executável CLI Python
│   ├── install.sh               # Script de bootstrap e instalação rápida em 1 linha
│   └── cli.js                   # Wrapper executável Node.js para NPM / NPX
├── meister/
│   ├── cli.py                   # Interface de linha de comando
│   ├── worker.py                # Execução nativa de workers com OpenRouter
│   ├── jev.py                   # Cliente TypeSafe Decisions API (OpenRouter)
│   ├── orchestrator.py          # Motor de orquestração multi-agente
│   ├── dag.py                   # Decomposição e resolução de DAG de tarefas
│   ├── gate.py                  # Portão de verificação determinística
│   ├── logger.py                # Gravação atômica de telemetria JSONL
│   ├── models.py                # Matriz de modelos e cálculo de custos
│   ├── hooks.py                 # Instalador de hooks Git e Claude
│   ├── herdr_bridge.py          # Ponte de comunicação e eventos com Herdr IPC
│   ├── tui.py                   # Dashboard em modo texto interativo para terminal
│   ├── templates/               # Templates injetáveis (CLAUDE.md, CODEX.md, hooks)
│   └── dashboard/               # Servidor Flask e interface Web
├── tests/                       # Suíte de testes unitários com pytest
├── herdr-plugin.toml            # Manifesto do plugin para Herdr (atalhos prefix+m, prefix+M)
├── package.json                 # Manifesto do pacote npm / npx
├── CLAUDE.md                    # Regras para Claude Code
├── CODEX.md                     # Regras para OpenAI Codex
├── AGENTS.md                    # Diretivas unificadas para agentes
├── ARCHITECTURE.md              # Documentação profunda da máquina de estados
└── pyproject.toml / setup.py    # Pacote Python instalável
```

---

### Adaptador GitHub Copilot CLI
> ℹ️ **STATUS: VERIFICADO (GitHub Copilot CLI 1.0.88)**
> O adaptador Copilot CLI (`copilot`) utiliza as flags oficiais `-p`, `--allow-all` e `--no-ask-user` para execução não-interativa autônoma.
> Sua presença e posição na cadeia são definidas por `meister/default_config.yaml` e podem ser alteradas em `meister.config.yaml`; variáveis de ambiente de seleção de modelos foram removidas.
