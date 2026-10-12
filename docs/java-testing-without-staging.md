# Testing Java batch jobs without a staging environment

🌐 **English** · [Português (Brasil)](pt-BR/TESTES_JAVA_SEM_HOMOLOGACAO.md)

How to test, automatically, a Java 8 batch job that calls legacy sites (JSON responses) **without touching production** and without Docker. The complete example, which compiles and passes on JDK 8 + Maven, is in [`docs/examples/java-batch-testing/`](examples/java-batch-testing/).

> Tests do not prove a feature correct. They lower the risk to an acceptable level and warn you early when something breaks. The last sections say what still needs care in production.

## The idea

Bring a "fake site" into the test instead of taking the test to the real site:

1. business rules are separated from HTTP access and tested with JSON files;
2. HTTP access is tested against a fake server (WireMock) on `localhost`, answering with recorded JSON;
3. the batch output is compared with a reference output (golden master);
4. a separate, read-only test warns when the real site changed its format.

## The layers in the example

| Layer | Example file | What it catches | Cost |
|---|---|---|---|
| 1. Unit (no network) | `PedidoParserTest` | business rules, missing field, invalid value | milliseconds |
| 2. Batch × fake site | `LoteProcessorTest` | pagination, 5xx errors, expired session, timeout, refused login, truncated JSON, malformed item | about 2 s |
| 3. Contract | `ContratoJsonTest` | a field that disappeared or changed type in the recorded response | milliseconds |
| 4. Real-site read check | `VerificacaoLeituraTest` | the real site changed its layout | outside `mvn test`, on demand |

The golden master is `LoteProcessorTest.loteCompletoReproduzOGoldenMaster` (compares with `src/test/resources/golden/saida_esperada.csv`). Dry-run (`Saida.DESCARTAR`) processes everything without writing.

## Applying it to the real project

1. **Configurable base URL.** If the URL is hard-coded, pass it through a constructor or property (`SiteClient`). In production it points at the site; in tests, at `http://localhost:<port>`. Usually this is the only change to production code.
2. **Split fetching from processing.** One class makes the request and returns text; another turns the JSON into objects and applies the rules (`PedidoParser`). The second one is tested with files only.
3. **Record real responses once.** Save the JSON in `src/test/resources/fixtures/`, anonymizing personal data before the commit. Use read-only requests only. WireMock's record mode, `curl` or the batch's own log all work. Keep one fixture per case: normal, empty, error, missing field, last page.
4. **Simulate the failures.** For every failure that has happened in production, write a WireMock test that reproduces it (see `LoteProcessorTest`).
5. **Create the golden master.** Run the current batch over the fixtures, review the output by hand and keep it. After any change, the difference must be intentional.
6. **Offer dry-run.** The batch walks and processes everything, but the `Saida` writes nothing. It is the final rehearsal before the real run.
7. **Run the read check from time to time**, for example before each batch execution.

## Versions (Java 8)

Pin these; newer ones require Java 11 or later:

| Library | Version | Note |
|---|---|---|
| JUnit Jupiter | 5.10.x | |
| WireMock | `wiremock-jre8` 2.35.x | 3.x needs Java 11 |
| JSONAssert | 1.5.x | |
| Mockito (if used) | 4.11.x | 5.x needs Java 11 |
| Surefire | 3.2.x | must be 2.22 or later for JUnit 5 |

Everything comes from Maven Central.

## Running it

```bash
cd docs/examples/java-batch-testing
export JAVA_HOME=/path/to/jdk8        # Windows: set JAVA_HOME=C:\path\to\jdk8
mvn -B test
```

The read check against the real site is separate and only issues GETs:

```bash
mvn test -Pverificacao-leitura -Dsite.url=https://legacy-site -Dsite.token=<token>
```

Without `-Dsite.url` and `-Dsite.token` it runs nothing.

## How Meister helps

The gate already runs `mvn -B -q test` in an isolated worktree, so everything above becomes an automatic barrier on each task. The recommended plan has two stages:

1. **Safety net** (tests and the base-URL extraction only): split fetching from processing, record the fixtures, create the golden master. The gate goes green on the **current** behavior.
2. **The feature change**, protected by those tests. If old behavior breaks by accident, the gate rejects it.

The gate runs `mvn test`. Tests that need `mvn verify` (Failsafe) are not part of it.

## Evidence from the example

14 tests pass on JDK 8 (Temurin 1.8.0_504) with Maven. To show they really protect, two defects were injected into the code and both were caught: changing the discount from 10% to 20% failed 2 tests; removing the re-login on expired session failed 1.

## What this does not cover

- **A site change that was never recorded.** Tests only know the responses you kept. The read check lowers this risk but does not remove it.
- **Real data with cases nobody has seen.** Grow the fixtures whenever a new case shows up in production.
- **Side effects on the real site.** If the batch writes or changes data there, that write is only simulated. Keep dry-run and start with a few records.
- **Load and concurrency.** The example does not measure performance.
- **HTML responses.** The pattern is the same (fixtures + WireMock), but the parser is more fragile and the contract needs more attention.

For Java 8, Maven and Meister on Windows, see [java-maven.md](java-maven.md).
