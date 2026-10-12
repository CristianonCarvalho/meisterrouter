#!/bin/sh
# Cria o projeto de exemplo "shop" em $1
set -e
D="$1"; rm -rf "$D"; mkdir -p "$D"; cd "$D"
git init -q -b main
printf '__pycache__/\n.pytest_cache/\n.meister/\nplan.json\n' > .gitignore
printf '' > conftest.py
printf 'def hello():\n    return "shop"\n' > shop.py
cat > meister.config.yaml <<'YAML'
router:
  mode: first
runtime:
  host: process
gate:
  commands:
    - name: tests
      run: "python3 -m pytest -q -p no:cacheprovider"
      timeout_seconds: 120
      required: true
workers:
  tier_order:
    - {name: tier_1, harness: copilot, model: claude-haiku-5.5, cost_per_m_tokens: 0.20, max_retries: 1}
    - {name: tier_2, harness: agy, model: gemini-3.8-flash-high, cost_per_m_tokens: 1.50, max_retries: 1}
    - {name: tier_3, harness: claude, model: sonnet, cost_per_m_tokens: 4.00, max_retries: 1}
YAML
git add -A; git -c user.name=demo -c user.email=demo@example.com commit -q -m "initial commit"
