# 🔮 MeisterRouter

**Plugin do [Herdr](https://herdr.dev) que distribui as tarefas de um plano pelas suas assinaturas de CLIs de IA e coordena tudo de forma determinística.**

Você escolhe uma **LLM orquestradora** (por exemplo o Claude Code) para planejar. O MeisterRouter distribui as tarefas entre o **GitHub Copilot**, o **Codex**, o **Antigravity (Gemini)** e o **Claude**, cada uma num worktree próprio e numa aba visível do Herdr. Cada resultado passa por um **gate determinístico** (testes e lint) e só então é integrado; a `main` só avança por fast-forward no fim. O único componente não determinístico é o **Jev** (OpenRouter), que escolhe a via inicial de cada tarefa e julga o resultado.

> [!IMPORTANT]
> **O Herdr é pré-requisito.** O MeisterRouter roda como plugin dele (abas dos workers, popups e atalhos). Instale o Herdr antes: `curl -fsSL https://herdr.dev/install.sh | sh`.

📐 **Como funciona:** veja os [diagramas](docs/DIAGRAMAS.md) (componentes, sequência, roteamento, falhas, gates, estados e mais).

## 👀 Em funcionamento

**Linha do tempo** (`prefix+t` no Herdr): uma barra por tarefa e por fase (worker, gate, integração), a via de cada uma, a linha do Jev e o custo, ao vivo.

![Linha do tempo de um run com 4 tarefas, ao vivo](docs/img/timeline.gif)

**Dashboard** (`prefix+shift+m` no Herdr, ou `meister dashboard` em `http://localhost:5050`): métricas por run, custo, overhead e a tabela de tarefas.

![Dashboard web com as métricas e as tarefas de um run](docs/img/dashboard-web.png)

> As imagens usam um run **de demonstração** (dados sintéticos) gerado pelos próprios renderizadores do MeisterRouter; nenhum projeto real aparece nelas.

## 🚀 Instalação

**1. Herdr** (se ainda não tiver): `curl -fsSL https://herdr.dev/install.sh | sh`

**2. MeisterRouter** (o repositório é privado; precisa da [GitHub CLI](https://cli.github.com/) logada, `gh auth status`):
```bash
gh api -H "Accept: application/vnd.github.raw" repos/CristianonCarvalho/meisterrouter/contents/bin/install.sh | bash
```
O instalador faz o resto sozinho: instala o `meister`, liga o plugin ao Herdr, cadastra os atalhos (num bloco gerenciado do `config.toml`, com backup) e confere o ambiente, mostrando o que ainda falta (CLIs dos workers, chave do Jev). Pode rodar `meister setup` de novo quando quiser: ele não repete o que já está pronto (`--dry-run` mostra o que faria).

**3. A chave do Jev**, uma vez (só o Jev usa o OpenRouter; os workers rodam nas suas assinaturas):
```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.meister/.env
```
Sem chave, defina `router: {mode: first}` (veja [modelos e ordem](#-escolher-os-modelos-e-a-ordem)): a primeira via é escolhida sem rede.

**4. Cada projeto**, uma vez, dentro dele: `meister setup --project` (cria `CLAUDE.md`, `CODEX.md`, `AGENTS.md`, os hooks e o guard).

Outras formas de instalar (clone manual, npm, pip) estão em [Instalação alternativa e comandos avançados](docs/INSTALACAO_E_COMANDOS_AVANCADOS.md).

## 🛠️ Uso

**1. Planeje** com a sua LLM orquestradora (plano em Markdown no formato do `superpowers:writing-plans`) e converta:
```bash
meister plan import plano.md -o plano.json
```
**2. Execute:**
```bash
meister orchestrate --plan-file plano.json     # ou prefix+m no Herdr
```
Os workers abrem em abas do Herdr e você acompanha ao vivo. Se algo falhar, rode o mesmo comando: as tarefas já concluídas são puladas e o run continua de onde parou (o que fazer em cada falha está no [manual de execução](docs/MANUAL_DE_EXECUCAO.md)).

**3. Acompanhe** pelos atalhos do Herdr:

| Atalho | O que abre |
|---|---|
| `prefix+m` | Inicia a orquestração no workspace |
| `prefix+shift+m` | Dashboard em popup |
| `prefix+t` | Linha do tempo (Gantt) em popup |

## 🎛️ Escolher os modelos e a ordem

Cada **via** é uma assinatura (CLI) com um modelo. A **ordem** é a cadeia de fallback, sempre para a frente: se a primeira via falhar ou esgotar a cota, o Meister passa para a próxima. Veja o que está ativo:
```bash
meister models
```
```
  #  NOME                     HARNESS          MODELO                         CUSTO/1M  STATUS
  1  copilot_luna             copilot          gpt-6-luna                   $0.200  ligada
  2  agy_gemini_flash         agy              gemini-3.8-flash-high        $1.500  ligada
  3  claude_sonnet            claude           sonnet                       $4.000  ligada
  4  codex_luna               codex            gpt-6-luna                   $0.200  desligada
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
    - {name: copilot_luna, harness: copilot, model: gpt-6-luna, cost_per_m_tokens: 0.20, credit_usd: 0.01, max_retries: 2}
    - {name: codex_luna, harness: codex, model: gpt-6-luna, enabled: false, cost_per_m_tokens: 0.20, max_retries: 2}
    - {name: agy_gemini_flash, harness: agy, model: gemini-3.8-flash-high, cost_per_m_tokens: 1.50, max_retries: 2}
    - {name: claude_sonnet, harness: claude, model: sonnet, cost_per_m_tokens: 4.00, max_retries: 1, eligible_classes: [ESCALATE]}
```
(O `meister models` lista as vias desligadas por último; elas não entram na cadeia.)

| Para... | Faça |
|---|---|
| mudar a **ordem** | reordene as linhas de `tier_order` |
| **ligar ou desligar** uma via | `enabled: true` ou `enabled: false` |
| limitar a via a certas **classes** de tarefa (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) | `eligible_classes: [...]` (o Jev só manda para a via o que ela aceita) |
| ignorar o Jev e usar sempre a primeira via | `router.mode: first` |

Atenção: a lista `tier_order` do projeto **substitui** a padrão inteira. Depois de editar, confira com `meister config validate` e `meister models`. Os campos e os demais ajustes (timeouts, gate, escopo) estão em [configuração avançada](docs/INSTALACAO_E_COMANDOS_AVANCADOS.md#configuração-avançada).

## 📚 Mais documentação

- [Instalação alternativa e comandos avançados](docs/INSTALACAO_E_COMANDOS_AVANCADOS.md): outras formas de instalar, atalhos à mão, `init`/guard, `classify`, `control`, `worker`, `clean`, `--resume`, opções do dashboard e da linha do tempo, configuração avançada, testes e estrutura do projeto.
- [Diagramas](docs/DIAGRAMAS.md): componentes, sequência e fluxos.
- [Manual de execução](docs/MANUAL_DE_EXECUCAO.md): o que você faz em cada situação e como se recuperar de falhas.
- [Modelos e custos](docs/MODELOS_E_CUSTOS.md) e o [CHANGELOG](CHANGELOG.md). Versão instalada: `meister --version`.
