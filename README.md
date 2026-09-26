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

### 1. Clonar ou Acessar o Repositório
```bash
cd /Users/cristianocarvalho/Documents/meisterrouter
```

### 2. Configurar o Ambiente Virtual
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 3. Configurar a Chave da API
Copie `.env.example` para `.env` e configure sua chave do OpenRouter:
```bash
cp .env.example .env
# Adicione sua OPENROUTER_API_KEY
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

### 3. Painel de Telemetria ao Vivo (Dashboard)
Acompanhe os custos, economia gerada e distribuição de modelos em tempo real:
```bash
meister dashboard
```
Acesse no seu navegador: **`http://localhost:5050`**

---

### 4. Consultar Catálogo de Modelos & Benchmarks
```bash
meister models
```
Exibe na hora as métricas oficiais do Artificial Analysis e a precificação vigente por 1M de tokens.

---

## 📁 Estrutura do Projeto

```
meisterrouter/
├── bin/
│   └── meister                  # Executável CLI
├── meister/
│   ├── cli.py                   # Interface de linha de comando
│   ├── jev.py                   # Cliente TypeSafe Decisions API (OpenRouter)
│   ├── logger.py                # Gravação atômica de telemetria JSONL
│   ├── models.py                # Matriz de modelos e cálculo de custos
│   ├── hooks.py                 # Instalador de hooks Git e Claude
│   ├── templates/               # Templates injetáveis (CLAUDE.md, CODEX.md, hooks)
│   └── dashboard/               # Servidor Flask e interface Web
│       ├── server.py
│       └── templates/index.html
├── tests/                       # Suíte de testes unitários com pytest
├── CLAUDE.md                    # Regras para Claude Code
├── CODEX.md                     # Regras para OpenAI Codex
├── AGENTS.md                    # Diretivas unificadas para agentes
├── ARCHITECTURE.md              # Documentação profunda da máquina de estados
└── pyproject.toml / setup.py    # Pacote Python instalável
```
