# Como os GIFs do README são feitos

Os GIFs de `docs/img/` são regerados por `record.sh` (terminal) e `record.sh timeline` (timeline web).
Os workers são **simulados** (`stub_worker.py` faz de conta que é `copilot`, `agy` ou `claude`), então a gravação é
repetível, rápida e não gasta cota; a orquestração, os worktrees, o gate, o fallback e a integração são os de verdade.
Os cenários (atraso, bug na primeira tentativa, via sem cota) estão em `scenarios/`.

```bash
brew install vhs                 # e ffmpeg; meister e python3 precisam estar no PATH
docs/img/src/record.sh           # hero, gate e fallback, em inglês e em português
docs/img/src/record.sh timeline  # a timeline web (precisa do Google Chrome e do Node 22+)
```
