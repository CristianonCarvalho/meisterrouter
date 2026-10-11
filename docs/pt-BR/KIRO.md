# Kiro CLI como harness

🌐 [English](../kiro.md) · **Português (Brasil)**

O Meister aceita o [Kiro CLI](https://kiro.dev) como harness de worker. Em `workers.tier_order` o harness se chama `kiro-cli` (alias `kiro`). Cada worker roda:

```bash
kiro-cli chat --no-interactive --trust-all-tools [--model M] [--effort E] "<prompt>"
```

Conferido no kiro-cli 2.29.1: roda sem interação, cria e edita arquivos no diretório atual (o worktree da tarefa), imprime a resposta final no stdout, os logs das ferramentas (`[tool] Creating x`, `[tool] status: Completed`) no stderr e sai com `0`. Um modelo inexistente sai com `1` e `Error: Internal error ... The model ... is not available` no stderr.

## Usar só o Kiro

Não precisa de Copilot, Claude, Codex nem OpenRouter. Use `router.mode: first` (sem Jev, sem rede) e liste só vias do Kiro. A `tier_order` do projeto **substitui** a lista padrão inteira, então os outros harnesses saem:

```yaml
router:
  mode: first              # sempre começa pela primeira via; não precisa da chave do OpenRouter
workers:
  tier_order:              # a ordem é a cadeia de fallback
    - {name: tier_1, harness: kiro-cli, model: auto, max_retries: 2}
    - {name: tier_1b, harness: kiro-cli, model: claude-haiku-4.5, max_retries: 2}
    - {name: tier_2, harness: kiro-cli, model: claude-sonnet-4.5, max_retries: 1}
gate:
  commands:                # opcional: sem ele, o gate detecta os testes do projeto
    - name: tests
      run: "{python} -m pytest -q"
      timeout_seconds: 600
      required: true
```

Sem `gate.commands`, o gate usa a detecção automática (por exemplo pytest, `package.json` ou `pom.xml`). Confira com `meister config validate` e `meister models`. Os demais ajustes do gate estão em [configuração avançada](INSTALACAO_E_COMANDOS_AVANCADOS.md#configuração-avançada).

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
