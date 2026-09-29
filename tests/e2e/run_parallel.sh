#!/usr/bin/env bash
# E2E paralelo: meister orchestrate com 2 subtarefas independentes (arquivos disjuntos).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
preflight

LOG="$WORK/e2e_parallel.log"
exec > >(tee "$LOG") 2>&1

REPO="$WORK/e2e_par_repo"
export MEISTER_LOG_DIR="$WORK/e2e_par_state/logs"
export MEISTER_WORKTREES_DIR="$WORK/e2e_par_state/wt"
rm -rf "$REPO" "$WORK/e2e_par_state"
mkdir -p "$MEISTER_LOG_DIR" "$MEISTER_WORKTREES_DIR"

step "0. setup repo descartavel"
make_disposable_repo "$REPO"
cd "$REPO" || exit 1

PLAN='[{"id":"t1","description":"Adicione mul(a, b) que retorna a*b em calc.py e test_mul em tests/test_calc.py. Não altere outros arquivos.","target_files":["calc.py","tests/test_calc.py"],"depends_on":[]},{"id":"t2","description":"Adicione shout(name) que retorna name.upper() em text.py e test_shout em tests/test_text.py. Não altere outros arquivos.","target_files":["text.py","tests/test_text.py"],"depends_on":[]}]'

SAMPLER_LOG="$WORK/e2e_par_herdr.log"
python3 "$SCRIPT_DIR/herdr_sampler.py" > "$SAMPLER_LOG" 2>&1 &
SAMPLER=$!

step "1. orchestrate paralelo"
RC=0
"$MR/bin/meister" orchestrate --task "$PLAN" || RC=$?
echo "orchestrate finalizado com rc=$RC"

sleep 3
kill "$SAMPLER" 2>/dev/null || true
wait "$SAMPLER" 2>/dev/null || true

step "2. estado do repo e evidencias"
repo_state "$REPO"
cat "$SAMPLER_LOG"
timeline "$MEISTER_LOG_DIR"

step "3. checagens"
check "par-a rc 0" '[ "$RC" = 0 ]'
check "par-b t1 e t2 rodaram de forma sobreposta" 'python3 - "$MEISTER_LOG_DIR/orchestration_log.jsonl" <<'"'"'PYEOF'"'"'
import json, sys
starts = {}
ends = {}
for line in open(sys.argv[1]):
    try:
        e = json.loads(line)
    except Exception:
        continue
    tid = e.get("task_id")
    ev = e.get("event") or e.get("event_type")
    ts = e.get("ts")
    if tid in ("t1", "t2") and ts:
        if ev == "worker_task_start" and tid not in starts:
            starts[tid] = ts
        elif ev == "worker_task_end" and tid not in ends:
            ends[tid] = ts
assert "t1" in starts and "t2" in starts, f"starts incompletos: {starts}"
assert "t1" in ends and "t2" in ends, f"ends incompletos: {ends}"
assert max(starts["t1"], starts["t2"]) < min(ends["t1"], ends["t2"]), f"Execucao nao sobreposta: starts={starts} ends={ends}"
PYEOF'
check "par-c main tem def mul e def shout (uma vez cada)" '[ "$(git -C "$REPO" show main:calc.py | grep -c "def mul")" = 1 ] && [ "$(git -C "$REPO" show main:text.py | grep -c "def shout")" = 1 ]'
check "par-d pytest passa" '(cd "$REPO" && "$PY" -m pytest -q)'
check "par-e dois commits de merge em serie" '[ "$(git -C "$REPO" log --grep="Merge subtask" --oneline | wc -l | tr -d " ")" = 2 ]'
check "par-f sampler viu tabs worker:t1/t2 e panes cwd com _t1/_t2" 'grep -q "worker:t1" "$SAMPLER_LOG" && grep -q "worker:t2" "$SAMPLER_LOG" && grep -E "cwd=.*_t1" "$SAMPLER_LOG" && grep -E "cwd=.*_t2" "$SAMPLER_LOG"'
check "par-g sem worktrees/branches/tabs restantes" '[ "$(git -C "$REPO" worktree list | wc -l | tr -d " ")" = 1 ] && [ -z "$(git -C "$REPO" branch --list "meister/worktree/*")" ] && [ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

print_summary_and_exit
