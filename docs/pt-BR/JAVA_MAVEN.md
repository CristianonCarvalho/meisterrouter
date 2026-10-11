# Java 8, Maven e GitLab

🌐 [English](../java-maven.md) · **Português (Brasil)**

Como usar o MeisterRouter num projeto Java 8 com Maven, no Windows, hospedado no GitLab.

## O que o Meister detecta

Quando a raiz do projeto tem um `pom.xml` e `gate.commands` está vazio, o gate roda o Maven sozinho:

```
mvn -B -q test
```

O executável do Maven é resolvido nesta ordem:

1. O Maven Wrapper do projeto: `mvnw.cmd` no Windows, `mvnw` nos demais.
2. `mvn` no `PATH` (no Windows ele é achado pelo `PATHEXT`, porque é um `mvn.cmd`).
3. `%MAVEN_HOME%\bin\mvn.cmd` e depois `%M2_HOME%\bin\mvn.cmd` (`bin/mvn` fora do Windows).

Se nada for achado, o gate falha com uma mensagem dizendo isso. As variáveis usadas vêm do ambiente somado ao `gate.env` (abaixo).

## O Maven embutido do Eclipse não serve

O Maven embutido do Eclipse (m2e) é um conjunto de bibliotecas carregado pela IDE; ele não oferece um comando `mvn` que o gate possa chamar. Para o gate e a IDE usarem o mesmo Maven:

1. Instale um Maven externo (descompacte o arquivo do Apache Maven, por exemplo em `C:\tools\apache-maven-3.9.9`).
2. No Eclipse, abra **Window > Preferences > Maven > Installations**, clique em **Add...**, escolha essa pasta e marque-a como padrão.
3. Aponte o `MAVEN_HOME` para ela (variável do sistema ou `gate.env`).

Alternativa: o Maven Wrapper. Rode `mvn -N wrapper:wrapper` uma vez no projeto e versione `mvnw`, `mvnw.cmd` e `.mvn/`. O wrapper vem primeiro na ordem de resolução, então todo worktree usa a mesma versão do Maven.

## Exemplo de `meister.config.yaml`

```yaml
gate:
  env:                                   # aplicado a todos os comandos do gate
    JAVA_HOME: 'C:\Program Files\Java\jdk1.8.0_431'
    MAVEN_HOME: 'C:\tools\apache-maven-3.9.9'
  # Opcional: substitui o padrão "mvn -B -q test".
  # commands:
  #   - {name: unit, run: "mvn -B -q test", timeout_seconds: 900, required: true}
  # Variante mais rápida, opcional:
  #   - {name: unit, run: "mvn -B -q -o -T 1C test", timeout_seconds: 900, required: true}
```

- Com `JAVA_HOME` no `gate.env`, a pasta `bin` dele entra no início do `PATH` do gate, então `java` e `javac` são os do JDK 8 mesmo que haja outro JDK instalado.
- Use aspas simples nos caminhos do Windows no YAML, para as barras invertidas ficarem como estão.
- `-o` (offline) quebra se faltar uma dependência no repositório local (`~/.m2/repository`); `-T 1C` (build em paralelo) pode quebrar plugins que não são thread-safe. Por isso nenhum dos dois está no comando padrão.
- `target/**` é tolerado por padrão, então a saída do build nunca conta como alteração fora do escopo da tarefa. Se você definir `scope.tolerated_files`, a lista substitui a padrão: mantenha `target/**` nela.

Acrescente também `target/` ao `.gitignore` do projeto, para a saída do build nunca ser commitada:

```
target/
```

## Fluxo com o GitLab

O Meister só usa o git local (worktrees e integração); ele nunca fala com o GitLab. Depois da run:

```bash
git push -u origin <sua-branch>
glab mr create --fill          # ou abra o merge request pela página do GitLab
```

## Não verificado

A perna Windows da CI agora roda o gate num projeto Maven de verdade, com o Maven e o JDK 8 do runner (`tests/test_gate_maven_real.py`), o que cobre o `mvn.cmd`, o `PATHEXT` e os caminhos do Windows. O que continua sem verificação é a máquina do dono: o Maven externo e o JDK instalados nela. Antes de um plano de verdade, experimente uma tarefa trivial (por exemplo, acrescentar um comentário a uma classe) com `meister orchestrate` e confira se o gate roda o Maven e passa.
