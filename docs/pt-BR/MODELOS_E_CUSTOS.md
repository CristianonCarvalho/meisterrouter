# Modelos e custos: tabela de estudo

Atualizada em **2026-10-05**. Serve para estudar e justificar mudanças na escolha de modelos e vias. Os mesmos dados estão em
[`modelos_e_custos.csv`](../modelos_e_custos.csv) (para planilha).

**Fontes:** OpenRouter (API pública `https://openrouter.ai/api/v1/models` e o endpoint do Jev em
`/api/v1/models/typesafe/jev-1.13/endpoints`) para preços por token; [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models)
para custo por tarefa e índice de inteligência. Os preços por token dos dois batem. A cobrança do Copilot vem da
[documentação do GitHub](https://docs.github.com/copilot/concepts/billing/usage-based-billing-for-individuals): 1 crédito de IA = US$ 0,01.

## Como ler
- **Entrada / saída:** USD por 1M de tokens. **Combinado 3:1** = (3 × entrada + saída) / 4, convenção do Artificial Analysis. É o valor de
  `cost_per_m_tokens` no catálogo (`meister/default_config.yaml`). Para o Jev vale só a entrada, porque a saída é grátis.
- **O esforço de raciocínio não muda o preço por token**, só quantos tokens o modelo gasta. Por isso o preço combinado é igual em todas as
  linhas do mesmo modelo, e o que diferencia os esforços é o **custo por tarefa**.
- **Custo/tarefa AA** (USD): média no benchmark de raciocínio do Artificial Analysis. **Não é o nosso trabalho de código**; serve para comparar
  modelos entre si, não para prever a nossa fatura. **Índice AA:** Artificial Analysis Intelligence Index (não é o Coding Agent Index).

## Em uso no Meister

| Modelo | Esforço | Entrada / saída | Combinado 3:1 | Custo/tarefa AA | Índice AA | Uso no Meister |
|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | low | 2.00 / 10.00 | **4.00** | 0.42 | 36 | — |
| Claude Sonnet 5.5 | medium | 2.00 / 10.00 | **4.00** | 0.59 | 41 | — |
| Claude Sonnet 5.5 | high | 2.00 / 10.00 | **4.00** | 1.12 | 47 | **claude_sonnet** |
| Claude Sonnet 5.5 | xhigh | 2.00 / 10.00 | **4.00** | 2.75 | 52 | — |
| Claude Sonnet 5.5 | max | 2.00 / 10.00 | **4.00** | 7.67 | 56 | — |
| Gemini 3.8 Flash | low | 0.75 / 3.75 | **1.50** | — | 33 | — |
| Gemini 3.8 Flash | medium | 0.75 / 3.75 | **1.50** | 0.93 | 40 | — |
| Gemini 3.8 Flash | high | 0.75 / 3.75 | **1.50** | 1.24 | 41 | **agy_gemini_flash** |
| GPT-6 Luna | non-reasoning | 0.10 / 0.50 | **0.20** | 0.01 | 18 | — |
| GPT-6 Luna | low | 0.10 / 0.50 | **0.20** | 0.0045 | 22 | — |
| GPT-6 Luna | medium | 0.10 / 0.50 | **0.20** | 0.02 | 30 | **copilot_luna;codex_luna** |
| GPT-6 Luna | high | 0.10 / 0.50 | **0.20** | 0.03 | 33 | — |
| GPT-6 Luna | xhigh | 0.10 / 0.50 | **0.20** | 0.04 | 35 | — |
| GPT-6 Luna | max | 0.10 / 0.50 | **0.20** | 0.07 | 38 | — |
| TypeSafe Jev 1.13 | - | 0.042 / 0.00 | **0.042** | — | — | **(jev, só decisões)** |

| Via do Meister | Harness | Modelo | Como é cobrado |
|---|---|---|---|
| `copilot_luna` (1ª via) | `copilot` | GPT-6 Luna | Créditos do Copilot (1 crédito = US$ 0,01), pelos tokens de entrada, saída e cache |
| `codex_luna` (desligada) | `codex` | GPT-6 Luna | Créditos do Codex, gratuitos e poucos: não gastar em teste |
| `agy_gemini_flash` | `agy` | Gemini 3.8 Flash high | Assinatura subsidiada do dono: o custo efetivo é menor que o de lista |
| `claude_sonnet` | `claude` | Sonnet 5.5 | Não verificado (assinatura ou API) |
| Jev (só decisões) | OpenRouter | `typesafe/jev-1.13` | US$ 0,042 por 1M tokens de entrada; saída grátis |

## Para estudar mudanças de escolha
**Intermediário (mesmo preço do Sonnet por token, bem menos tokens por tarefa):**

| Modelo | Esforço | Entrada / saída | Combinado 3:1 | Custo/tarefa AA | Índice AA | Uso no Meister |
|---|---|---|---|---|---|---|
| GPT-6.1 Sol | low | 2.00 / 10.00 | **4.00** | 0.13 | 42 | — |
| GPT-6.1 Sol | medium | 2.00 / 10.00 | **4.00** | 0.21 | 48 | — |
| GPT-6.1 Sol | high | 2.00 / 10.00 | **4.00** | 0.32 | 50 | — |
| GPT-6.1 Sol | xhigh | 2.00 / 10.00 | **4.00** | 0.39 | 51 | — |
| GPT-6.1 Sol | max | 2.00 / 10.00 | **4.00** | 0.72 | 52 | — |

**Mais caros (referência):**

| Modelo | Esforço | Entrada / saída | Combinado 3:1 | Custo/tarefa AA | Índice AA | Uso no Meister |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | low | 4.00 / 20.00 | **8.00** | 0.55 | 42 | — |
| Claude Opus 5.5 | medium | 4.00 / 20.00 | **8.00** | 1.34 | 51 | — |
| Claude Opus 5.5 | high | 4.00 / 20.00 | **8.00** | 1.82 | 54 | — |
| Claude Opus 5.5 | xhigh | 4.00 / 20.00 | **8.00** | 3.46 | 56 | — |
| Claude Opus 5.5 | max | 4.00 / 20.00 | **8.00** | 5.98 | 58 | — |
| GPT-6 Astra | low | 10.00 / 50.00 | **20.00** | 0.82 | 46 | — |
| GPT-6 Astra | medium | 10.00 / 50.00 | **20.00** | 1.54 | 50 | — |
| GPT-6 Astra | high | 10.00 / 50.00 | **20.00** | 1.73 | 51 | — |
| GPT-6 Astra | xhigh | 10.00 / 50.00 | **20.00** | 2.31 | 52 | — |
| GPT-6 Astra | max | 10.00 / 50.00 | **20.00** | 3.26 | 53 | — |
| Claude Fable 5.1 | low | 10.00 / 50.00 | **20.00** | 2.37 | 47 | — |
| Claude Fable 5.1 | medium | 10.00 / 50.00 | **20.00** | 2.98 | 49 | — |
| Claude Fable 5.1 | high | 10.00 / 50.00 | **20.00** | 3.91 | 51 | — |
| Claude Fable 5.1 | xhigh | 10.00 / 50.00 | **20.00** | 5.98 | 53 | — |
| Claude Fable 5.1 | max | 10.00 / 50.00 | **20.00** | 7.63 | 53 | — |
| Gemini 4 Argon | high | 2.00 / 10.00 | **4.00** | 1.99 | 53 | — |

**Baratos de referência (sem harness no Meister; disponibilidade por CLI não verificada):**

| Modelo | Esforço | Entrada / saída | Combinado 3:1 | Custo/tarefa AA | Índice AA | Uso no Meister |
|---|---|---|---|---|---|---|
| Claude 4.5 Haiku | reasoning | 1.00 / 5.00 | **2.00** | 0.28 | 17 | — |
| GLM-5.3-Flash | - | 0.15 / 0.50 | **0.2375** | 0.25 | 42 | — |
| Qwen3.8-Flash-Next | - | 0.15 / 0.47 | **0.23** | 0.37 | 40 | — |
| DeepSeek V4.1 Flash | max | 0.30 / 1.20 | **0.525** | 0.27 | 39 | — |
| MiMo-V2.6-Flash | - | 0.14 / 0.28 | **0.175** | 0.06 | 38 | — |
| Gemini 3.5 Flash-Lite | - | 0.30 / 2.50 | **0.85** | 0.12 | 22 | — |

### Leituras úteis
- O Luna é cerca de **37 vezes** mais barato por tarefa que o Sonnet 5.5 high (0,03 contra 1,12): mantém-se como primeira via.
- No Artificial Analysis o **Gemini 3.8 Flash high custa mais por tarefa que o Sonnet 5.5 high** (1,24 contra 1,12), apesar de o token custar
  2,7 vezes menos, porque gasta mais tokens de raciocínio. O subsídio da assinatura compensa isso para o dono; sem o subsídio a conta mudaria.
- **Gemini 3.8 Flash medium** custa 25% menos por tarefa que o high (0,93 contra 1,24) com índice quase igual (40 contra 41).
- **GPT-6.1 Sol low** tem índice 42 por 0,13 por tarefa, contra 41 por 1,24 do Gemini high: candidato a estudar, se estiver disponível pelo Copilot (não verificado).
- Os modelos baratos de referência têm índice 38 a 42 por 0,06 a 0,37 por tarefa, mas não têm harness no Meister.

## Uso real observado (projeto de teste, run 2, 2026-10-04)
| Via | Tokens (entrada / saída) | Preço de lista, sem cache | Catálogo (combinado × tokens) | Cobrado de verdade |
|---|---|---|---|---|
| `copilot_luna` (9 tarefas) | 2.271.700 / 41.200 | US$ 0,248 | US$ 0,463 | **US$ 0,072** (7,16 créditos) |
| `agy_gemini_flash` (1 tarefa) | 288.094 / 20.571 | US$ 0,293 | US$ 0,463 | não medido (assinatura) |

O catálogo **superestima** o gasto: o uso real é cerca de 98% entrada, e a maior parte é cache de leitura (US$ 0,01 por 1M no Luna), que o preço
combinado não conhece. Para o Copilot, os créditos × US$ 0,01 são a medida fiel. Cada chamada, mesmo trivial, consome 15 a 29 mil tokens de entrada
(contexto do projeto).
O `meister report` e o dashboard agora calculam o custo do Copilot a partir dos créditos registrados pelo CLI.
O preço por crédito (`credit_usd`) fica no catálogo de vias da configuração; a estimativa do catálogo não é usada quando há créditos.

## Lacunas
- O **Coding Agent Index** do Artificial Analysis não foi coletado (só o Intelligence Index e o custo por tarefa).
- O **esforço real** do Luna no Copilot e no Codex não está confirmado (documentação dizia medium; amostra do Codex mostrou `none`), e o do worker
  `claude_sonnet` usa o padrão do CLI.
- **Disponibilidade de cada modelo por harness** (Copilot, Codex, agy, claude) não foi verificada.
- O custo por tarefa do Artificial Analysis é de um benchmark de raciocínio.

## Como atualizar
1. **Preços por token (OpenRouter):**
   `curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys;[print(m['id'],m['pricing']) for m in json.load(sys.stdin)['data'] if 'luna' in m['id']]"`
   (multiplique por 1e6; o endpoint `/api/v1/models/<id>/endpoints` traz o preço por provedor, como no Jev).
2. **Custo por tarefa e índice (Artificial Analysis):** a página é dinâmica; abra no navegador a tabela do leaderboard e use "Expand columns".
3. Atualize o CSV e esta tabela, e o `cost_per_m_tokens` do catálogo se o combinado mudar. O teste `test_model_table_matches_catalog`
   (`tests/test_hygiene.py`) falha se a tabela e o catálogo divergirem.
