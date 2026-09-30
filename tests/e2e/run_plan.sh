#!/usr/bin/env bash
# E2E do fluxo de plano do MeisterRouter (superpowers -> plan import -> validate -> orchestrate --plan-file).
#
#  P0  Rápido (sem LLM): import de plano inválido sai com rc != 0 citando a task,
#      o mesmo plano com --allow-unscoped sai com rc 0;
#      orchestrate com texto livre sai com rc != 0 (2) e NÃO cria run no SQLite nem emite worker_spawn.
#  P1  Real (luna/gemini): plano superpowers canônico (mul + shout), import (-o plan.json),
#      validate plan.json, orchestrate --plan-file plan.json, retomada do mesmo comando (idempotência),
#      checagens determinísticas de git, pytest, eventos JSONL e limpeza de recursos.
#  P2  Real (luna/gemini): dependência real entre tarefas sem arquivo em comum (mul em calc.py
#      e square em geo.py dependendo de mul); valida import sequencial com target_files disjuntos,
#      ordem temporal de worker_spawn da task_2 após subtask_completed da task_1, integridade e limpeza.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
preflight

LOG="$WORK/e2e_plan.log"
exec > >(tee "$LOG") 2>&1

echo "meister: $(command -v meister)   inicio: $(date +%H:%M:%S)"

# =============================================================================================
echo; echo "################ P0: validacao rapida sem LLM (import invalido + orchestrate texto livre) ################"
P0="$WORK/e2e_plan_p0"; ST0="$WORK/e2e_plan_state/p0"
rm -rf "$ST0"; mkdir -p "$ST0/logs" "$ST0/wt"
make_disposable_repo "$P0"
export MEISTER_LOG_DIR="$ST0/logs" MEISTER_WORKTREES_DIR="$ST0/wt"
cd "$P0" || exit 1

cat <<'EOF' > invalid_plan.md
# Plano Invalido Implementation Plan

**Goal:** Testar rejeicao de plano sem bloco Files.

## Global Constraints
- Python 3, sem dependencias externas.

### Task 1: tarefa_sem_files
Adicione mul em calc.py sem especificar o bloco Files.
EOF

RC_P0_INV=0
OUT_P0_INV=$(meister plan import --format superpowers invalid_plan.md 2>&1) || RC_P0_INV=$?

RC_P0_UNSCOPED=0
meister plan import --format superpowers --allow-unscoped invalid_plan.md >/dev/null 2>&1 || RC_P0_UNSCOPED=$?

count_sqlite_runs() {
  local repo_dir="$1"
  python3 - "$repo_dir/.meister/meister.db" <<'PYEOF'
import os, sqlite3, sys
db = sys.argv[1]
if not os.path.exists(db):
    print(0)
    sys.exit(0)
try:
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='runs'")
    if cur.fetchone()[0] == 0:
        print(0)
    else:
        cur.execute("SELECT count(*) FROM runs")
        print(cur.fetchone()[0])
    conn.close()
except Exception:
    print(0)
PYEOF
}

RUNS_BEFORE=$(count_sqlite_runs "$P0")

RC_P0_ORCH=0
meister orchestrate --task "faça isso e aquilo" > "$ST0/orch_freeform.out" 2>&1 || RC_P0_ORCH=$?

RUNS_AFTER=$(count_sqlite_runs "$P0")

TOTAL_SPAWNS_P0="$(python3 -c 'import json, sys, os; f=sys.argv[1]; print(sum(1 for l in open(f) if json.loads(l).get("event")=="worker_spawn" or json.loads(l).get("event_type")=="worker_spawn") if os.path.exists(f) else 0)' "$MEISTER_LOG_DIR/orchestration_log.jsonl")"

echo "P0: rc_inv=$RC_P0_INV  rc_unscoped=$RC_P0_UNSCOPED  rc_orch=$RC_P0_ORCH  runs_before=$RUNS_BEFORE  runs_after=$RUNS_AFTER  spawns=$TOTAL_SPAWNS_P0"
echo "== estado final P0"; repo_state "$P0"

