#!/usr/bin/env bash
# E2E de SEGURANCA do MeisterRouter (Herdr + codex/agy).
#
#  S1  Gate reprova: t1 boa + t2 que cria um teste falho de proposito.
#      Esperado: main NAO avanca (nem com o trabalho bom da t1), rc != 0, nada sobra (worktrees/branches/tabs).
#  S2  SIGKILL no meio + retomada: t2 depende de t1. Mata o orquestrador quando t2 ja esta rodando e roda o MESMO comando.
#      Esperado: t1 nao e refeita E o resultado final na main tem o trabalho da t1 e da t2.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
preflight

LOG="$WORK/e2e_safety.log"
exec > >(tee "$LOG") 2>&1

echo "meister: $(command -v meister)   inicio: $(date +%H:%M:%S)"

# =============================================================================================
echo; echo "################ S1: gate reprova -> main intacta ################"
S1="$WORK/e2e_safety_s1"; ST1="$WORK/e2e_safety_state/s1"
rm -rf "$ST1"; mkdir -p "$ST1/logs" "$ST1/wt"
make_disposable_repo "$S1"; BASE1="$(git -C "$S1" rev-parse main)"
export MEISTER_LOG_DIR="$ST1/logs" MEISTER_WORKTREES_DIR="$ST1/wt"
PLAN1='[{"id":"t1","description":"Adicione mul(a, b) que retorna a*b em calc.py e test_mul em tests/test_calc.py. Nao altere outros arquivos.","target_files":["calc.py","tests/test_calc.py"],"depends_on":[]},{"id":"t2","description":"Adicione em tests/test_text.py um teste chamado test_falha_proposital com o corpo: assert 1 == 2. O teste DEVE falhar; nao o corrija e nao altere nenhum outro arquivo.","target_files":["tests/test_text.py"],"depends_on":[]}]'
cd "$S1" || exit 1
echo "== orchestrate (S1) $(date +%H:%M:%S)"
RC1=0
meister orchestrate --task "$PLAN1" || RC1=$?
echo "rc=$RC1  fim: $(date +%H:%M:%S)"
echo "== estado final S1"; repo_state "$S1"
echo "== eventos S1"; timeline "$MEISTER_LOG_DIR"

check "S1-a main NAO avancou"                   '[ "$(git -C "$S1" rev-parse main)" = "$BASE1" ]'
check "S1-b orchestrate saiu com rc != 0"       '[ "$RC1" != 0 ]'
check "S1-c teste falho nao chegou na main"     '! git -C "$S1" show main:tests/test_text.py | grep -q "assert 1 == 2"'
check "S1-d t2 rodou e NAO foi integrada"       '[ "$(cnt "$MEISTER_LOG_DIR" t2 worker_task_end)" -ge 1 ] && [ "$(cnt "$MEISTER_LOG_DIR" t2 subtask_completed)" = 0 ]'
check "S1-e sem worktrees restantes"            '[ "$(git -C "$S1" worktree list | wc -l | tr -d " ")" = 1 ]'
check "S1-f sem branches meister/worktree/*"    '[ -z "$(git -C "$S1" branch --list "meister/worktree/*")" ]'
check "S1-g sem tabs worker:* restantes"        '[ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

# =============================================================================================
echo; echo "################ S2: SIGKILL no meio + retomada ################"
S2="$WORK/e2e_safety_s2"; ST2="$WORK/e2e_safety_state/s2"
rm -rf "$ST2"; mkdir -p "$ST2/logs" "$ST2/wt"
make_disposable_repo "$S2"
export MEISTER_LOG_DIR="$ST2/logs" MEISTER_WORKTREES_DIR="$ST2/wt"
PLAN2='[{"id":"t1","description":"Adicione mul(a, b) que retorna a*b em calc.py e test_mul em tests/test_calc.py. Nao altere outros arquivos.","target_files":["calc.py","tests/test_calc.py"],"depends_on":[]},{"id":"t2","description":"Adicione shout(name) que retorna name.upper() em text.py e test_shout em tests/test_text.py. Nao altere outros arquivos.","target_files":["text.py","tests/test_text.py"],"depends_on":["t1"]}]'
cd "$S2" || exit 1
echo "== orchestrate #1 em background $(date +%H:%M:%S)"
meister orchestrate --task "$PLAN2" > "$ST2/run1.out" 2>&1 &
ORCH=$!
for _ in $(seq 1 300); do
  [ "$(cnt "$MEISTER_LOG_DIR" t2 worker_task_start)" -ge 1 ] && break
  kill -0 "$ORCH" 2>/dev/null || { echo "!! orquestrador terminou antes de t2 iniciar"; break; }
  sleep 1
