# Primeiros passos: instalação, primeira run e uso no dia a dia

O [README](../../README.pt-BR.md#-comece-agora) tem a versão curta: instalar, `meister setup --project` e pedir à sua LLM orquestradora o que você quer. Esta página tem os detalhes.

## Instalação

```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash
```

O instalador instala o `meister`, confere o seu ambiente e lista o que ainda falta (CLIs de worker, chave do Jev). Para fixar uma versão (tag) em vez da `main`: `... | bash -s -- --version v0.9.1` (ou `latest`). Você pode rodar o `meister setup` de novo quando quiser: ele não refaz o que já está feito (`--dry-run` mostra o que faria).

Outras formas de instalar (clone, npm, pip) estão em [Instalação alternativa e comandos avançados](INSTALACAO_E_COMANDOS_AVANCADOS.md). No Windows, use `pipx` ou `pip` (o instalador acima é bash): veja [Windows](INSTALACAO_E_COMANDOS_AVANCADOS.md#windows).

**A chave do Jev** (opcional, uma vez; só o Jev usa o OpenRouter, os workers rodam nas suas próprias assinaturas):

```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.meister/.env
```

**Cada projeto**, uma vez, de dentro dele: `meister setup --project` (cria `CLAUDE.md`, `CODEX.md`, `AGENTS.md`, os hooks e o guard).

## Atualização

Como atualizar depende de como você instalou. Confira o resultado com `meister --version` (o commit mostrado é o código em uso).

| Instalado com | Atualize com |
|---|---|
| O instalador `curl ... install.sh` | Rode o mesmo comando de novo. Ele atualiza o clone em `~/.local/share/meisterrouter` (acrescente `--version latest` ou uma tag para escolher a versão). |
| Um clone com `pip install -e .` ou `./bin/install.sh` | `git pull` dentro do clone. A instalação é editável, então não há nada a reinstalar (rode `pip install -e .` de novo só se o `pyproject.toml` mudou as dependências). |
| `pipx` (Windows) | `pipx install --force git+https://github.com/CristianonCarvalho/meisterrouter.git` |
| `pip` | `pip install --upgrade git+https://github.com/CristianonCarvalho/meisterrouter.git` |
| `npm` | `npm install -g git+https://github.com/CristianonCarvalho/meisterrouter.git` |

Depois de atualizar:

- **Em cada projeto**, rode `meister setup --project` de novo. Ele não refaz o que já está feito e atualiza o hook de pre-commit gerenciado. Um hook que você editou à mão é mantido; `meister init --hooks --force` o substitui.
- **Uma run em andamento** continua com o código que carregou ao começar. A versão nova vale a partir da próxima run.
- O [CHANGELOG](../../CHANGELOG.pt-BR.md) lista o que mudou em cada versão.

## Como usar

Depois do `meister setup --project`, o projeto tem as regras e os hooks que **ensinam a sua LLM orquestradora a usar o Meister**. Você não digita `meister plan` nem `meister orchestrate`:

1. **Abra a sua LLM orquestradora** (Claude Code ou Codex) no projeto.
2. **Peça em linguagem natural**, por exemplo: *"quero um filtro por data na tela de pedidos"*. Ela conversa com você, desenha a solução e escreve o plano: lê as regras com `meister plan guide` e parte do `meister plan new` (ou, se você usa a skill Superpowers, do plano da skill).
3. **Ela faz o resto**: importa e valida o plano, e então o executa com o Meister. O `CLAUDE.md` e o hook de prompt (`UserPromptSubmit`) dizem que o método de execução é sempre o Meister, e o **guard** (`PreToolUse`) recusa qualquer edição direta de código, então ela delega.
4. **Você acompanha** pela timeline e pelo dashboard:

   ```bash
   meister timeline      # Gantt ao vivo de tarefas e fases
   meister dashboard     # métricas, custo e tarefas em http://localhost:5050
   meister logs -f       # acompanha o log da run
   meister wait --run-id <id>   # bloqueia até a run terminar (o código de saída diz como foi)
   ```

5. **Ela revisa as evidências** (testes, diff) e conclui. Commit, push e merge continuam dependendo da sua aprovação.

Se o Meister estiver indisponível, a LLM pergunta antes de implementar por outro meio. Se uma run falhar, peça para rodar de novo: as tarefas já concluídas são puladas (o que fazer em cada falha está no [manual de execução](MANUAL_DE_EXECUCAO.md)).

## Rodando os comandos você mesmo

Quer revisar ou escrever um plano à mão, ou rodar os comandos você mesmo? O fluxo nativo é:

```bash
meister plan guide                         # as regras do formato
meister plan new -o plano.md               # um plano vazio com N tarefas (--tasks N)
meister plan import plano.md -o plano.json
meister plan validate plano.json
meister orchestrate --plan-file plano.json
```

Veja o [formato do plano](FORMATO_DO_PLANO.md) e os [comandos avançados](INSTALACAO_E_COMANDOS_AVANCADOS.md).