check "P0-a import de plano sem Files sai com rc != 0 e cita task" '[ "$RC_P0_INV" != 0 ] && echo "$OUT_P0_INV" | grep -qi "task_1"'
check "P0-b import com --allow-unscoped sai com rc 0"             '[ "$RC_P0_UNSCOPED" = 0 ]'
check "P0-c orchestrate com texto livre sai com rc != 0 (2)"       '[ "$RC_P0_ORCH" = 2 ]'
check "P0-d orchestrate com texto livre NAO cria run no SQLite"   '[ "$RUNS_BEFORE" = 0 ] && [ "$RUNS_AFTER" = 0 ]'
check "P0-e zero worker_spawn no JSONL"                           '[ "$TOTAL_SPAWNS_P0" = 0 ]'

# =============================================================================================
echo; echo "################ P1: fluxo de plano real (import -> validate -> orchestrate -> retomada) ################"
P1="$WORK/e2e_plan_p1"; ST1="$WORK/e2e_plan_state/p1"
rm -rf "$ST1"; mkdir -p "$ST1/logs" "$ST1/wt"
make_disposable_repo "$P1"
export MEISTER_LOG_DIR="$ST1/logs" MEISTER_WORKTREES_DIR="$ST1/wt"
cd "$P1" || exit 1

cat <<'EOF' > plan.md
# Plano E2E Implementation Plan

**Goal:** Implementar funcoes mul e shout com testes automatizados.

## Global Constraints
- Python 3, sem dependências externas.

### Task 1: mul
**Files:**
- Modify: `calc.py`
- Test: `tests/test_calc.py`

Adicione mul(a, b) que retorna a*b em calc.py e test_mul em tests/test_calc.py. Nao altere outros arquivos.

### Task 2: shout
**Files:**
- Modify: `text.py`
- Test: `tests/test_text.py`

Adicione shout(name) que retorna name.upper() em text.py e test_shout em tests/test_text.py. Nao altere outros arquivos.
EOF

echo "== passo 1: meister plan import $(date +%H:%M:%S)"
RC_P1_IMPORT=0
meister plan import --format superpowers plan.md -o plan.json > "$ST1/import.out" 2>&1 || RC_P1_IMPORT=$?

echo "== passo 2: meister plan validate $(date +%H:%M:%S)"
RC_P1_VALIDATE=0
meister plan validate plan.json > "$ST1/validate.out" 2>&1 || RC_P1_VALIDATE=$?

echo "== passo 3: meister orchestrate --plan-file #1 $(date +%H:%M:%S)"
RC_P1_ORCH1=0
meister orchestrate --plan-file plan.json > "$ST1/run1.out" 2>&1 || RC_P1_ORCH1=$?
T1_SPAWN_1="$(cnt "$MEISTER_LOG_DIR" task_1 worker_spawn)"
T2_SPAWN_1="$(cnt "$MEISTER_LOG_DIR" task_2 worker_spawn)"

echo "== passo 4: meister orchestrate --plan-file #2 (retomada/idempotencia) $(date +%H:%M:%S)"
RC_P1_ORCH2=0
meister orchestrate --plan-file plan.json > "$ST1/run2.out" 2>&1 || RC_P1_ORCH2=$?
T1_SPAWN_2="$(cnt "$MEISTER_LOG_DIR" task_1 worker_spawn)"
T2_SPAWN_2="$(cnt "$MEISTER_LOG_DIR" task_2 worker_spawn)"

echo "== estado final P1"; repo_state "$P1"
echo "== eventos P1"; timeline "$MEISTER_LOG_DIR"

