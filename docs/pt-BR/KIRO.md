# Kiro CLI como harness

🌐 [English](../kiro.md) · **Português (Brasil)**

O Meister aceita o [Kiro CLI](https://kiro.dev) como harness de worker. Em `workers.tier_order` o harness se chama `kiro-cli` (alias `kiro`). Cada worker roda:

```bash
kiro-cli chat --no-interactive --trust-all-tools [--model M] [--effort E] "<prompt>"
```

Conferido no kiro-cli 2.29.1: roda sem interação, cria e edita arquivos no diretório atual (o worktree da tarefa), imprime a resposta final no stdout, os logs das ferramentas (`[tool] Creating x`, `[tool] status: Completed`) no stderr e sai com `0`. Um modelo inexistente sai com `1` e `Error: Internal error ... The model ... is not available` no stderr.

## Usar só o Kiro

Não precisa de Copilot, Claude, Codex nem OpenRouter. Na raiz do projeto, gere a configuração pronta:

```bash
meister config init --kiro        # grava ./meister.config.yaml (use --path para outro lugar e --force para sobrescrever)
meister config validate
meister models
```

O arquivo usa `router.mode: first` (sem Jev, sem rede, sem chave do OpenRouter) e três vias do Kiro, que formam a cadeia de fallback:

| Via | Modelo | Tentativas |
|---|---|---|
| `tier_1` | `auto` | 2 |
| `tier_1b` | `claude-haiku-4.5` | 2 |
| `tier_2` | `claude-sonnet-4.5` | 1 |

A `tier_order` do projeto **substitui** a lista padrão inteira, então os outros harnesses saem. Para trocar os modelos, edite o arquivo; `kiro-cli chat --list-models` mostra os que a sua conta pode usar.

Sem `gate.commands`, o gate usa a detecção automática: `pom.xml` (Maven), `package.json`, `Cargo.toml`, ou pytest quando existe `pytest.ini`, `conftest.py`, `setup.cfg` com `[tool:pytest]` ou `pyproject.toml`. Se o projeto não tem nenhum desses sinais, o gate reprova com "No tests or linters detected" e o worker pode tentar editar o `meister.config.yaml`, que é protegido e barra a tarefa. Nesse caso declare você mesmo o bloco `gate.commands` (o arquivo gerado traz um exemplo comentado). Os demais ajustes do gate estão em [configuração avançada](INSTALACAO_E_COMANDOS_AVANCADOS.md#configuração-avançada).

Verificado com o `kiro-cli` 2.29.1: `orchestrate` com esse arquivo concluiu uma tarefa em 14 s e o gate passou.

## Modelos e custo

Os modelos válidos incluem `auto` (o padrão), `claude-sonnet-4.5`, `claude-haiku-4.5`, `deepseek-3.2`, `glm-5` e `qwen3-coder-next`. Para listar os modelos que a sua conta pode usar:

```bash
kiro-cli chat --list-models
```

O Kiro cobra em créditos, com um multiplicador por modelo:

| Modelo | Créditos |
|---|---|
| `auto` | 1,00x |
| `claude-sonnet-4.5` | 1,30x |
| `claude-haiku-4.5` | 0,40x |
| `qwen3-coder-next` | 0,05x |

## Esforço

O Meister aceita `effort` de `low` a `max` (`low`, `medium`, `high`, `xhigh`, `max`) numa via do Kiro e o repassa como `--effort`. Quando o modelo não suporta esforço, o Kiro só avisa no stderr e segue sem ele.

## Limitações

- **Sem fallback por cota.** A mensagem de cota esgotada do Kiro nunca foi observada, então o fallback automático por cota não dispara para o Kiro. Erros com código de saída diferente de 0 seguem a retentativa e o fallback normais (`retry.worker_error_attempts` e depois a próxima via).
- **Sem uso nos relatórios.** O uso e os créditos do Kiro não aparecem nos relatórios de uso (`meister report`, dashboard).

## Windows

O binário é `kiro-cli.exe`. Não verificado no Windows.
