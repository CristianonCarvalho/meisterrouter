#!/usr/bin/env bash
# E2E real: plano em CADEIA de 6 tarefas (escala) — run_chain.sh
# Prova que o MeisterRouter aguenta 6 tarefas encadeadas, cada uma sobre o resultado
# integrado da anterior, com workers reais, terminando com os testes passando na main.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
preflight

LOG="$WORK/e2e_chain.log"
exec > >(tee "$LOG") 2>&1

echo "meister: $(command -v meister)   inicio: $(date +%H:%M:%S)"

CHAIN_REPO="$WORK/e2e_chain"
CHAIN_STATE="$WORK/e2e_chain_state"
rm -rf "$CHAIN_STATE"
mkdir -p "$CHAIN_STATE/logs" "$CHAIN_STATE/wt"
make_disposable_repo "$CHAIN_REPO"

export MEISTER_LOG_DIR="$CHAIN_STATE/logs"
export MEISTER_WORKTREES_DIR="$CHAIN_STATE/wt"
cd "$CHAIN_REPO" || exit 1

cat <<'EOF' > plan.md
# Plano Cadeia Implementation Plan

**Goal:** calc, geo e report encadeados, cada um usando o anterior.

## Global Constraints
- Python 3, sem dependências externas.

### Task 1: mul
**Files:**
- Modify: `calc.py`
- Test: `tests/test_calc.py`

Adicione mul(a, b) que retorna a*b em calc.py e test_mul em tests/test_calc.py. Nao altere outros arquivos.

### Task 2: square
**Files:**
- Modify: `calc.py`
- Test: `tests/test_calc.py`

Adicione square(x) que retorna mul(x, x) em calc.py (mul ja existe, NAO a reimplemente) e test_square em tests/test_calc.py. Nao altere outros arquivos.

### Task 3: area_square
**Files:**
- Create: `geo.py`
- Test: `tests/test_geo.py`

Crie geo.py com area_square(side) que retorna square(side), usando 'from calc import square' (square ja existe em calc.py; NAO a reimplemente nem altere calc.py). Crie tests/test_geo.py com test_area_square verificando area_square(3) == 9. Nao altere outros arquivos.

### Task 4: area_rect
**Files:**
- Modify: `geo.py`
- Test: `tests/test_geo.py`

Adicione a geo.py a funcao area_rect(w, h) que retorna mul(w, h), usando 'from calc import mul' (mul ja existe em calc.py; NAO a reimplemente). Adicione test_area_rect em tests/test_geo.py verificando area_rect(2, 5) == 10. Nao altere outros arquivos.

### Task 5: describe
**Files:**
- Create: `report.py`
- Test: `tests/test_report.py`

Crie report.py com describe(side) que retorna a string f"{side}:{area_square(side)}", usando 'from geo import area_square' (ja existe em geo.py; NAO a reimplemente). Crie tests/test_report.py com test_describe verificando describe(3) == "3:9". Nao altere outros arquivos.

### Task 6: describe_all
**Files:**
- Modify: `report.py`
- Test: `tests/test_report.py`

Adicione a report.py a funcao describe_all(sides) que retorna as descricoes separadas por "|", usando describe (ja existe em report.py; NAO a reimplemente): describe_all([1, 2]) == "1:1|2:4". Adicione test_describe_all em tests/test_report.py. Nao altere outros arquivos.
EOF

echo "== passo 1: meister plan import $(date +%H:%M:%S)"
RC_CHAIN_IMPORT=0
meister plan import --format superpowers plan.md -o plan.json > "$CHAIN_STATE/import.out" 2>&1 || RC_CHAIN_IMPORT=$?

echo "== passo 2: meister plan validate $(date +%H:%M:%S)"
RC_CHAIN_VALIDATE=0
meister plan validate plan.json > "$CHAIN_STATE/validate.out" 2>&1 || RC_CHAIN_VALIDATE=$?

echo "== passo 3: meister orchestrate --plan-file #1 $(date +%H:%M:%S)"
RC_CHAIN_ORCH1=0
meister orchestrate --plan-file plan.json > "$CHAIN_STATE/run1.out" 2>&1 || RC_CHAIN_ORCH1=$?

