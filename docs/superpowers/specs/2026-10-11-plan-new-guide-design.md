# Planos sem depender do superpowers: `meister plan new` e `meister plan guide`

**Status:** implementado (aprovado pelo dono em 2026-10-11, decisões 1, 2 e 3 abaixo). Ficou de fora, como previsto na seção 4: a validação semântica do conteúdo das tarefas (o `plan validate` confere só a estrutura: ids, arquivos, dependências e chaves).
**Origem:** item do `BACKLOG.md` "Tornar o superpowers opcional". O Meister não precisa do plugin instalado; o que o amarra é o **formato** de plano da skill e o texto que a apresenta como o caminho (`plan import --format superpowers` por padrão, modelos do `init`, README e `CLAUDE.md`).
**Decisões do dono:** (1) o modelo de plano é em **Markdown** (JSON só como opção); (2) as regras ficam num comando à parte, `meister plan guide`, que o `CLAUDE.md` do projeto manda ler; (3) `plan new` vira o **primeiro passo** nos modelos do `init`.

## 1. Fluxo
```
meister plan guide                 # o LLM lê as regras do formato (curto, em en ou pt-BR)
meister plan new -o plano.md       # moldura pronta, com N tarefas vazias
   (o LLM preenche)
meister plan import plano.md -o plano.json
meister plan validate plano.json   # já existe
meister plan analyze plano.json    # já existe
meister orchestrate --plan-file plano.json
```

## 2. Comandos
- `meister plan new [--tasks N] [--format markdown|json] [-o FILE] [--project]`: `--tasks` padrão 3; sem `-o`, imprime no terminal. O Markdown tem o título, `## Global Constraints`, N blocos `### Task K:` com `**Files:**` e `**Depends on:**` (a partir da 2) e uma linha de comentário HTML apontando para `meister plan guide`; o importador ignora o comentário. `--format json` escreve o array canônico (`id`, `description`, `target_files`, `depends_on`, sem chaves extras, porque o validador as recusa). `--project` preenche as Global Constraints com regras lidas de `CLAUDE.md` e `AGENTS.md` do diretório, se existirem (nunca inventa).
- `meister plan guide [--lang en|pt-BR]`: imprime o guia completo (padrão: o idioma configurado). Conteúdo: o fluxo acima; divisão em MUITAS tarefas pequenas (3 a 5 arquivos, uma entrega testável por tarefa, descrição curta de cerca de 1,5 mil tokens); `**Files:**` é o escopo e o gate reprova o que sai dele (glob aceito); `**Depends on:**` só quando há dependência real, e `scope.allow_dependency_files` permite editar arquivos das dependências; paralelismo entre tarefas que não compartilham arquivos; o que NÃO fazer (citar frases de erro de provedor na descrição; pedir edição do `meister.config.yaml`; tarefas gigantes); exemplos em Markdown e JSON.
- `meister plan import`: `--format` passa a ter padrão `auto` (detecta Markdown de tarefas ou JSON canônico) e aceita `markdown` como nome do formato nativo. `--format superpowers` continua valendo, **sem aviso de obsolescência** (é o mesmo formato).

## 3. Mudanças nos textos
- Modelos do `init` (`meister/templates/{en,pt-BR}/{CLAUDE,CODEX,AGENTS}.md.template`): o fluxo de plano passa a `plan guide`, `plan new`, `plan import`, `plan validate`, `orchestrate`; o superpowers aparece como "se você usa a skill".
- `README.md` e `README.pt-BR.md`, `docs/plan-format.md` e `docs/pt-BR/FORMATO_DO_PLANO.md`: o fluxo nativo primeiro; a seção "Superpowers: recommended, not required" fica como "Superpowers (opcional)".
- `CLAUDE.md` do repositório, seção 05: título e passos 1 e 2 sem tornar a skill obrigatória (a regra "use `meister orchestrate`" e a política de planos pequenos não mudam).

## 4. Não objetivos
Mudar o formato dos planos, renomear `docs/superpowers/` (convenção de caminho deste repositório), validar semanticamente o conteúdo das tarefas.

## 5. Testes
`plan new` produz um Markdown que passa por `plan import` e `plan validate` sem edição, para qualquer N de 1 a 20; o JSON gerado passa por `plan validate`; `--project` sem arquivos não inventa regras; `plan guide` traz as seções-chave nos dois idiomas; `plan import` sem `--format` aceita Markdown e JSON; `--format superpowers` e `markdown` dão o mesmo resultado; paridade dos catálogos e `test_i18n_ratchet`; modelos do `init` mencionam `plan guide` e `plan new` (e `tests/test_init_templates.py`).

## 6. Divisão sugerida em tarefas
1. Guia: `meister/plan_guide.py` e catálogos (en e pt-BR) com o texto.
2. Modelo: `meister/plan_template.py` (Markdown e JSON; `--project`).
3. CLI: `plan new` e `plan guide`.
4. `plan import` com `--format auto` e o nome `markdown`.
5. Modelos do `init` (6 arquivos).
6. Documentação (READMEs, `plan-format`, `CLAUDE.md` seção 05).
7. CHANGELOG e status deste spec.
