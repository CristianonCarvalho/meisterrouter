"""
meister.gate — Portão de Qualidade Determinístico (Deterministic Quality Gate).

Detecta executores de testes (pytest, vitest, cargo, npm), executa validações
locais de testes e linters (ruff, eslint), extrai sumários de git diff e avalia
a conclusão com o TypeSafe Jev Decisions API (control_cycle).
"""

import json
import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from meister.config import MeisterConfig, load_config
from meister.env_setup import prepare_environment
from meister.jev import control_cycle

logger = logging.getLogger(__name__)

@dataclass
class VerificationResult:
    passed: bool
    output: str
    infrastructure_error: bool = False
    skipped: List[str] = field(default_factory=list)


class DeterministicGate:
    """
    Portão de qualidade determinístico para validação pré-commit e garantia de evidência.
    """

    def __init__(self, repo_path: str = ".", config: Optional[MeisterConfig] = None):
        self.repo_path = os.path.abspath(repo_path)
        self.config = config or load_config(cwd=self.repo_path)

    def detect_test_runner(self, repo_path: Optional[str] = None) -> Optional[str]:
        """
        Auto-detecta o executor de testes apropriado no diretório do repositório.
        Retorna: 'pytest', 'vitest', 'cargo test', 'npm test' ou None.
        """
        path = os.path.abspath(repo_path or self.repo_path)

        # 1. Vitest (prioritário sobre npm genérico se arquivos específicos existirem)
        vitest_configs = [
            "vitest.config.ts",
            "vitest.config.js",
            "vitest.config.mjs",
            "vitest.config.mts",
        ]
        if any(os.path.exists(os.path.join(path, f)) for f in vitest_configs):
            return "vitest"

        # 2. Pytest configs específicos
        if os.path.exists(os.path.join(path, "pytest.ini")) or os.path.exists(
            os.path.join(path, "conftest.py")
        ):
            return "pytest"

        if os.path.exists(os.path.join(path, "setup.cfg")):
            try:
                with open(os.path.join(path, "setup.cfg"), "r", encoding="utf-8", errors="ignore") as f:
                    if "[tool:pytest]" in f.read():
                        return "pytest"
            except Exception:
                pass

        if os.path.exists(os.path.join(path, "pyproject.toml")):
            try:
                with open(os.path.join(path, "pyproject.toml"), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if "[tool.pytest" in content or "pytest" in content:
                        return "pytest"
            except Exception:
                pass

        # 3. Cargo / Rust
        if os.path.exists(os.path.join(path, "Cargo.toml")):
            return "cargo test"

        # 4. Package.json (Node / JS / TS)
        pkg_path = os.path.join(path, "package.json")
        if os.path.exists(pkg_path):
            try:
                with open(pkg_path, "r", encoding="utf-8", errors="ignore") as f:
                    pkg = json.load(f)
                    scripts = pkg.get("scripts", {})
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "vitest" in deps or "vitest" in scripts.get("test", ""):
                        return "vitest"
                    if "test" in scripts or deps:
                        return "npm test"
            except Exception:
                return "npm test"

        # 5. Diretório de testes python residual
        tests_dir = os.path.join(path, "tests")
        if os.path.isdir(tests_dir):
            for root, _, files in os.walk(tests_dir):
                if any(f.endswith(".py") for f in files):
                    return "pytest"

        return None

    def detect_linters(self, repo_path: Optional[str] = None) -> List[str]:
        """
        Detecta linters configurados no repositório (ruff, eslint).
        """
        path = os.path.abspath(repo_path or self.repo_path)
        linters: List[str] = []

        # Ruff
        ruff_indicators = ["ruff.toml", ".ruff.toml"]
        if any(os.path.exists(os.path.join(path, f)) for f in ruff_indicators):
            linters.append("ruff")
        elif os.path.exists(os.path.join(path, "pyproject.toml")):
            try:
                with open(os.path.join(path, "pyproject.toml"), "r", encoding="utf-8", errors="ignore") as f:
                    if "[tool.ruff" in f.read():
                        linters.append("ruff")
            except Exception:
                pass

        # ESLint
        eslint_indicators = [
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yaml",
            ".eslintrc.yml",
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.ts",
            "eslint.config.cjs",
        ]
        if any(os.path.exists(os.path.join(path, f)) for f in eslint_indicators):
            linters.append("eslint")
        elif os.path.exists(os.path.join(path, "package.json")):
            try:
                with open(os.path.join(path, "package.json"), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if "eslint" in content:
                        linters.append("eslint")
            except Exception:
                pass

        return linters

    def get_diff_summary(self, repo_path: Optional[str] = None) -> str:
        """
        Extrai o resumo das alterações de git (git diff --stat e status).
        """
        path = os.path.abspath(repo_path or self.repo_path)
        try:
            res = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            out = (res.stdout or "").strip()
            if not out:
                res_cached = subprocess.run(
                    ["git", "diff", "--cached", "--stat"],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                out = (res_cached.stdout or "").strip()
            if not out:
                res_status = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                out = (res_status.stdout or "").strip()
            return out or "No changes"
        except Exception as e:
            return f"Error extracting diff: {e}"

    def run_verification(self, repo_path: Optional[str] = None) -> Tuple[bool, str]:
        """
        Executa linters e suite de testes determinística.
        Retorna (passed: bool, output: str).
        """
        result = self.run_verification_ex(repo_path)
        return result.passed, result.output

    def run_verification_ex(self, repo_path: Optional[str] = None) -> VerificationResult:
        """Executa verificações e distingue falhas de código de falhas de infraestrutura."""
        path = os.path.abspath(repo_path or self.repo_path)
        if self.config.gate.commands:
            return self._run_configured_commands(path)

        runner = self.detect_test_runner(path)
        linters = self.detect_linters(path)
        node_check = os.path.isfile(os.path.join(path, "package.json")) and (
            runner in ("vitest", "npm test") or "eslint" in linters
        )
        if self.config.gate.install or (
            node_check and self.config.environment.install_dependencies
        ):
            if self.config.environment.install_dependencies:
                installed, install_output = prepare_environment(path, self.config)
                if not installed:
                    return VerificationResult(False, f"[INSTALL] {install_output}", infrastructure_error=True)
                install_prefix = f"[INSTALL]\n{install_output}\n" if install_output else ""
            else:
                install_prefix = ""
        else:
            install_prefix = ""
        if os.path.isdir(os.path.join(path, "node_modules", ".bin")):
            env_path = os.environ.get("PATH", "")
            local_path = os.path.join(path, "node_modules", ".bin")
            env_path = local_path + os.pathsep + env_path
        else:
            env_path = os.environ.get("PATH", "")

        if not runner and not linters:
            if self.config.gate.allow_unverified:
                return VerificationResult(
                    True,
                    install_prefix + "[UNVERIFIED] nenhuma verificação configurada ou detectada",
                )
            return VerificationResult(
                False,
                install_prefix
                + "Nenhum teste ou linter detectado. Configure gate.commands no meister.config.yaml "
                "(ou gate.allow_unverified: true).",
            )

        outputs: List[str] = [install_prefix.rstrip()] if install_prefix else []
        skipped: List[str] = []
        attempted = False
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = path + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        local_bin = os.path.join(path, "node_modules", ".bin")
        env["PATH"] = env_path

        # 1. Executa linters se presentes
        for linter in linters:
            if linter == "ruff":
                cmd = [sys.executable, "-m", "ruff", "check", "."]
            elif linter == "eslint":
                cmd = [os.path.join(local_bin, "eslint"), "."]
                if not os.path.isfile(cmd[0]):
                    message = f"[SKIPPED ESLINT] binário local não encontrado: {cmd[0]}"
                    outputs.append(message)
                    skipped.append(message)
                    continue
            else:
                continue

            try:
                attempted = True
                proc = subprocess.run(
                    cmd,
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=env,
                )
                combined = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
                outputs.append(f"[{linter.upper()}]\n{combined}")
                if proc.returncode != 0:
                    return VerificationResult(False, "\n".join(outputs), skipped=skipped)
            except subprocess.TimeoutExpired as exc:
                return VerificationResult(
                    False, f"[{linter.upper()}] timeout: {exc}", infrastructure_error=True, skipped=skipped
                )
            except OSError as exc:
                return VerificationResult(
                    False, f"[{linter.upper()}] executável indisponível: {exc}",
                    infrastructure_error=True, skipped=skipped
                )

        # 2. Executa suite de testes se detectada
        if runner:
            if runner == "pytest":
                cmd = [sys.executable, "-m", "pytest"]
            elif runner == "vitest":
                cmd = [os.path.join(local_bin, "vitest"), "run"]
                if not os.path.isfile(cmd[0]):
                    message = f"[SKIPPED VITEST] binário local não encontrado: {cmd[0]}"
                    outputs.append(message)
                    skipped.append(message)
                    cmd = []
            elif runner == "cargo test":
                cmd = ["cargo", "test"]
            elif runner == "npm test":
                cmd = ["npm", "test"]
            else:
                cmd = runner.split()

            if cmd:
                try:
                    attempted = True
                    proc = subprocess.run(
                        cmd,
                        cwd=path,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        env=env,
                        shell=False,
                    )
                    combined = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
                    outputs.append(f"[{runner}]\n{combined}")
                    if proc.returncode != 0:
                        return VerificationResult(False, "\n".join(outputs), skipped=skipped)
                except subprocess.TimeoutExpired as exc:
                    return VerificationResult(
                        False, f"[{runner}] timeout: {exc}", infrastructure_error=True, skipped=skipped
                    )
                except OSError as exc:
                    return VerificationResult(
                        False, f"[{runner}] executável indisponível: {exc}",
                        infrastructure_error=True, skipped=skipped
                    )

        if not attempted:
            return VerificationResult(
                True, "\n".join(outputs), infrastructure_error=True, skipped=skipped
            )
        return VerificationResult(True, "\n".join(outputs), skipped=skipped)

    def _run_configured_commands(self, path: str) -> VerificationResult:
        outputs: List[str] = []
        skipped: List[str] = []
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = path + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        local_bin = os.path.join(path, "node_modules", ".bin")
        if os.path.isdir(local_bin):
            env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")

        if self.config.environment.install_dependencies:
            installed, install_output = prepare_environment(path, self.config)
            if not installed:
                return VerificationResult(False, f"[INSTALL] {install_output}", infrastructure_error=True)
            if install_output:
                outputs.append(f"[INSTALL]\n{install_output}")
        if os.path.isdir(local_bin):
            env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")

        for command in self.config.gate.commands:
            argv = shlex.split(command.run) if isinstance(command.run, str) else list(command.run)
            try:
                proc = subprocess.run(
                    argv,
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=command.timeout_seconds,
                    env=env,
                    shell=False,
                )
            except subprocess.TimeoutExpired as exc:
                detail = f"[{command.name}] timeout: {exc}"
                outputs.append(detail)
                return VerificationResult(False, "\n".join(outputs), infrastructure_error=True, skipped=skipped)
            except OSError as exc:
                detail = f"[{command.name}] executável indisponível: {exc}"
                outputs.append(detail)
                return VerificationResult(False, "\n".join(outputs), infrastructure_error=True, skipped=skipped)
            combined = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
            outputs.append(f"[{command.name}]\n{combined}")
            if proc.returncode:
                if command.required:
                    return VerificationResult(False, "\n".join(outputs), skipped=skipped)
                warning = f"[WARN {command.name}] comando opcional falhou (rc={proc.returncode})"
                outputs.append(warning)
                skipped.append(warning)
        return VerificationResult(True, "\n".join(outputs), skipped=skipped)

    def evaluate_completion(
        self,
        diff_summary: Optional[str] = None,
        test_passed: Optional[bool] = None,
        attempts: int = 1,
        security_sensitive: bool = False,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Avalia o progresso contra a API de controle do TypeSafe Jev.
        """
        if diff_summary is None:
            diff_summary = self.get_diff_summary()

        if test_passed is None:
            test_passed, _ = self.run_verification()

        test_result = "pass" if test_passed else "fail"

        res = control_cycle(
            diff_summary=diff_summary,
            test_result=test_result,
            attempts=attempts,
            security_sensitive=security_sensitive,
            model=model,
        )
        if not test_passed and res.get("action") == "COMPLETE":
            logger.warning("Jev returned COMPLETE with failing tests. Overriding to RETRY via deterministic gate.")
            res["action"] = "RETRY"
            res["override_reason"] = "Hard deterministic gate: test_passed is False"
        return res


def detect_test_runner(repo_path: str = ".") -> Optional[str]:
    """Helper funcional para detectar executor de testes."""
    return DeterministicGate(repo_path).detect_test_runner()


def run_verification(repo_path: str = ".") -> Tuple[bool, str]:
    """Helper funcional para executar verificação determinística."""
    return DeterministicGate(repo_path).run_verification()


def run_verification_ex(repo_path: str = ".") -> VerificationResult:
    """Helper funcional para obter resultado detalhado da verificação."""
    return DeterministicGate(repo_path).run_verification_ex()


def evaluate_completion(
    diff_summary: str,
    test_passed: bool,
    attempts: int = 1,
    security_sensitive: bool = False,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper funcional para avaliar conclusão via Jev control decisions."""
    return DeterministicGate(".").evaluate_completion(
        diff_summary=diff_summary,
        test_passed=test_passed,
        attempts=attempts,
        security_sensitive=security_sensitive,
        model=model,
    )
