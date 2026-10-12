"""Worker simulado para as gravações do README: faz de conta que é copilot/agy/claude e escreve os arquivos da tarefa.

O comportamento (atraso, bug na primeira tentativa, via sem cota) vem de /tmp/demo_scenario.json (veja scenarios/)."""
import json
import os
import sys
import time

name = os.environ.get("STUB_NAME", "copilot")
prompt = " ".join(sys.argv[1:])
action = "noop"
for key, act in (("add_tax", "tax"), ("apply_discount", "discount"), ("total(", "checkout")):
    if key in prompt:
        action = act
        break
try:
    scenario = json.load(open("/tmp/demo_scenario.json"))
except OSError:
    scenario = {}
state = "/tmp/demo_state"
os.makedirs(state, exist_ok=True)
counter = os.path.join(state, f"{action}.n")
n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
open(counter, "w").write(str(n))
time.sleep(float(scenario.get("delay", 2.5)))

if name in scenario.get("quota_bins", []):
    sys.stderr.write("Error: 429 Too Many Requests: rate limit exceeded\n")
    sys.exit(1)

FILES = {
 "tax": {
  "tax.py": "def add_tax(price, rate=0.2):\n    return round(price * (1 + rate), 2)\n",
  "test_tax.py": "from tax import add_tax\n\n\ndef test_add_tax():\n    assert add_tax(100) == 120.0\n",
 },
 "discount": {
  "discount.py": "def apply_discount(price, pct):\n    return round(price * (1 - pct / 100), 2)\n",
  "test_discount.py": "from discount import apply_discount\n\n\ndef test_apply_discount():\n    assert apply_discount(200, 10) == 180.0\n",
 },
 "checkout": {
  "checkout.py": "from discount import apply_discount\nfrom tax import add_tax\n\n\ndef total(price, pct):\n    return add_tax(apply_discount(price, pct))\n",
  "test_checkout.py": "from checkout import total\n\n\ndef test_total():\n    assert total(200, 10) == 216.0\n",
 },
}
BUGGY = {
 "discount.py": "def apply_discount(price, pct):\n    return round(price * (1 - pct), 2)\n",
 "test_discount.py": "from discount import apply_discount\n\n\ndef test_apply_discount():\n    assert apply_discount(200, 10) == 180.0\n",
}
files = FILES.get(action, {})
if action == scenario.get("buggy") and n == 1:
    files = BUGGY
for path, body in files.items():
    open(path, "w").write(body)
if "json" in prompt or name in ("agy", "claude"):
    print(json.dumps({"result": "done", "type": "result", "is_error": False}))
else:
    print("done")
