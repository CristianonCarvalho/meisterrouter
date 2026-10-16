# Formato do plano

O MeisterRouter executa um **plano**: uma lista de tarefas, cada uma com os arquivos que pode alterar e
(opcionalmente) as tarefas de que depende. No uso direto você não escreve nem converte o plano à mão: a sua
LLM orquestradora (por exemplo o Claude Code) o gera, importa e executa seguindo as regras que o
`meister setup --project` instala (`CLAUDE.md`, hooks e guard). Esta página explica o formato, para você
revisar um plano ou escrever um do zero.

## O fluxo

O MeisterRouter tem comandos próprios para escrever um plano; nenhum plugin é necessário:

```bash
meister plan guide                 # as regras do formato (curto, em en ou pt-BR)
meister plan new -o plano.md       # moldura pronta, com N tarefas vazias
   (preencha as tarefas)
meister plan import plano.md -o plano.json
meister plan validate plano.json
meister plan analyze plano.json
meister orchestrate --plan-file plano.json
```

### `meister plan guide`

Imprime o guia completo do formato: o fluxo acima, como dividir o trabalho em muitas tarefas pequenas,
`**Files:**` como escopo, `**Depends on:**`, paralelismo, o que não fazer e exemplos em Markdown e JSON.

| Opção | O que faz |
|---|---|
| `--lang en\|pt-BR` | Idioma do guia (padrão: o idioma configurado). |

### `meister plan new`

Imprime (ou grava) um plano vazio: o título, `## Global Constraints` e N blocos `### Task K:` com
`**Files:**` e, a partir da segunda tarefa, `**Depends on:**`. Uma linha de comentário aponta para
`meister plan guide`; o importador a ignora. A moldura passa por `plan import` e `plan validate` sem edição.

| Opção | O que faz |
|---|---|
| `--tasks N` | Número de tarefas (padrão 3). |
| `--format markdown\|json` | `markdown` (padrão) ou o array JSON canônico descrito no fim da página. |
| `-o ARQUIVO` | Grava em `ARQUIVO`; sem ele, imprime no terminal. |
| `--project` | Preenche `## Global Constraints` com regras lidas do `CLAUDE.md` e do `AGENTS.md` do diretório atual, se existirem (nunca inventa regras). |

## O plano em Markdown

```markdown
# Filtro de busca por data

## Global Constraints

- Python 3.10, sem dependências novas.

### Task 1: Função de filtro

**Files:**
- Create: `src/filtro.py`
- Test: `tests/test_filtro.py`

Implemente `filtrar_por_data(itens, inicio, fim)`. Escreva o teste primeiro.

### Task 2: Ligar o filtro à tela

**Files:**
- Modify: `src/tela.py:40-80`

**Depends on:** Task 1

Chame `filtrar_por_data` ao clicar em "Buscar".

### Task 3: Documentar

**Files:**
- Modify: `docs/*.md`

Atualize a documentação.
```

| Elemento | Regra |
|---|---|
| `### Task N: título` | Uma tarefa por cabeçalho, numerada. Vira o id `task_N`. |
| `**Files:**` | Lista dos arquivos que a tarefa pode tocar, um por linha: `- Create: \`caminho\``, `- Modify: \`caminho\`` ou `- Test: \`caminho\``; `Rewrite` vale como `Modify`, `Delete` é aceito, e vários caminhos entre crases na mesma linha valem todos (o texto depois deles é ignorado). Aceita sufixo de linhas (`src/tela.py:40-80`, ignorado) e glob (`drizzle/*`, `docs/*.md`). O **gate de escopo reprova** qualquer mudança fora dessa lista. |
| `**Depends on:**` | Opcional: `Task 1` ou `Task 2, Task 5`. A tarefa só começa depois das indicadas. |
| `## Global Constraints` | Opcional: regras do projeto. O Meister as **resume** para cada tarefa (não repete o plano inteiro). |
| Corpo da tarefa | O que o worker deve fazer, com os critérios de aceite. Quanto mais específico, melhor o resultado. |

### Dependências e paralelismo

Por padrão (`--deps sequential`) cada tarefa depende da anterior: é o mais seguro, mas serial. Com
`--deps files` só há dependência quando duas tarefas compartilham arquivos ou quando você escreve
`**Depends on:**`; as independentes rodam **em paralelo**, cada uma no seu worktree. No exemplo acima, a
Tarefa 3 só espera a 2 no modo sequencial; no modo `files` ela roda junto com as outras.
Tarefas sem `**Files:**` só entram com `--allow-unscoped` e rodam isoladas, sem paralelismo.

### Dicas para um bom plano

- **Tarefas pequenas**, cada uma com um resultado testável (um arquivo de código e o seu teste).
- **Declare todos os arquivos**, inclusive os gerados (lockfiles, `drizzle/*`) ou acrescente-os em
  `scope.tolerated_files`.
- **Inclua os testes existentes que sua mudança afeta de propósito.** Se a tarefa muda um comportamento que um teste existente verifica, acrescente esse arquivo de teste em `**Files:**`. Senão, o gate reprova a mudança como `scope_conflict`.
- **Evite sobreposição**: duas tarefas que mexem no mesmo arquivo viram sequenciais.
- Comandos que geram arquivos (`npm install`, `prisma generate`) fazem o importador avisar se o arquivo
  gerado não está coberto por `Files:` ou pelos arquivos tolerados.

## O JSON canônico

O `meister plan import` converte o Markdown em JSON; o `orchestrate` aceita esse JSON diretamente. Cada
tarefa tem `id`, `description` (o texto que o worker recebe), `target_files` e `depends_on`:

```json
[
  {
    "id": "task_1",
    "description": "Task 1: Função de filtro\n\n...",
    "target_files": ["src/filtro.py", "tests/test_filtro.py"],
    "depends_on": []
  },
  {
    "id": "task_2",
    "description": "Task 2: Ligar o filtro à tela\n\n...",
    "target_files": ["src/tela.py"],
    "depends_on": ["task_1"]
  }
]
```

## Superpowers (opcional)

O formato é o mesmo do plano gerado pela skill **`writing-plans`** do plugin
[Superpowers](https://github.com/obra/superpowers) para o Claude Code. Se você usa a skill, o plano dela
entra direto no `meister plan import` (`--format superpowers` continua aceito e é o mesmo formato).

```text
/plugin marketplace add obra/superpowers-marketplace
/plugin install superpowers@superpowers-marketplace
```

## Comandos (para quem quiser rodar à mão)

```bash
meister plan guide                                    # as regras do formato
meister plan new --tasks 5 -o plano.md                # moldura de plano vazia
meister plan import plano.md -o plano.json            # Markdown -> JSON (mostra a tabela de tarefas)
meister plan import plano.md --deps files -o plano.json
meister plan validate plano.json                      # confere esquema, dependências e chaves proibidas
meister plan analyze plano.json                       # paralelismo previsto e avisos de plano serial
meister orchestrate --plan-file plano.json            # executa
```
O `plan import` detecta o formato sozinho (`--format auto`, o padrão): plano em Markdown ou JSON canônico.
`--format markdown` e `--format superpowers` são nomes do mesmo formato nativo.
Mais opções (`--resume`, `--quiet`, `--allow-unscoped`) em
[Instalação alternativa e comandos avançados](INSTALACAO_E_COMANDOS_AVANCADOS.md).
