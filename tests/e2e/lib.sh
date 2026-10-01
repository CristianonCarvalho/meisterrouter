#!/usr/bin/env bash
# tests/e2e/lib.sh: Biblioteca comum para scripts E2E do MeisterRouter.
# Este arquivo deve ser apenas incluído via source por run_flow.sh, run_parallel.sh e run_safety.sh.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "ERRO: lib.sh é uma biblioteca e deve ser incluída via source, não executada diretamente." >&2
  exit 1
fi

E2E_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MR="$(cd "$E2E_LIB_DIR/../.." && pwd)"

PY="${MEISTER_E2E_PYTHON:-$MR/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

WORK="${MEISTER_E2E_DIR:-${TMPDIR:-/tmp}/meister-e2e}"
mkdir -p "$WORK"

export PATH="$MR/bin:$PATH"

step() { printf '\n===== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }

preflight() {
  local err=0
  if [ "${HERDR_ENV:-0}" != "1" ]; then
    echo "ERRO: HERDR_ENV deve ser 1 para executar testes E2E com Herdr." >&2
    err=1
  fi
  if ! command -v herdr >/dev/null 2>&1; then
    echo "ERRO: herdr CLI não encontrado no PATH." >&2
    err=1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "ERRO: git não encontrado no PATH." >&2
    err=1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERRO: python3 não encontrado no PATH." >&2
    err=1
  fi
  if [ $err -ne 0 ]; then
    exit 1
  fi
  if ! command -v codex >/dev/null 2>&1; then
    echo "AVISO: codex CLI não encontrado no PATH (necessário para workers codex_luna)." >&2
  fi
  if ! command -v agy >/dev/null 2>&1; then
    echo "AVISO: agy CLI não encontrado no PATH (necessário para workers agy_gemini_flash)." >&2
  fi
  echo "meister resolvido: $(command -v meister)"
}

RESULTS=()
check() { # $1=nome  $2=comando avaliado (retorno 0 = PASS)
  if eval "$2" >/dev/null 2>&1; then
    RESULTS+=("PASS  $1")
  else
    RESULTS+=("FAIL  $1")
  fi
}

skip() { # $1=nome  $2=motivo
  RESULTS+=("SKIP  $1 ($2)")
}

herdr_ids() {
  python3 - <<'PYEOF'
import json, os, subprocess
ws = os.environ.get("HERDR_WORKSPACE_ID", "")
out = []
for kind in ("tab", "pane"):
    r = subprocess.run(["herdr", kind, "list", "--workspace", ws], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)["result"]
        out += [f"{kind} {x.get(kind + '_id')} {x.get('label') or ''}".rstrip() for x in d.get(kind + "s", [])]
    except Exception:
        pass
print("\n".join(sorted(out)))
PYEOF
}

worker_tabs() { herdr_ids | grep -E '^tab [^ ]+ worker:' || true; }

record_worker_tabs_before() {
  WT_BEFORE_IDS="$(worker_tabs | awk '{print $2}')"
}

cleanup_worker_tabs() {
  echo
  echo "Fechando tabs worker:* criadas por este teste (apenas as que não existiam antes)..."
  local current_ids
  current_ids="$(worker_tabs | awk '{print $2}')"
  for t in $current_ids; do
    if ! echo "$WT_BEFORE_IDS" | grep -qx "$t"; then
      herdr tab close "$t" >/dev/null 2>&1 && echo "  fechada tab $t"
    fi
  done
}

print_summary_and_exit() {
  echo
  echo "################ RESUMO ################"
  printf '%s\n' "${RESULTS[@]}"
  cleanup_worker_tabs
  local fails skips passes
  fails=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^FAIL" || true)
  skips=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^SKIP" || true)
  passes=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^PASS" || true)
  echo
  echo "Totais: PASS=$passes  FAIL=$fails  SKIP=$skips"
  if [ "$fails" -gt 0 ]; then
    echo "Resultado: FALHA ($fails checagens falharam)"
    exit 1
  elif [ "$skips" -gt 0 ]; then
    echo "Resultado: INCONCLUSIVO ($skips checagens ignoradas/inconclusivas)"
    exit 2
  else
    echo "Resultado: SUCESSO (todas as checagens passaram)"
    exit 0
  fi
}

cnt() { # $1=logdir $2=task_id $3=evento
  python3 - "$1/orchestration_log.jsonl" "$2" "$3" <<'PYEOF'
import json, sys
n = 0
try:
    for line in open(sys.argv[1]):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("task_id") == sys.argv[2] and (e.get("event") or e.get("event_type")) == sys.argv[3]:
            n += 1
except FileNotFoundError:
    pass
print(n)
PYEOF
}

timeline() { # $1=logdir
  python3 - "$1/orchestration_log.jsonl" <<'PYEOF'
import json, sys
try:
    for l in open(sys.argv[1]):
        e = json.loads(l)
        print(e.get("ts", "")[11:23], e.get("task_id"), e.get("tier"), e.get("event") or e.get("event_type"),
              "exit=", e.get("exit_code"), (e.get("error") or "")[:90])
except FileNotFoundError:
    print("(sem JSONL)")
PYEOF
}

make_disposable_repo() { # $1=target_dir
  local target="$1"
  rm -rf "$target" && mkdir -p "$target/tests" && cd "$target" || exit 1
  git init -q -b main
  printf 'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n' > calc.py
  printf 'def greet(name):\n    return "hi " + name\n' > text.py
  printf 'from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n' > tests/test_calc.py
  printf 'from text import greet\n\n\ndef test_greet():\n    assert greet("a") == "hi a"\n' > tests/test_text.py
  printf '[tool.pytest.ini_options]\npythonpath = ["."]\n' > pyproject.toml
  printf '.meister/\n__pycache__/\n' > .gitignore
  local luna_m="${E2E_LUNA_MODEL:-gpt-6-luna}"
  cat <<CFG > meister.config.yaml
router:
  mode: first
workers:
  tier_order:
    - {name: codex_luna, harness: codex, model: ${luna_m}, max_retries: 1}
    - {name: agy_gemini_flash, harness: agy, model: gemini-3.8-flash-medium, max_retries: 1}
CFG
  git add -A && git commit -qm init
}

repo_state() { # $1=repo
  echo "-- main: $(git -C "$1" rev-parse --short main)"
  echo "-- log --all:"; git -C "$1" log --oneline --all | sed 's/^/     /'
  echo "-- worktrees:"; git -C "$1" worktree list | sed 's/^/     /'
  echo "-- branches meister/*:"; git -C "$1" branch --list 'meister/*' | sed 's/^/     /'
  echo "-- refs de arquivo:"; git -C "$1" for-each-ref refs/meister/archive | sed 's/^/     /'
  echo "-- tabs worker:* abertas:"; worker_tabs | sed 's/^/     /'
}

record_worker_tabs_before