verify_p1_plan_json() {
  [ "$RC_P1_IMPORT" = 0 ] || return 1
  python3 -c '
import json, sys
try:
    d = json.load(open("plan.json"))
    assert len(d) == 2
    assert d[0].get("id") == "task_1" and d[0].get("depends_on") == []
    assert d[1].get("id") == "task_2" and d[1].get("depends_on") == ["task_1"]
except Exception:
    sys.exit(1)
'
}

check "P1-a import rc 0 e plan.json com 2 tarefas e dependencias corretas" 'verify_p1_plan_json'
check "P1-b validate saiu com rc 0"                       '[ "$RC_P1_VALIDATE" = 0 ]'
check "P1-c orchestrate saiu com rc 0"                    '[ "$RC_P1_ORCH1" = 0 ]'
check "P1-d main tem def mul exatamente 1x em calc.py"    '[ "$(git -C "$P1" show main:calc.py | grep -c "def mul")" = 1 ]'
check "P1-e main tem def shout exatamente 1x em text.py"  '[ "$(git -C "$P1" show main:text.py | grep -c "def shout")" = 1 ]'
check "P1-f pytest passa na main"                         '(cd "$P1" && "$PY" -m pytest -q)'
check "P1-g eventos subtask_completed para task_1 e task_2 no JSONL" '[ "$(cnt "$MEISTER_LOG_DIR" task_1 subtask_completed)" -ge 1 ] && [ "$(cnt "$MEISTER_LOG_DIR" task_2 subtask_completed)" -ge 1 ]'
check "P1-h worker_spawn de cada task == 1 apos primeiro orchestrate" '[ "$T1_SPAWN_1" = 1 ] && [ "$T2_SPAWN_1" = 1 ]'
check "P1-i retomada (passo 4) saiu com rc 0"             '[ "$RC_P1_ORCH2" = 0 ]'
check "P1-j retomada NAO criou novos worker_spawn"        '[ "$T1_SPAWN_2" = 1 ] && [ "$T2_SPAWN_2" = 1 ]'
check "P1-k sem worktrees restantes"                      '[ "$(git -C "$P1" worktree list | wc -l | tr -d " ")" = 1 ]'
check "P1-l sem branches meister/worktree/* e meister/integration/*" '[ -z "$(git -C "$P1" branch --list "meister/worktree/*" "meister/integration/*")" ]'
check "P1-m sem tabs worker:* restantes"                  '[ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

# =============================================================================================
echo; echo "################ P2: dependencia real entre tasks sem arquivo em comum ################"
P2="$WORK/e2e_plan_p2"; ST2="$WORK/e2e_plan_state/p2"
rm -rf "$ST2"; mkdir -p "$ST2/logs" "$ST2/wt"
make_disposable_repo "$P2"
export MEISTER_LOG_DIR="$ST2/logs" MEISTER_WORKTREES_DIR="$ST2/wt"
cd "$P2" || exit 1

cat <<'EOF' > plan.md
# Plano Dependencia Implementation Plan

**Goal:** mul em calc.py e square em geo.py, que depende de mul.

## Global Constraints
- Python 3, sem dependências externas.

### Task 1: mul
**Files:**
- Modify: `calc.py`
- Test: `tests/test_calc.py`

Adicione mul(a, b) que retorna a*b em calc.py e test_mul em tests/test_calc.py. Nao altere outros arquivos.

### Task 2: square
**Files:**
- Create: `geo.py`
- Test: `tests/test_geo.py`

Crie geo.py com a funcao square(x) que retorna mul(x, x), usando 'from calc import mul' (a funcao mul e criada por outra tarefa em calc.py; NAO a reimplemente nem altere calc.py). Crie tests/test_geo.py com test_square verificando square(3) == 9. Nao altere outros arquivos.
EOF

echo "== passo 1: meister plan import $(date +%H:%M:%S)"
RC_P2_IMPORT=0
meister plan import --format superpowers plan.md -o plan.json > "$ST2/import.out" 2>&1 || RC_P2_IMPORT=$?

