import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
E2E_DIR = REPO_ROOT / "tests" / "e2e"


def test_e2e_scripts_exist():
    assert E2E_DIR.is_dir(), f"Diretório E2E não encontrado: {E2E_DIR}"
    expected_scripts = {"lib.sh", "run_flow.sh", "run_parallel.sh", "run_safety.sh", "herdr_sampler.py"}
    found_files = {p.name for p in E2E_DIR.iterdir()}
    for exp in expected_scripts:
        assert exp in found_files, f"Arquivo esperado '{exp}' não encontrado em {E2E_DIR}"


def test_e2e_scripts_bash_syntax_valid():
    sh_files = list(E2E_DIR.glob("*.sh"))
    assert len(sh_files) >= 4, f"Esperava pelo menos 4 arquivos .sh, encontrou {len(sh_files)}"
    for sh in sh_files:
        res = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        assert res.returncode == 0, f"bash -n falhou em {sh.name}:\nstdout: {res.stdout}\nstderr: {res.stderr}"


def test_e2e_scripts_portability_no_hardcoded_paths():
    forbidden_tokens = ["/Users/", "scratchpad"]
    for p in E2E_DIR.rglob("*"):
        if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden_tokens:
                assert token not in text, f"Arquivo {p.name} contém '{token}' violando portabilidade"


def test_e2e_scripts_run_scripts_executable_and_source_lib():
    run_scripts = list(E2E_DIR.glob("run_*.sh"))
    assert len(run_scripts) >= 3, f"Esperava pelo menos 3 run_*.sh, encontrou {len(run_scripts)}"
    for script in run_scripts:
        assert os.access(script, os.X_OK), f"Script {script.name} não possui bit de execução (+x)"
        content = script.read_text(encoding="utf-8")
        assert re.search(r'(?:source|\.)\s+.*lib\.sh', content), f"Script {script.name} não inclui lib.sh via source"


def test_e2e_scripts_herdr_sampler_compiles(tmp_path):
    sampler = E2E_DIR / "herdr_sampler.py"
    assert sampler.is_file(), "herdr_sampler.py não encontrado"
    dest = tmp_path / "herdr_sampler.pyc"
    py_compile.compile(str(sampler), cfile=str(dest), doraise=True)
    assert dest.is_file()


def test_e2e_scripts_no_pytest_collection_in_e2e_dir():
    # Nenhum arquivo dentro de tests/e2e/ deve ser um módulo de teste pytest
    e2e_tests = list(E2E_DIR.glob("test_*.py")) + list(E2E_DIR.glob("*_test.py"))
    assert len(e2e_tests) == 0, f"Arquivos de teste encontrados em tests/e2e: {[f.name for f in e2e_tests]}"

    # Garante via collect-only que nenhum item coletado pelo pytest pertence a tests/e2e/
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Falha na coleta de testes:\n{res.stderr}"
    for line in res.stdout.splitlines():
        assert "tests/e2e/" not in line, f"Pytest coletou teste indevido de tests/e2e: {line}"
