# Models and costs: reference table

Updated on **2026-10-05**. Use this page to study and justify changes to model and lane selection. The same data is available in
[`modelos_e_custos.csv`](modelos_e_custos.csv) (for spreadsheets).

**Sources:** OpenRouter (public API `https://openrouter.ai/api/v1/models` and the Jev endpoint at
`/api/v1/models/typesafe/jev-1.13/endpoints`) for per-token prices; [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models)
for cost per task and intelligence index. The per-token prices from both sources match. Copilot billing comes from the
[GitHub documentation](https://docs.github.com/copilot/concepts/billing/usage-based-billing-for-individuals): 1 AI credit = US$ 0.01.

## How to read this
- **Input / output:** USD per 1M tokens. **Combined 3:1** = (3 × input + output) / 4, the Artificial Analysis convention. This is the value of
  `cost_per_m_tokens` in the catalog (`meister/default_config.yaml`). For Jev, only the input cost applies because output is free.
- **Reasoning effort does not change the per-token price**, only the number of tokens the model uses. That is why the combined price is the same
  across all rows for the same model, while **cost per task** distinguishes the effort levels.
- **AA cost/task** (USD): average on the Artificial Analysis reasoning benchmark. **This is not our code work**; it is for comparing models,
  not predicting our bill. **AA Index:** Artificial Analysis Intelligence Index (not the Coding Agent Index).

## In use at Meister

| Model | Effort | Input / output | Combined 3:1 | AA cost/task | AA Index | Use at Meister |
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
| TypeSafe Jev 1.13 | - | 0.042 / 0.00 | **0.042** | — | — | **(jev, decisions only)** |

| Meister lane | Harness | Model | How it is billed |
|---|---|---|---|
| `copilot_luna` (1st lane) | `copilot` | GPT-6 Luna | Copilot credits (1 credit = US$ 0.01), for input, output, and cache tokens |
| `codex_luna` (disabled) | `codex` | GPT-6 Luna | Codex credits, free and limited: do not spend them on testing |
| `agy_gemini_flash` | `agy` | Gemini 3.8 Flash high | Owner's subsidized subscription: the effective cost is lower than list price |
| `claude_sonnet` | `claude` | Sonnet 5.5 | Not verified (subscription or API) |
| Jev (decisions only) | OpenRouter | `typesafe/jev-1.13` | US$ 0.042 per 1M input tokens; output is free |

## For studying selection changes
**Mid-range (same per-token price as Sonnet, far fewer tokens per task):**

| Model | Effort | Input / output | Combined 3:1 | AA cost/task | AA Index | Use at Meister |
|---|---|---|---|---|---|---|
| GPT-6.1 Sol | low | 2.00 / 10.00 | **4.00** | 0.13 | 42 | — |
| GPT-6.1 Sol | medium | 2.00 / 10.00 | **4.00** | 0.21 | 48 | — |
| GPT-6.1 Sol | high | 2.00 / 10.00 | **4.00** | 0.32 | 50 | — |
| GPT-6.1 Sol | xhigh | 2.00 / 10.00 | **4.00** | 0.39 | 51 | — |
| GPT-6.1 Sol | max | 2.00 / 10.00 | **4.00** | 0.72 | 52 | — |

**More expensive (reference):**

| Model | Effort | Input / output | Combined 3:1 | AA cost/task | AA Index | Use at Meister |
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

**Inexpensive reference models (no harness in Meister; CLI availability unverified):**

| Model | Effort | Input / output | Combined 3:1 | AA cost/task | AA Index | Use at Meister |
|---|---|---|---|---|---|---|
| Claude 4.5 Haiku | reasoning | 1.00 / 5.00 | **2.00** | 0.28 | 17 | — |
| GLM-5.3-Flash | - | 0.15 / 0.50 | **0.2375** | 0.25 | 42 | — |
| Qwen3.8-Flash-Next | - | 0.15 / 0.47 | **0.23** | 0.37 | 40 | — |
| DeepSeek V4.1 Flash | max | 0.30 / 1.20 | **0.525** | 0.27 | 39 | — |
| MiMo-V2.6-Flash | - | 0.14 / 0.28 | **0.175** | 0.06 | 38 | — |
| Gemini 3.5 Flash-Lite | - | 0.30 / 2.50 | **0.85** | 0.12 | 22 | — |

### Useful takeaways
- Luna is about **37 times** cheaper per task than Sonnet 5.5 high (0.03 versus 1.12): it remains the first lane.
- On Artificial Analysis, **Gemini 3.8 Flash high costs more per task than Sonnet 5.5 high** (1.24 versus 1.12), even though its tokens cost
  2.7 times less, because it uses more reasoning tokens. The subscription subsidy offsets this for the owner; without the subsidy, the calculation would change.
- **Gemini 3.8 Flash medium** costs 25% less per task than high (0.93 versus 1.24) with a nearly equal index (40 versus 41).
- **GPT-6.1 Sol low** has an index of 42 at 0.13 per task, compared with 41 at 1.24 for Gemini high: worth studying as a candidate, if available through Copilot (unverified).
- Inexpensive reference models have an index of 38 to 42 at 0.06 to 0.37 per task, but have no harness in Meister.

## Observed actual usage (test project, run 2, 2026-10-04)
| Lane | Tokens (input / output) | List price, without cache | Catalog (combined × tokens) | Actually charged |
|---|---|---|---|---|
| `copilot_luna` (9 tasks) | 2.271.700 / 41.200 | US$ 0,248 | US$ 0,463 | **US$ 0,072** (7,16 credits) |
| `agy_gemini_flash` (1 task) | 288.094 / 20.571 | US$ 0,293 | US$ 0,463 | not measured (subscription) |

The catalog **overestimates** spending: actual usage is about 98% input, and most of it is read cache (US$ 0.01 per 1M on Luna), which the combined price
does not account for. For Copilot, credits × US$ 0.01 is the accurate measure. Each call, even a trivial one, uses 15 to 29 thousand input tokens
(project context).
`meister report` and the dashboard now calculate Copilot's cost from the credits recorded by the CLI.
The per-credit price (`credit_usd`) is in the configuration's lane catalog; the catalog estimate is not used when credits are available.

## Gaps
- The Artificial Analysis **Coding Agent Index** was not collected (only the Intelligence Index and cost per task were collected).
- Luna's **actual effort** in Copilot and Codex is unconfirmed (documentation said medium; the Codex sample showed `none`), and worker
  `claude_sonnet` uses the CLI default.
- **Model availability on each harness** (Copilot, Codex, agy, claude) was not verified.
- Artificial Analysis cost per task is from a reasoning benchmark.

## How to update
1. **Per-token prices (OpenRouter):**
   `curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys;[print(m['id'],m['pricing']) for m in json.load(sys.stdin)['data'] if 'luna' in m['id']]"`
   (multiply by 1e6; the `/api/v1/models/<id>/endpoints` endpoint provides per-provider prices, as with Jev).
2. **Cost per task and index (Artificial Analysis):** the page is dynamic; open the leaderboard table in a browser and select "Expand columns".
3. Update the CSV and this table, and `cost_per_m_tokens` in the catalog if the combined price changes. The `test_model_table_matches_catalog` test
   (`tests/test_hygiene.py`) fails if the table and catalog diverge.
