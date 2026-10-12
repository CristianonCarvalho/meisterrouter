# Testar lotes Java sem ambiente de homologação

🌐 [English](../java-testing-without-staging.md) · **Português (Brasil)**

Como testar de forma automática um lote Java 8 que acessa sites legados (respostas em JSON), **sem tocar na produção** e sem Docker. O exemplo completo, que compila e passa no JDK 8 + Maven, está em [`docs/examples/java-batch-testing/`](../examples/java-batch-testing/).

> Teste não garante que a funcionalidade está correta. Ele reduz o risco até um nível aceitável e avisa cedo quando algo quebra. As seções finais dizem o que continua exigindo cuidado em produção.

## A ideia

Trazer um "site falso" para dentro do teste, em vez de levar o teste até o site real:

1. a regra de negócio é separada do acesso HTTP e testada com arquivos JSON;
2. o acesso HTTP é testado contra um servidor falso (WireMock) em `localhost`, que responde com JSON gravado;
3. o resultado do lote é comparado com uma saída de referência (golden master);
4. um teste à parte, só de leitura, avisa quando o site real mudou de formato.

## As camadas do exemplo

| Camada | Arquivo do exemplo | O que pega | Custo |
|---|---|---|---|
| 1. Unidade (sem rede) | `PedidoParserTest` | regra de negócio, campo ausente, valor inválido | milissegundos |
| 2. Lote × site falso | `LoteProcessorTest` | paginação, erro 5xx, sessão expirada, timeout, login recusado, JSON truncado, item malformado | cerca de 2 s |
| 3. Contrato | `ContratoJsonTest` | campo que sumiu ou mudou de tipo na resposta gravada | milissegundos |
| 4. Leitura do site real | `VerificacaoLeituraTest` | o site real mudou de layout | fora do `mvn test`, sob demanda |

O golden master está em `LoteProcessorTest.loteCompletoReproduzOGoldenMaster` (compara com `src/test/resources/golden/saida_esperada.csv`). O dry-run (`Saida.DESCARTAR`) processa tudo sem gravar.

## Como aplicar no projeto real

1. **URL base configurável.** Se o código tem a URL escrita dentro dele, passe-a por construtor ou propriedade (`SiteClient`). Em produção aponta para o site; no teste, para `http://localhost:<porta>`. Em geral é a única mudança no código de produção.
2. **Separe a busca do tratamento.** Uma classe faz a requisição e devolve o texto; outra converte o JSON em objetos e aplica as regras (`PedidoParser`). A segunda se testa só com arquivos.
3. **Grave as respostas reais uma vez.** Salve o JSON em `src/test/resources/fixtures/`, com dados pessoais anonimizados antes do commit. Use apenas requisições de leitura. O modo de gravação do WireMock, `curl` ou o log do próprio lote servem. Faça uma fixture por caso: normal, vazia, com erro, com campo faltando, última página.
4. **Simule as falhas.** Para cada falha que já aconteceu em produção, escreva um teste com WireMock que a reproduz (veja os testes de `LoteProcessorTest`).
5. **Crie o golden master.** Rode o lote atual sobre as fixtures, revise a saída à mão e guarde-a. Depois de qualquer mudança, a diferença tem que ser intencional.
6. **Ofereça dry-run.** O lote percorre e trata tudo, mas a `Saida` não grava. É o ensaio final antes de rodar de verdade.
7. **Rode a verificação de leitura de vez em quando**, por exemplo antes de cada execução do lote.

## Versões (Java 8)

Fixe estas versões; as mais novas exigem Java 11 ou mais:

| Biblioteca | Versão | Observação |
|---|---|---|
| JUnit Jupiter | 5.10.x | |
| WireMock | `wiremock-jre8` 2.35.x | a 3.x exige Java 11 |
| JSONAssert | 1.5.x | |
| Mockito (se usar) | 4.11.x | a 5.x exige Java 11 |
| Surefire | 3.2.x | precisa ser 2.22 ou mais para JUnit 5 |

Tudo vem do Maven Central.

## Rodar

```bash
cd docs/examples/java-batch-testing
export JAVA_HOME=/caminho/do/jdk8     # no Windows: set JAVA_HOME=C:\caminho\do\jdk8
mvn -B test
```

A verificação de leitura contra o site real é separada e só faz GET:

```bash
mvn test -Pverificacao-leitura -Dsite.url=https://site-legado -Dsite.token=<token>
```

Sem `-Dsite.url` e `-Dsite.token` ela não executa nada.

## Como o Meister ajuda

O gate já roda `mvn -B -q test` num worktree isolado, então tudo acima vira barreira automática a cada tarefa. O plano recomendado tem duas etapas:

1. **Rede de proteção** (só testes e a extração da URL base): separar busca e tratamento, gravar as fixtures, criar o golden master. O gate fica verde com o comportamento **atual**.
2. **A mudança da funcionalidade**, protegida por esses testes. Se algo do comportamento antigo quebrar sem querer, o gate reprova.

O gate roda `mvn test`. Testes que dependem de `mvn verify` (Failsafe) não entram nele.

## Evidência do exemplo

14 testes passando no JDK 8 (Temurin 1.8.0_504) com Maven. Para provar que eles protegem de verdade, dois defeitos foram injetados no código e ambos foram pegos: mudar o desconto de 10% para 20% reprovou 2 testes; remover o novo login quando a sessão expira reprovou 1.

## O que isto não cobre

- **Mudança do site que nunca foi gravada.** O teste só conhece as respostas que você guardou. A verificação de leitura reduz esse risco, não o elimina.
- **Dados reais com casos que ninguém viu.** Amplie as fixtures sempre que um caso novo aparecer em produção.
- **Efeitos colaterais no site real.** Se o lote grava ou altera dados lá, o comportamento dessa escrita só é simulado. Mantenha o dry-run e comece por poucos registros.
- **Carga e concorrência.** O exemplo não mede desempenho.
- **Respostas em HTML.** O padrão é o mesmo (fixtures + WireMock), mas o parser é mais frágil e o contrato precisa de mais atenção.

Para o Java 8, Maven e o Meister no Windows, veja [JAVA_MAVEN.md](JAVA_MAVEN.md).