echo "== passo 4: meister orchestrate --plan-file #2 (idempotencia) $(date +%H:%M:%S)"
RC_CHAIN_ORCH2=0
meister orchestrate --plan-file plan.json > "$CHAIN_STATE/run2.out" 2>&1 || RC_CHAIN_ORCH2=$?

echo "== estado final cadeia"; repo_state "$CHAIN_REPO"
echo "== eventos cadeia"; timeline "$MEISTER_LOG_DIR"

print_chain_timings() {
  python3 - "$MEISTER_LOG_DIR/orchestration_log.jsonl" <<'PYEOF'
import json, sys
from datetime import datetime

spawns = {}
completed = {}
all_ts = []

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
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        all_ts.append(dt)
        if ev == "worker_spawn" and tid and tid not in spawns:
            spawns[tid] = dt
        elif ev == "subtask_completed" and tid:
            completed[tid] = dt

    durations = []
    for n in range(1, 7):
        tid = f"task_{n}"
        if tid in spawns and tid in completed:
            dur = (completed[tid] - spawns[tid]).total_seconds()
            durations.append(f"{tid}: {dur:.1f}s")
        else:
            durations.append(f"{tid}: N/D")

    total_dur = (max(all_ts) - min(all_ts)).total_seconds() if all_ts else 0.0
    print(f"Tempo total medido: {total_dur:.1f}s | Tarefas: {', '.join(durations)}")
except Exception as exc:
    print(f"(Nao foi possivel calcular tempos: {exc})")
PYEOF
}

echo
echo "== tempos informativos da cadeia:"
print_chain_timings
echo

verify_chain_plan_json() {
  [ "$RC_CHAIN_IMPORT" = 0 ] || return 1
  python3 -c '
import json, sys
try:
    d = json.load(open("plan.json"))
    assert len(d) == 6
    by_id = {t["id"]: t for t in d}
    for n in range(1, 7):
        tid = f"task_{n}"
        assert tid in by_id
        t = by_id[tid]
        if n == 1:
            assert t.get("depends_on") == []
        else:
            assert t.get("depends_on") == [f"task_{n-1}"]

    t1_files = sorted(by_id["task_1"].get("target_files", []))
    t2_files = sorted(by_id["task_2"].get("target_files", []))
    t3_files = sorted(by_id["task_3"].get("target_files", []))
    t4_files = sorted(by_id["task_4"].get("target_files", []))
    t5_files = sorted(by_id["task_5"].get("target_files", []))
    t6_files = sorted(by_id["task_6"].get("target_files", []))

    assert t1_files == ["calc.py", "tests/test_calc.py"]
    assert t1_files == t2_files
    assert t3_files == ["geo.py", "tests/test_geo.py"]
    assert t3_files == t4_files
    assert t5_files == ["report.py", "tests/test_report.py"]
    assert t5_files == t6_files
except Exception as exc:
    print(f"Erro na verificacao de plan.json: {exc}", file=sys.stderr)
    sys.exit(1)
'
}