echo "== passo 2: meister plan validate $(date +%H:%M:%S)"
RC_P2_VALIDATE=0
meister plan validate plan.json > "$ST2/validate.out" 2>&1 || RC_P2_VALIDATE=$?

echo "== passo 3: meister orchestrate --plan-file $(date +%H:%M:%S)"
RC_P2_ORCH=0
meister orchestrate --plan-file plan.json > "$ST2/run.out" 2>&1 || RC_P2_ORCH=$?
T1_SPAWN_P2="$(cnt "$MEISTER_LOG_DIR" task_1 worker_spawn)"
T2_SPAWN_P2="$(cnt "$MEISTER_LOG_DIR" task_2 worker_spawn)"

echo "== estado final P2"; repo_state "$P2"
echo "== eventos P2"; timeline "$MEISTER_LOG_DIR"

verify_p2_plan_json() {
  [ "$RC_P2_IMPORT" = 0 ] || return 1
  python3 -c '
import json, sys
try:
    d = json.load(open("plan.json"))
    assert len(d) == 2
    by_id = {t["id"]: t for t in d}
    t1 = by_id["task_1"]
    t2 = by_id["task_2"]
    assert t1.get("depends_on") == []
    assert t2.get("depends_on") == ["task_1"]
    f1 = set(t1.get("target_files", []))
    f2 = set(t2.get("target_files", []))
    assert len(f1) > 0 and len(f2) > 0
    assert f1.isdisjoint(f2)
except Exception:
    sys.exit(1)
'
}

verify_p2_order() {
  python3 - "$MEISTER_LOG_DIR/orchestration_log.jsonl" <<'PYEOF'
import json, sys
from datetime import datetime

t1_done = None
t2_spawn = None

try:
    for line in open(sys.argv[1]):
        try:
            e = json.loads(line)
        except Exception:
            continue
        tid = e.get("task_id")
        ev = e.get("event") or e.get("event_type")
        ts = e.get("ts") or e.get("timestamp")
        if not ts:
            continue
        if tid == "task_1" and ev == "subtask_completed":
            t1_done = ts
        elif tid == "task_2" and ev == "worker_spawn" and t2_spawn is None:
            t2_spawn = ts

    if not t1_done or not t2_spawn:
        sys.exit(1)

    d1 = datetime.fromisoformat(t1_done.replace("Z", "+00:00"))
    d2 = datetime.fromisoformat(t2_spawn.replace("Z", "+00:00"))
    assert d2 > d1
except Exception:
    sys.exit(1)
PYEOF
}

check "P2-a import rc 0, plan.json com 2 tarefas, dependencias e target_files disjuntos" 'verify_p2_plan_json'
check "P2-b validate rc 0 e orchestrate rc 0"                    '[ "$RC_P2_VALIDATE" = 0 ] && [ "$RC_P2_ORCH" = 0 ]'
check "P2-c worker_spawn da task_2 ocorreu apos subtask_completed da task_1" 'verify_p2_order'
check "P2-d main tem geo.py contendo from calc import mul e def square" 'git -C "$P2" show main:geo.py | grep -q "from calc import mul" && git -C "$P2" show main:geo.py | grep -q "def square"'
check "P2-e main tem def mul exatamente 1x em calc.py"           '[ "$(git -C "$P2" show main:calc.py | grep -c "def mul")" = 1 ]'
check "P2-f pytest passa na main"                                '(cd "$P2" && "$PY" -m pytest -q)'
check "P2-g worker_spawn de cada task == 1"                     '[ "$T1_SPAWN_P2" = 1 ] && [ "$T2_SPAWN_P2" = 1 ]'
check "P2-h sem worktrees, branches temporarias ou tabs restantes" '[ "$(git -C "$P2" worktree list | wc -l | tr -d " ")" = 1 ] && [ -z "$(git -C "$P2" branch --list "meister/worktree/*" "meister/integration/*")" ] && [ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

print_summary_and_exit
