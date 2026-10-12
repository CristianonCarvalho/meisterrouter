#!/usr/bin/env bash
# Regera os GIFs do README (docs/img/*.gif).
#   ./record.sh                 grava os GIFs de terminal (parallel, gate, fallback) em en e pt-BR
#   ./record.sh timeline        grava os GIFs da timeline web (precisa do Google Chrome e do Node 22+)
# Requisitos: vhs (brew install vhs), ffmpeg, meister no PATH e python3. Os workers são simulados
# (stub_worker.py), então não gasta cota; orquestração, worktrees, gate, fallback e integração são reais.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE/../}
WORK=$(mktemp -d)

if [ "${1:-}" = "timeline" ]; then
  CHROME=${CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
  "$CHROME" --headless=new --remote-debugging-port=9333 --user-data-dir="$WORK/chrome" --no-first-run \
    --hide-scrollbars about:blank >/dev/null 2>&1 &
  CHROME_PID=$!; sleep 3
  for L in en pt-BR; do
    MEISTER_LANG=$L python3 -m meister.timeline_demo --mode live --speed 130 --port 5052 >/dev/null 2>&1 &
    DEMO_PID=$!
    until curl -s -o /dev/null http://127.0.0.1:5052/timeline; do sleep 0.3; done
    node "$HERE/capture.mjs" http://127.0.0.1:5052/timeline 20 "$WORK/$L" 1280 900 10
    kill $DEMO_PID; wait $DEMO_PID 2>/dev/null || true
    python3 - "$WORK/$L" <<'PY'
import glob, json, os, sys
d = sys.argv[1]
t = json.load(open(f"{d}/times.json"))
files = sorted(os.path.basename(f) for f in glob.glob(f"{d}/f_*.png"))
rows = [f"file '{f}'\nduration {(t[i+1]-t[i]) if i+1 < len(t) else 3.0:.3f}" for i, f in enumerate(files)]
rows.append(f"file '{files[-1]}'")
open(f"{d}/concat.txt", "w").write("\n".join(rows))
PY
    SUF=""; [ "$L" = "pt-BR" ] && SUF="-pt-BR"
    ffmpeg -v error -y -f concat -safe 0 -i "$WORK/$L/concat.txt" \
      -vf "fps=8,scale=1040:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
      -loop 0 "$OUT/timeline-web$SUF.gif"
  done
  kill $CHROME_PID
  exit 0
fi

# --- GIFs de terminal ---
mkdir -p "$WORK/bin" "$WORK/tapes"
for b in copilot agy claude codex; do
  printf '#!/bin/sh\nSTUB_NAME=%s exec python3 %s "$@"\n' "$b" "$HERE/stub_worker.py" > "$WORK/bin/$b"
  chmod +x "$WORK/bin/$b"
done
cat > "$WORK/setup.sh" <<SETUP
export PATH=$WORK/bin:\$PATH
export PS1=\$'\\[\\e[1;35m\\]❯\\[\\e[0m\\] '
export GIT_PAGER=cat MEISTER_LANG=\${3:-en}
rm -rf /tmp/demo_state
cp "$HERE/scenarios/\$1.json" /tmp/demo_scenario.json
$HERE/new_project.sh /tmp/shop >/dev/null
cd /tmp/shop && cp $HERE/\$2 plan.json
clear
SETUP
cat > "$WORK/tapes/common.tape" <<'COMMON'
Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 700
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set Framerate 24
Set WindowBar Colorful
Set BorderRadius 10
COMMON

# tape <nome> <plano> <idioma> <sufixo> <corpo>
tape() {
  cat > "$WORK/tapes/$1$4.tape" <<TAPE
Source "$WORK/tapes/common.tape"
Output "$OUT/$1$4.gif"
Hide
Type "source $WORK/setup.sh $1 $2 $3"
Enter
Sleep 1s
Show
Sleep 600ms
$5
TAPE
  vhs "$WORK/tapes/$1$4.tape"
}
ORCH='Type "meister orchestrate --plan-file plan.json"
Enter'

# Um comando só em cada GIF: é o que a LLM orquestradora roda por você, não algo que o usuário digita.
tape parallel plan_parallel.json en "" "Type \"# 3 tasks: 1 and 2 are independent, 3 depends on both\"
Enter
$ORCH
Sleep 14s"
tape parallel plan_parallel.json pt-BR "-pt-BR" "Type \"# 3 tarefas: a 1 e a 2 são independentes, a 3 depende das duas\"
Enter
$ORCH
Sleep 14s"
tape gate plan_gate.json en "" "Type \"# The worker's first attempt has a bug. Will you notice? The gate will.\"
Enter
$ORCH
Sleep 13s"
tape gate plan_gate.json pt-BR "-pt-BR" "Type \"# A primeira tentativa do worker tem um bug. Você notaria? O gate nota.\"
Enter
$ORCH
Sleep 13s"
tape fallback plan_fallback.json en "" "Type \"# tier_1 runs out of quota in the middle of the task...\"
Enter
$ORCH
Sleep 13s"
tape fallback plan_fallback.json pt-BR "-pt-BR" "Type \"# a tier_1 fica sem cota no meio da tarefa...\"
Enter
$ORCH
Sleep 13s"
