# Gate para Java/Maven no Windows (Java 8, Maven externo, GitLab)

**Status:** aprovado pelo dono em 2026-10-11; plano de 4 tarefas.

## Contexto
O dono tem um projeto desktop Java 8 com Maven, no Windows, hospedado no GitLab, e usa um Maven externo instalado (o Maven embutido do Eclipse, m2e, é um conjunto de bibliotecas carregado pela IDE e, pelo que se sabe, não oferece um comando `mvn` que o gate possa chamar).

## O que já funciona sem código novo
- `gate.commands`, `gate.install` e `gate.allow_unverified` já aceitam qualquer linguagem.
- O Meister só usa `git` local (worktrees e integração); GitLab não exige mudança de código. Abrir o merge request é uma etapa fora do Meister (`git push` e `glab mr create`).

## Mudanças
1. **`gate.env`**: mapa de variáveis de ambiente aplicado aos comandos do gate (por exemplo `JAVA_HOME` do JDK 8 e `MAVEN_HOME`); se `JAVA_HOME` existir, o seu `bin` entra no início do `PATH`. `target/**` entra nos arquivos tolerados por padrão.
2. **Detecção de Maven**: `pom.xml` passa a ser reconhecido pelo gate sem `gate.commands`. O executável é resolvido nesta ordem: `mvnw.cmd`/`mvnw` do projeto; `mvn` do PATH (resolvido com PATHEXT, porque no Windows é um `.cmd`); `%MAVEN_HOME%\bin\mvn.cmd` ou `%M2_HOME%`. Comando: `mvn -B -q test`. Se nada for achado, o gate falha com mensagem clara dizendo que o Maven embutido do Eclipse não serve.
3. **Documentação**: página "Java 8, Maven e GitLab" (EN e pt-BR) com um `meister.config.yaml` de exemplo, o uso do mesmo Maven da IDE (Window > Preferences > Maven > Installations) e o fluxo de MR no GitLab.

## Fora do escopo
Gradle, abrir MR pelo Meister, cópia de `target/` entre worktrees, flags de build opcionais (`-o`, `-T 1C`): quem quiser usa `gate.commands`. `-o` e `-T` ficam de fora do padrão porque `-o` quebra quando uma dependência não está no repositório local e `-T` pode quebrar plugins que não são thread-safe.

## Verificação
Testes com `mvn`, `mvn.cmd` e `mvnw` simulados (diretórios temporários, sem Maven real). **Não verificado:** execução real num projeto Java 8 no Windows do dono; sugestão: rodar `meister orchestrate` com uma tarefa trivial lá.
