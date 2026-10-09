# 🔮 MeisterRouter

🌐 [English](README.md) · **Português (Brasil)**

**Plugin do [Herdr](https://herdr.dev) que distribui as tarefas de um plano pelas suas assinaturas de CLIs de IA e coordena tudo de forma determinística.**

Você escolhe uma **LLM orquestradora** (por exemplo o Claude Code) para planejar. O MeisterRouter distribui as tarefas entre o **GitHub Copilot**, o **Codex**, o **Antigravity (Gemini)** e o **Claude**, cada uma num worktree próprio e numa aba visível do Herdr. Cada resultado passa por um **gate determinístico** (testes e lint) e só então é integrado; a `main` só avança por fast-forward no fim. O único componente não determinístico é o **Jev** (OpenRouter), que escolhe a via inicial de cada tarefa e julga o resultado.

> [!IMPORTANT]
> **O Herdr é pré-requisito.** O MeisterRouter roda como plugin dele (abas dos workers, popups e atalhos). Instale o Herdr antes: `curl -fsSL https://herdr.dev/install.sh | sh`.

📐 **Como funciona:** veja os [diagramas](docs/pt-BR/DIAGRAMAS.md) (componentes, sequência, roteamento, falhas, gates, estados e mais).

## 👀 Em funcionamento

**Linha do tempo** (`prefix+t` no Herdr): uma barra por tarefa e por fase (worker, gate, integração), a via de cada uma, a linha do Jev e o custo, ao vivo.

![Linha do tempo de um run com 4 tarefas, ao vivo](docs/img/timeline.gif)

**Dashboard** (`prefix+shift+m` no Herdr, ou `meister dashboard` em `http://localhost:5050`): métricas por run, custo, overhead e a tabela de tarefas.

![Dashboard web com as métricas e as tarefas de um run](docs/img/dashboard-web.png)

> As imagens usam um run **de demonstração** (dados sintéticos) gerado pelos próprios renderizadores do MeisterRouter; nenhum projeto real aparece nelas.

## ✅ Pré-requisitos

| | O quê | Para quê |
|---|---|---|
| **Obrigatório** | macOS ou Linux. Windows não é suportado (o `meister` usa travas de arquivo POSIX). | Onde o MeisterRouter roda. |
| **Obrigatório** | [Herdr](https://herdr.dev) | O MeisterRouter roda como plugin dele (abas dos workers, popups, atalhos). `curl -fsSL https://herdr.dev/install.sh \| sh` |
| **Obrigatório** | Pelo menos uma CLI de IA com assinatura: `copilot`, `codex`, `agy` (Antigravity/Gemini) ou `claude` | São os workers. O `meister setup` mostra quais ele encontrou. |
| **Recomendado** | Uma LLM orquestradora com planejamento, por exemplo o **Claude Code** com o plugin [Superpowers](https://github.com/obra/superpowers) | Ela conversa com você, escreve o plano no [formato do plano](docs/pt-BR/FORMATO_DO_PLANO.md) e dispara o Meister. O Superpowers é opcional: sem ele, o plano só precisa estar nesse formato. |
| **Opcional** | Chave do OpenRouter | Só o **Jev** usa (escolhe a via de cada tarefa). Sem ela, use `router: {mode: first}`. |

## 🚀 Instalação

```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash
```
O instalador faz tudo sozinho: instala o `meister`, liga o plugin ao Herdr, cadastra os atalhos (num bloco gerenciado do `config.toml`, com backup) e confere o ambiente, mostrando o que ainda falta (CLIs dos workers, chave do Jev). Ele aborta, com a instrução de instalação, se o Herdr não estiver instalado. Para fixar uma versão (tag) em vez da `main`: `... | bash -s -- --version v0.9.1` (ou `latest`). Pode rodar `meister setup` de novo quando quiser: não repete o que já está pronto (`--dry-run` mostra o que faria).

**A chave do Jev** (opcional, uma vez; só o Jev usa o OpenRouter, os workers rodam nas suas assinaturas):
```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.meister/.env
```
**Cada projeto**, uma vez, dentro dele: `meister setup --project` (cria `CLAUDE.md`, `CODEX.md`, `AGENTS.md`, os hooks e o guard).

Outras formas de instalar (clone, npm, pip) e a versão fixa estão em [Instalação alternativa e comandos avançados](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md).

## 🛠️ Como usar (direto, sem digitar comandos)

Depois do `meister setup --project`, o projeto passa a ter as regras e os hooks que **ensinam a sua LLM orquestradora a usar o Meister**. Você não digita `meister plan` nem `meister orchestrate`:

1. **Abra a sua LLM orquestradora** (Claude Code ou Codex) no projeto, dentro do Herdr.
2. **Peça em linguagem natural**, por exemplo: *"quero um filtro de busca por data na tela de pedidos"*. Com o Superpowers, ela conversa com você, desenha a solução e escreve o plano.
3. **Ela faz o resto**: importa e valida o plano, e executa com o Meister. O `CLAUDE.md` e o hook de prompt (`UserPromptSubmit`) dizem a ela que o método de execução é sempre o Meister, e o **guard** (`PreToolUse`) recusa qualquer edição direta de código, então ela delega.
4. **Você acompanha** no Herdr: cada worker abre numa aba visível, e os atalhos mostram o resto:

| Atalho | O que abre |
|---|---|
| `prefix+m` | Inicia a orquestração no workspace |
| `prefix+shift+m` | Dashboard em popup |
| `prefix+t` | Linha do tempo (Gantt) em popup |

5. **Ela revisa as evidências** (testes, diff) e conclui. Commit, push e merge continuam dependendo do seu OK.

Se o Meister não estiver disponível (daemon fora do ar, sem Herdr), a LLM pergunta antes de implementar por outro meio. Se um run falhar, peça para ela rodar de novo: as tarefas já concluídas são puladas (o que fazer em cada falha está no [manual de execução](docs/pt-BR/MANUAL_DE_EXECUCAO.md)).

Quer conferir ou escrever um plano à mão, ou rodar os comandos você mesmo? Veja o [formato do plano](docs/pt-BR/FORMATO_DO_PLANO.md) e os [comandos avançados](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md).

## 🎛️ Escolher os modelos e a ordem

Cada **via** é uma assinatura (CLI) com um modelo. A **ordem** é a cadeia de fallback, sempre para a frente: se a primeira via falhar ou esgotar a cota, o Meister passa para a próxima. Veja o que está ativo:
```bash
meister models
```
```
  #  NOME                     HARNESS          MODELO                       ESFORÇO      CUSTO/1M  STATUS
  1  tier_1                   copilot          claude-haiku-5.5             -              $0.200  ligada
  2  tier_1b                  copilot          gpt-6-luna                   -              $0.200  ligada
  3  tier_2                   agy              gemini-3.8-flash-high        -              $1.500  ligada
  4  tier_3                   claude           sonnet                       -              $4.000  ligada
  5  tier_1c                  codex            gpt-6-luna                   -              $0.200  desligada
  6  tier_3b                  claude           opus                         -              $8.000  desligada
```
Para mudar, crie o arquivo do projeto com as vias padrão e edite:
```bash
meister config init        # grava meister.config.yaml (vias, ordem e comentários)
```
```yaml
router:
  mode: jev                # jev escolhe a via inicial de cada tarefa; first = sempre a primeira (sem rede)
workers:
  tier_order:              # a ORDEM destas linhas é a cadeia de fallback
    - {name: tier_1, harness: copilot, model: claude-haiku-5.5, cost_per_m_tokens: 0.20, credit_usd: 0.01, max_retries: 2}
    - {name: tier_1b, harness: copilot, model: gpt-6-luna, cost_per_m_tokens: 0.20, credit_usd: 0.01, max_retries: 2}
    - {name: tier_1c, harness: codex, model: gpt-6-luna, enabled: false, cost_per_m_tokens: 0.20, max_retries: 2}
    - {name: tier_2, harness: agy, model: gemini-3.8-flash-high, cost_per_m_tokens: 1.50, max_retries: 2}
    - {name: tier_3, harness: claude, model: sonnet, effort: high, cost_per_m_tokens: 4.00, max_retries: 1, eligible_classes: [ESCALATE]}
    - {name: tier_3b, harness: claude, model: opus, enabled: false, cost_per_m_tokens: 8.00, max_retries: 1, eligible_classes: [ESCALATE]}
```
(O `meister models` lista as vias desligadas por último; elas não entram na cadeia.) Os rótulos da tabela acima estão em português porque `language: pt-BR`; com o padrão `language: en` aparecem `enabled`/`disabled`, `COST/1M` e `EFFORT`.

| Para... | Faça |
|---|---|
| mudar a **ordem** | reordene as linhas de `tier_order` |
| **ligar ou desligar** uma via | `enabled: true` ou `enabled: false` |
| limitar a via a certas **classes** de tarefa (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) | `eligible_classes: [...]` (o Jev só manda para a via o que ela aceita) |
| ignorar o Jev e usar sempre a primeira via | `router.mode: first` |
| ligar ou desligar uma via pela linha de comando | `meister models --enable tier_1c` ou `meister models --disable tier_1b` |
| definir o **esforço de raciocínio** de uma via | `effort: high` (os valores dependem do harness, abaixo) |

Atenção: a lista `tier_order` do projeto **substitui** a padrão inteira. Depois de editar, confira com `meister config validate` e `meister models`. Os campos e os demais ajustes (timeouts, gate, escopo) estão em [configuração avançada](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md#configuração-avançada).

Valores de effort por harness:

| Harness | Valores de `effort` (opcional; sem ele vale o padrão do harness) |
|---|---|
| `copilot` | none, minimal, low, medium, high, xhigh, max |
| `claude`, `agy` | low, medium, high, xhigh, max |
| `codex` | minimal, low, medium, high, xhigh |

## 📚 Mais documentação

- [Formato do plano](docs/pt-BR/FORMATO_DO_PLANO.md): como escrever ou revisar um plano (Superpowers é recomendado, não obrigatório), dependências e paralelismo.
- [Instalação alternativa e comandos avançados](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md): outras formas de instalar, atalhos à mão, `plan`/`orchestrate` à mão, `init`/guard, `classify`, `control`, `worker`, `clean`, `--resume`, opções do dashboard e da linha do tempo, configuração avançada, testes e estrutura do projeto.
- [Diagramas](docs/pt-BR/DIAGRAMAS.md): componentes, sequência e fluxos.
- [Manual de execução](docs/pt-BR/MANUAL_DE_EXECUCAO.md): o que você faz em cada situação e como se recuperar de falhas.
- [Modelos e custos](docs/pt-BR/MODELOS_E_CUSTOS.md) e o [CHANGELOG](CHANGELOG.pt-BR.md). Versão instalada: `meister --version`.