verify_sqlite_completed() {
  local repo_dir="$1"
  python3 - "$repo_dir/.meister/meister.db" <<'PYEOF'
import os, sqlite3, sys
db = sys.argv[1]
if not os.path.exists(db):
    print(f"Banco nao encontrado: {db}", file=sys.stderr)
    sys.exit(1)
try:
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT state FROM runs")
    runs = cur.fetchall()
    if len(runs) != 1 or runs[0][0] != "COMPLETED":
        print(f"Runs inesperado: {runs}", file=sys.stderr)
        sys.exit(1)

    cur.execute("SELECT step_id, status, integrated_sha FROM subtasks ORDER BY step_id")
    subs = cur.fetchall()
    if len(subs) != 6:
        print(f"Esperava 6 subtarefas no SQLite, encontrou {len(subs)}", file=sys.stderr)
        sys.exit(1)

    for step_id, status, sha in subs:
        if status != "COMPLETED":
            print(f"Subtarefa {step_id} status={status} != COMPLETED", file=sys.stderr)
            sys.exit(1)
        if not sha or not sha.strip():
            print(f"Subtarefa {step_id} sem integrated_sha", file=sys.stderr)
            sys.exit(1)
    conn.close()
except Exception as exc:
    print(f"Erro SQLite: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

verify_chain_order() {
  python3 - "$MEISTER_LOG_DIR/orchestration_log.jsonl" <<'PYEOF'
import json, sys
from datetime import datetime

spawns = {}
completed = {}

try:
    for line in open(sys.argv[1]):
        try:
            e = json.loads(line)
        except Exception:
            continue
        tid = e.get("task_id")
        ev = e.get("event") or e.get("event_type")
        ts = e.get("ts") or e.get("timestamp")
        if not ts or not tid:
            continue
        if ev == "worker_spawn" and tid not in spawns:
            spawns[tid] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif ev == "subtask_completed":
            completed[tid] = datetime.fromisoformat(ts.replace("Z", "+00:00"))

    for n in range(2, 7):
        prev_id = f"task_{n-1}"
        curr_id = f"task_{n}"
        if prev_id not in completed:
            print(f"{prev_id} nao possui subtask_completed", file=sys.stderr)
            sys.exit(1)
        if curr_id not in spawns:
            print(f"{curr_id} nao possui worker_spawn", file=sys.stderr)
            sys.exit(1)
        if spawns[curr_id] <= completed[prev_id]:
            print(f"Ordem violada: {curr_id} spawn ({spawns[curr_id]}) <= {prev_id} completed ({completed[prev_id]})", file=sys.stderr)
            sys.exit(1)
except Exception as exc:
    print(f"Erro na verificacao de ordem: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

verify_main_content() {
  local repo="$1"
  python3 - "$repo" <<'PYEOF'
import re, subprocess, sys

repo = sys.argv[1]

def get_file(fn):
    r = subprocess.run(["git", "-C", repo, "show", f"main:{fn}"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Arquivo {fn} nao encontrado na branch main", file=sys.stderr)
        sys.exit(1)
    return r.stdout

calc = get_file("calc.py")
geo = get_file("geo.py")
report = get_file("report.py")

prod = f"{calc}\n{geo}\n{report}"

checks = [
    (r'\bdef\s+mul\b', "def mul", 1),
    (r'\bdef\s+square\b', "def square", 1),
    (r'\bdef\s+area_square\b', "def area_square", 1),
    (r'\bdef\s+area_rect\b', "def area_rect", 1),
    (r'def\s+describe\(', "def describe(", 1),
    (r'\bdef\s+describe_all\b', "def describe_all", 1),
]

for pattern, label, expected_count in checks:
    matches = len(re.findall(pattern, prod))
    if matches != expected_count:
        print(f"Contagem incorreta para '{label}': esperava {expected_count}, encontrou {matches}", file=sys.stderr)
        sys.exit(1)

if "from calc import" not in geo:
    print("geo.py nao contem 'from calc import'", file=sys.stderr)
    sys.exit(1)

if "from geo import" not in report:
    print("report.py nao contem 'from geo import'", file=sys.stderr)
    sys.exit(1)

sys.exit(0)
PYEOF
}

verify_main_pytest() {
  local repo="$1"
  local py_bin="$2"
  python3 - "$repo" "$py_bin" <<'PYEOF'
import re, subprocess, sys

repo = sys.argv[1]
py_bin = sys.argv[2]

res = subprocess.run([py_bin, "-m", "pytest", "-q"], cwd=repo, capture_output=True, text=True)
if res.returncode != 0:
    print(f"pytest falhou na main (rc={res.returncode}):\n{res.stdout}\n{res.stderr}", file=sys.stderr)
    sys.exit(1)

m = re.search(r'(\d+)\s+passed', res.stdout)
if not m:
    print(f"Nao foi possivel extrair contagem de testes passados:\n{res.stdout}", file=sys.stderr)
    sys.exit(1)

passed_count = int(m.group(1))
print(f"Pytest na main: {passed_count} testes passaram")
if passed_count < 9:
    print(f"Esperava pelo menos 9 testes passados, mas obtive {passed_count}", file=sys.stderr)
    sys.exit(1)

sys.exit(0)
PYEOF
}

verify_ancestry() {
  local repo="$1"
  python3 - "$repo/.meister/meister.db" "$repo" <<'PYEOF'
import os, sqlite3, subprocess, sys

db_path = sys.argv[1]
repo = sys.argv[2]

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT step_id, integrated_sha FROM subtasks ORDER BY step_id")
rows = cur.fetchall()
if len(rows) != 6:
    print(f"Esperava 6 subtarefas no SQLite, encontrou {len(rows)}", file=sys.stderr)
    sys.exit(1)

for step_id, sha in rows:
    if not sha or not sha.strip():
        print(f"Subtarefa {step_id} sem integrated_sha", file=sys.stderr)
        sys.exit(1)
    res = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", sha.strip(), "main"])
    if res.returncode != 0:
        print(f"integrated_sha {sha} da subtarefa {step_id} nao e ancestral da main", file=sys.stderr)
        sys.exit(1)

res = subprocess.run(["git", "-C", repo, "log", "--merges", "--oneline", "main"], capture_output=True, text=True)
merges = [line for line in res.stdout.splitlines() if "Merge subtask" in line]
if len(merges) != 6:
    print(f"Esperava 6 merge commits de subtarefas na main, encontrou {len(merges)}:\n" + "\n".join(merges), file=sys.stderr)
    sys.exit(1)

sys.exit(0)
PYEOF
}

verify_spawns_per_task() {
  python3 - "$MEISTER_LOG_DIR" <<'PYEOF'
import json, sys

log_file = f"{sys.argv[1]}/orchestration_log.jsonl"
spawns = {}
try:
    for line in open(log_file):
        try:
            e = json.loads(line)
        except Exception:
            continue
        tid = e.get("task_id")
        ev = e.get("event") or e.get("event_type")
        if ev == "worker_spawn" and tid:
            spawns[tid] = spawns.get(tid, 0) + 1

    for n in range(1, 7):
        tid = f"task_{n}"
        count = spawns.get(tid, 0)
        if count != 1:
            print(f"{tid} teve {count} worker_spawn (esperado 1)", file=sys.stderr)
            sys.exit(1)
except Exception as exc:
    print(f"Erro ao verificar spawns por tarefa: {exc}", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PYEOF
}

verify_idempotence() {
  [ "$RC_CHAIN_ORCH2" = 0 ] || return 1
  python3 - "$MEISTER_LOG_DIR/orchestration_log.jsonl" <<'PYEOF'
import json, sys
count = 0
try:
    for line in open(sys.argv[1]):
        try:
            e = json.loads(line)
        except Exception:
            continue
        ev = e.get("event") or e.get("event_type")
        if ev == "worker_spawn":
            count += 1
    if count != 6:
        print(f"Total de worker_spawn apos orchestrate #2 e {count} != 6", file=sys.stderr)
        sys.exit(1)
except Exception as exc:
    print(f"Erro ao verificar idempotencia: {exc}", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PYEOF
}

check "C-a import rc 0, plan.json com 6 tarefas encadeadas e target_files em pares" 'verify_chain_plan_json'
check "C-b plan validate rc 0 e orchestrate #1 rc 0" '[ "$RC_CHAIN_VALIDATE" = 0 ] && [ "$RC_CHAIN_ORCH1" = 0 ]'
check "C-c SQLite run e 6 subtarefas COMPLETED com integrated_sha" "verify_sqlite_completed '$CHAIN_REPO'"
check "C-d ordem temporal de worker_spawn da task_n apos subtask_completed da task_{n-1}" 'verify_chain_order'
check "C-e conteudo na main com definicoes unicas e imports encadeados" "verify_main_content '$CHAIN_REPO'"
check "C-f pytest passa na main com pelo menos 9 testes passados" "verify_main_pytest '$CHAIN_REPO' '$PY'"
check "C-g ancestralidade das 6 subtarefas e 6 merge commits na main" "verify_ancestry '$CHAIN_REPO'"
check "C-h worker_spawn de cada task == 1 sem retries" 'verify_spawns_per_task'
check "C-i idempotencia orchestrate #2 rc 0 e total de worker_spawn continua 6" 'verify_idempotence'
check "C-j sem worktrees, branches temporarias ou tabs restantes" '[ "$(git -C "$CHAIN_REPO" worktree list | wc -l | tr -d " ")" = 1 ] && [ -z "$(git -C "$CHAIN_REPO" branch --list "meister/worktree/*" "meister/integration/*")" ] && [ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

print_summary_and_exit
