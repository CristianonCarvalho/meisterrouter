# Changelog

Este changelog segue o formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O projeto usa versionamento semântico e permanece em `0.x` até o contrato (plano JSON, `meister.config.yaml` e eventos do log) estabilizar. Os números `#N` são pull requests.

## [0.9.0] - 2026-10-07

Primeira versão marcada. Consolida o histórico dos PRs #1 a #73 (29/09 a 07/10/2026).

### Adicionado
- `gate.docs_only` opcional e desligado por padrão para mudanças só de documentação, antes e depois do merge; o gate final sempre executa a verificação completa.
- **Orquestração:** execução de planos em DAG com worktree por subtarefa, merge `--no-ff` numa branch de integração e fast-forward da `main` só no fim (#1, #4, #6); contrato de plano canônico com adaptador do superpowers (#5); `--resume` que reaproveita tarefas concluídas (#24); retentativa na mesma via quando o pane some (#26); progresso por tarefa e resumo final (#28); `meister plan analyze` e aviso de plano serial (#46); timeout por inatividade com trabalho preservado (#32).
- **Roteamento:** o Jev escolhe a via inicial de cada subtarefa e é o modo padrão, com proteção contra Jev lento (#13, #17); contexto estruturado para o Jev (#33); `max_parallel` por via (#16); `eligible_classes` por via, uma regra determinística sobre a escolha do Jev (#63).
- **Configuração:** catálogo de modelos 100% em configuração, com padrões empacotados (#14, #15); validação e exibição das vias, vias desligáveis, Copilot como primeira via (#12, #19).
- **Gate:** escopo com glob, preparo de ambiente Node e portão configurável (#22); venv do projeto, `{python}` e `ok_exit_codes` (#49); gate pré-merge fora da trava de integração (#47); cache do gate por árvore de arquivos (#66); testes em paralelo com `pytest-xdist` (#64).
- **Custo e medição:** uso e custo reais dos workers e tempo por fase (#43); `meister report` (#44); custo do Copilot por créditos (`credit_usd`) (#51); preços de modelos atualizados (#50).
- **Observabilidade:** dashboard agrupado e analítico, com projeto, run, tarefas legíveis, custo, overhead e pico de workers (#35, #39, #41, #45, #53, #54); linha do tempo (Gantt) em TUI, com modo ao vivo, visão de todos os runs, linha do Jev e estado "sem sinal" (#55, #56, #57, #58, #62); suporte a tarefas rodadas por `meister worker` (#73).
- **Operação:** `meister clean` remove branches antigas com segurança (#34); `meister init` seguro (#20); guard do Claude Code com modos `block`/`ask`/`off` e regra "método de execução = Meister" (#68, #69); manual de execução (#25, #29); diagramas Mermaid (#72) e fluxo ponta a ponta (#18).
- **Instalação automática:** `meister setup` liga o plugin ao Herdr, grava os atalhos num bloco gerenciado do `config.toml` (com backup e validação), confere o ambiente e, com `--project`, equipa o projeto; `meister config init` gera o `meister.config.yaml` com as vias e a ordem; `bin/install.sh` exige o Herdr e chama o `setup` (#76).
- **Instalador com versão fixa:** `install.sh --version vN.N.N|latest|main` (ou `MEISTER_VERSION`), por exemplo `curl ... | bash -s -- --version v0.9.0`.
- **`meister --version`**, fonte única da versão em `meister/__init__.py` e este changelog (#74).
- **README em inglês** (com o português em `README.pt-BR.md`) para a publicação no marketplace de plugins do Herdr; o aviso de que o plugin sozinho não instala o CLI `meister`; manifesto sem os atalhos que o Herdr não lê.
- **Plugin do Herdr:** wrapper de entrada (`bin/herdr-meister.sh`) no manifesto `herdr-plugin.toml` para localizar o CLI `meister` (`PATH`, `~/.local/bin`, Homebrew) e exibir notificação de instalação com encerramento 127 quando ausente (e saída 0 silenciosa no startup).
- **Documentação:** uso direto (a LLM orquestradora usa o Meister pelas regras e hooks), pré-requisitos (Herdr obrigatório, Superpowers recomendado), página do [formato do plano](docs/pt-BR/FORMATO_DO_PLANO.md) e instalação por `curl` (repositório público); README enxuto (o MeisterRouter é um plugin do Herdr, que é pré-requisito), com a página [Instalação alternativa e comandos avançados](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md), capturas da linha do tempo e do dashboard (#75) e diagramas atualizados.

### Corrigido
- Integração: commits do worker feitos no próprio worktree são integrados (#4); a branch de integração é reconstruída por merge (#6); trabalho rejeitado nunca se perde, inclusive mudanças não commitadas (#60).
- Detecção de pane desaparecido com o formato real de eventos do Herdr (#10) e vias com disjuntor aberto são puladas (#7).
- Tarefas sem `target_files` rodam isoladas (#9); rotas `[id]` do Next.js e `--resume` do mesmo run (#31); `Global Constraints` não vaza para cada tarefa (#23); importador do superpowers ignora títulos em blocos de código (#48).
- `npm install` não roda quando não há nada a instalar (#61); worktrees `prunable` não protegem mais a branch (#37).
- Dashboard: tabela legível, chamadas do Jev ligadas às tarefas, ordenação e custo com o catálogo atual (#38, #40, #42, #52).

### Testes e CI
- Matriz de quedas com 13 pontos de falha e scripts E2E com ferramentas reais (#2, #3, #8, #11, #30); watchdog por teste e guarda contra vazamento de branches (#36); testes independentes do relógio e estabilizados sob carga (#21, #27, #59, #65, #67).
