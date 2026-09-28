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
Isso criará automaticamente no projeto:
- `CLAUDE.md` (instruções completas para Claude Code)
- `CODEX.md` (instruções completas para OpenAI Codex)
- `AGENTS.md` (diretivas unificadas para Cursor, Copilot e subagentes)
- Hook Git `pre-commit` com verificação determinística e portão do Jev
- Diretório de telemetria `.meister/logs/`

---

### 2. Comandos do Jev Decisions

#### Classificar uma Tarefa (`classify`):
```bash
meister classify --context "Adicionar filtro de busca por data no painel"
```
Retorna JSON tipado com complexidade (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) e o implementador recomendado (`luna`, `haiku`, `gemini_flash`, `sonnet`).

#### Avaliar e Controlar o Loop (`control`):
```bash
meister control --diff-summary "Adicionado input e testes no componente Filter.tsx" --test-result pass
```
Retorna a ação autorizada:
- `COMPLETE`: Pode comitar e concluir a resposta.
- `RETRY`: Tentar novamente.
- `switch_implementer`: Trocar para o próximo modelo da hierarquia.

### 3. Execução e Delegação de Workers (`worker`)

O MeisterRouter inclui um runner nativo de execução que despacha tarefas diretamente para os modelos via OpenRouter, injeta o contexto dos arquivos do repositório e aplica edições atômicas no disco de forma determinística:

```bash
# Execução direta com modelo primário de baixo custo (GPT-6 Luna):
meister worker --model luna --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"

# Se o modelo falhar ou estiver inativo, escale imediatamente:
meister worker --model gemini_flash --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
# ou
meister worker --model haiku --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
```

> ⚠️ **DIRETIVA CRÍTICA: ZERO IMPLEMENTAÇÃO DIRETA PELO ORQUESTRADOR**
> Modelos arquitetos (Claude Sonnet 5, OpenAI Codex, GPT-4o) **NUNCA** devem escrever código de implementação diretamente quando um worker estiver configurado.
> Se o modelo primário recomendado falhar ou estiver inacessível, a regra estrita é a **decaída/escalonamento em cascata**:
> $$\text{Luna (\$0.077/M)} \longrightarrow \text{Gemini 3.8 Flash (\$0.577/M)} \longrightarrow \text{Claude 4.5 Haiku (\$0.77/M)} \longrightarrow \text{Sonnet/Opus}$$
> O arquiteto só pode implementar diretamente se **todos** os modelos da cadeia estiverem comprovadamente inacessíveis.

---

### 4. Orquestração Autônoma Multi-Agente (`orchestrate`)
Execute um ciclo de orquestração autônoma com decomposição em DAG e verificação por portão determinístico:
```bash
meister orchestrate --task "Refatorar camada de cache e cobrir com testes"
```

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

### Adaptador GitHub Copilot CLI (Status: NÃO VERIFICADO)
> ⚠️ **AVISO: EXECUÇÃO EXPERIMENTAL E NÃO VERIFICADA**
> As flags `-p`, `--allow-all` e `--no-ask-user` para o adaptador Copilot CLI (`copilot`) **não foram verificadas** em ambiente de produção devido à ausência do executável Copilot CLI instalado no ambiente de desenvolvimento local.
> Por este motivo, o Copilot **NÃO compõe a lista de tiers padrão** de workers do MeisterRouter. Ele pode ser habilitado de forma opt-in definindo `MEISTER_ENABLE_COPILOT=true` no ambiente ou configurando explicitamente via `meister.config.yaml`.
