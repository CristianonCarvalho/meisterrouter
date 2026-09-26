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

Escolha a forma que melhor se adapta ao seu fluxo de trabalho:

### Opção 1: Script Automático (Recomendado — 1 comando)
Instala automaticamente o ambiente, cria o symlink global `meister` e vincula o plugin ao Herdr se instalado:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash
```
> *Ou dentro de um clone local do repositório:*
```bash
./bin/install.sh
```

---

### Opção 2: Via NPX ou NPM (Ecossistema Node.js)
Você pode executar diretamente via **NPX** sem instalar nada previamente:
```bash
npx CristianonCarvalho/meisterrouter init --target .
```
Ou instalar globalmente via **NPM**:
```bash
npm install -g CristianonCarvalho/meisterrouter
meister --help
```
*(O runner Node.js gerencia o bootstrap do runtime Python de forma transparente)*.

---

### Opção 3: Como Plugin Oficial do Herdr
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

### Opção 4: Instalação Manual com Python (Pip / Venv)
```bash
git clone https://github.com/CristianonCarvalho/meisterrouter.git
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

---

### 3. Orquestração Autônoma Multi-Agente
Execute um ciclo de orquestração autônoma com decomposição em DAG e verificação por portão determinístico:
```bash
meister orchestrate --task "Refatorar camada de cache e cobrir com testes"
```

---

### 4. Painel de Telemetria ao Vivo (Dashboard)

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

### 5. Consultar Catálogo de Modelos & Benchmarks
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