done
T2_DONE_AT_KILL="$(cnt "$MEISTER_LOG_DIR" t2 subtask_completed)"
KILLED=0
if kill -0 "$ORCH" 2>/dev/null; then kill -9 "$ORCH" && KILLED=1; fi
wait "$ORCH" 2>/dev/null || true
echo "SIGKILL aplicado=$KILLED  t2 ja concluida no momento do kill=$T2_DONE_AT_KILL  ($(date +%H:%M:%S))"
echo "== estado logo apos o kill (antes de retomar)"; repo_state "$S2"
T1_SPAWN_1="$(cnt "$MEISTER_LOG_DIR" t1 worker_spawn)"

echo "== orchestrate #2 (MESMO comando = retomada) $(date +%H:%M:%S)"
RC2=0
meister orchestrate --task "$PLAN2" > "$ST2/run2.out" 2>&1 || RC2=$?
echo "rc=$RC2  fim: $(date +%H:%M:%S)"; tail -n 5 "$ST2/run2.out"
echo "== estado final S2"; repo_state "$S2"
echo "== eventos S2 (run #1 + run #2)"; timeline "$MEISTER_LOG_DIR"

check "S2-a kill pegou o run no meio (t2 rodando, nao concluida)" '[ "$KILLED" = 1 ] && [ "$T2_DONE_AT_KILL" = 0 ]'
check "S2-b retomada terminou com rc 0"         '[ "$RC2" = 0 ]'
check "S2-c t1 NAO foi reexecutada"             '[ "$(cnt "$MEISTER_LOG_DIR" t1 worker_spawn)" = "$T1_SPAWN_1" ] && [ "$T1_SPAWN_1" = 1 ]'
check "S2-d main tem o trabalho da t1 (mul)"    'git -C "$S2" show main:calc.py | grep -q "def mul"'
check "S2-e main tem o trabalho da t2 (shout)"  'git -C "$S2" show main:text.py | grep -q "def shout"'
check "S2-j sem definicao duplicada (shout 1x em text.py)" '[ "$(git -C "$S2" show main:text.py | grep -c "def shout")" = 1 ]'
check "S2-k JSONL explica a rejeicao no S1 (evento de rejeicao/falha da t2)" '[ "$(cnt "$ST1/logs" t2 subtask_rejected)" -ge 1 ] || [ "$(cnt "$ST1/logs" t2 subtask_failed)" -ge 1 ]'
check "S2-f pytest passa na main"              '(cd "$S2" && "$PY" -m pytest -q)'
check "S2-g sem worktrees restantes"            '[ "$(git -C "$S2" worktree list | wc -l | tr -d " ")" = 1 ]'
check "S2-h sem branches meister/worktree/*"    '[ -z "$(git -C "$S2" branch --list "meister/worktree/*")" ]'
check "S2-i sem tabs worker:* restantes"        '[ -z "$(worker_tabs | awk "{print \$2}" | grep -vxF -f <(echo "$WT_BEFORE_IDS") )" ]'

if printf '%s\n' "${RESULTS[@]}" | grep -q "FAIL  S2-d"; then
  echo
  echo ">> S2-d FALHOU: se S2-c passou, a HIPOTESE se confirma (t1 pulada, mas seu merge foi descartado ao recriar a"
  echo "   branch de integração). O trabalho antigo deve estar em refs/meister/archive (veja 'refs de arquivo' acima)."
fi

print_summary_and_exit
