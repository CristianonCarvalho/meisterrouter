#!/usr/bin/env bash
# E2E real do MeisterRouter: classify -> worker recomendado -> fallback -> gate -> control.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
preflight

LOG="$WORK/e2e_run_flow.log"
exec > >(tee "$LOG") 2>&1

REPO="$WORK/e2e_flow_repo"
export MEISTER_LOG_DIR="$WORK/e2e_flow_state/logs"
export MEISTER_WORKTREES_DIR="$WORK/e2e_flow_state/wt"
rm -rf "$REPO" "$WORK/e2e_flow_state"
mkdir -p "$MEISTER_LOG_DIR" "$MEISTER_WORKTREES_DIR"

step "0. setup repo descartavel"
make_disposable_repo "$REPO"
cd "$REPO" || exit 1
"$PY" -m pytest -q 2>&1 | tail -1
git log --oneline

TASK="Adicione a função mul(a, b) em calc.py que retorna a*b, e um teste test_mul em tests/test_calc.py. Não altere outros arquivos."
FILES="calc.py,tests/test_calc.py"

step "1. Jev classify"
CLS=$("$MR/bin/meister" classify --context "$TASK")
echo "$CLS"
RECOMMENDED=$(printf '%s' "$CLS" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("recommended_implementer",""))')
CHAIN=$(printf '%s' "$CLS" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(" ".join([d.get("recommended_implementer","")]+d.get("fallback_chain",[])))')
RUN_ID=$(printf '%s' "$CLS" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("run_id",""))')
TASK_ID=$(printf '%s' "$CLS" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("task_id",""))')
echo "recomendado: $RECOMMENDED   cadeia: $CHAIN"

step "2. worker com fallback deterministico"
USED=""; ATTEMPTS=0
for M in $CHAIN; do
  [ "$M" = "sonnet" ] && { echo "parando antes de sonnet (economia de cota Claude)"; break; }
  [ "$M" = "opus" ] && { echo "parando antes de opus (economia de cota Claude)"; break; }
  [ "$M" = "haiku" ] && { echo "parando antes de haiku (economia de cota Claude)"; break; }
  ATTEMPTS=$((ATTEMPTS+1))
  step "2.$ATTEMPTS meister worker --model $M"
  if "$MR/bin/meister" worker --model "$M" --task "$TASK" --files "$FILES" --cwd "$REPO" --run-id "$RUN_ID" --task-id "$TASK_ID"; then
    USED="$M"; echo ">> worker $M: SUCESSO"; break
  else
    echo ">> worker $M: FALHOU (rc=$?) -> proximo da cadeia"
  fi
done
echo "worker usado: ${USED:-NENHUM}"

step "3. evidencia deterministica"
cd "$REPO"
git status --short
git diff --stat
git log --oneline --all | head -10
git worktree list
PYRC=0
"$PY" -m pytest -q 2>&1 | tail -3 || PYRC=$?
TR=$([ "$PYRC" = 0 ] && echo pass || echo fail)
grep -n "def mul" calc.py || true
grep -n "def test_mul" tests/test_calc.py || true

step "4. Jev control"
DIFF_SUM="$(git diff --stat HEAD~1 HEAD 2>/dev/null | tr '\n' ' ' || echo 'diff')"
CTRL_OUT=$("$MR/bin/meister" control --diff-summary "$DIFF_SUM worker=$USED" --test-result "$TR" --attempts "$ATTEMPTS" --run-id "$RUN_ID" --task-id "$TASK_ID" --no-close-worker)
echo "$CTRL_OUT"

step "5. telemetria JSONL"
ls -la "$MEISTER_LOG_DIR" "$MEISTER_WORKTREES_DIR" 2>&1 || true
timeline "$MEISTER_LOG_DIR"

step "6. avaliacao das assercoes"
check "flow-a classify devolveu recomendado + cadeia" '[ -n "$RECOMMENDED" ] && [ -n "$CHAIN" ]'
check "flow-b algum worker da cadeia concluiu" '[ -n "$USED" ]'
check "flow-c main tem def mul e test_mul" 'git -C "$REPO" show main:calc.py | grep -q "def mul" && git -C "$REPO" show main:tests/test_calc.py | grep -q "def test_mul"'
check "flow-d pytest passa na main" '(cd "$REPO" && "$PY" -m pytest -q)'
check "flow-e control respondeu COMPLETE" 'echo "$CTRL_OUT" | grep -qi "COMPLETE"'
check "flow-f eventos JSONL com mesmo run_id e worker_start/end" 'python3 - "$MEISTER_LOG_DIR" <<'"'"'PYEOF'"'"'
import glob, json, sys
events = []
for p in glob.glob(sys.argv[1] + "/*.jsonl"):
    with open(p) as f:
        for l in f:
            if l.strip():
                events.append(json.loads(l))
assert len(events) > 0, "Sem eventos"
run_ids = set(e.get("run_id") for e in events if e.get("run_id"))
assert len(run_ids) == 1, f"Multiplos run_ids: {run_ids}"
types = set(e.get("event") or e.get("event_type") for e in events)
assert "worker_start" in types, f"worker_start ausente em {types}"
assert "worker_end" in types, f"worker_end ausente em {types}"
PYEOF'
check "flow-g sem worktrees/branches/tabs restantes" '[ "$(git -C "$REPO" worktree list | wc -l | tr -d " ")" = 1 ] && [ -z "$(git -C "$REPO" branch --list "meister/worktree/*")" ] && [ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

if [ -n "${E2E_LUNA_MODEL:-}" ] && [ "$E2E_LUNA_MODEL" != "gpt-6-luna" ]; then
  check "flow-h worker usado NAO foi a luna (fallback executado)" '[ "$USED" != "luna" ]'
fi

print_summary_and_exit
