"""
meister.state — Persistência SQLite WAL, máquina de estados determinística e retomada idempotente.

Implementa:
- Armazenamento em SQLite com WAL mode (Achado #7).
- Máquina de estados finitos (FSM) com validação de transições para Runs e Subtasks.
- Identificadores determinísticos por hash SHA-256 (Achado #28).
- Retomada idempotente de tarefas interrompidas ou concluídas.
- Registro multi-slot de active panes (substituindo active_worker_pane.txt de slot único).
"""

from __future__ import annotations

import os
import json
import time
import sqlite3
import hashlib
import logging
from meister.i18n import t
from enum import Enum
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, Dict, List, Any, Union, Set

logger = logging.getLogger(__name__)


class StateError(Exception):
    """Exceção base para erros de gerenciamento de estado."""
    pass


class InvalidStateTransitionError(StateError):
    """Lançada quando uma transição de estado inválida é tentada."""
    pass


class RunState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SubtaskState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


VALID_RUN_TRANSITIONS: Dict[RunState, Set[RunState]] = {
    RunState.PENDING: {RunState.RUNNING, RunState.CANCELLED},
    RunState.RUNNING: {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED},
    RunState.FAILED: {RunState.RUNNING, RunState.PENDING, RunState.CANCELLED},
    RunState.COMPLETED: {RunState.RUNNING},
    RunState.CANCELLED: {RunState.RUNNING, RunState.PENDING},
}

VALID_SUBTASK_TRANSITIONS: Dict[SubtaskState, Set[SubtaskState]] = {
    SubtaskState.PENDING: {SubtaskState.RUNNING, SubtaskState.FAILED},
    SubtaskState.RUNNING: {SubtaskState.COMPLETED, SubtaskState.FAILED, SubtaskState.RETRYING},
    SubtaskState.RETRYING: {SubtaskState.RUNNING, SubtaskState.FAILED},
    SubtaskState.FAILED: {SubtaskState.PENDING, SubtaskState.RUNNING, SubtaskState.RETRYING},
    SubtaskState.COMPLETED: {SubtaskState.RUNNING, SubtaskState.PENDING},
}


def utc_now_iso() -> str:
    """Retorna timestamp UTC atual formatado em ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def compute_run_id(task_prompt: str, cwd: str = "") -> str:
    """Gera run_id determinístico baseado no hash SHA-256 do prompt e diretório (Achado #28)."""
    normalized = f"{os.path.abspath(cwd or os.getcwd()).lower()}:{task_prompt.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def compute_subtask_id(run_id: str, step_id: str, description: str = "") -> str:
    """Gera subtask_id determinístico baseado em run_id, step_id e descrição (Achado #28)."""
    normalized = f"{run_id}:{str(step_id).strip()}:{description.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def task_fingerprint(step: Dict[str, Any], steps_by_id: Dict[str, Dict[str, Any]]) -> str:
    """Calcula fingerprint recursivo do conteúdo e dependências da tarefa, sem incluir escopo."""
    cache: Dict[str, str] = {}
    visiting: Set[str] = set()

    def fingerprint(step_id: str) -> str:
        if step_id in cache:
            return cache[step_id]
        if step_id in visiting:
            raise ValueError(t("engine.state.cycle", step_id=step_id))
        current = steps_by_id.get(step_id)
        if current is None:
            raise ValueError(t("engine.state.unknown_dependency", step_id=step_id))

        visiting.add(step_id)
        dependencies = current.get("depends_on") or []
        if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
            raise ValueError(t("engine.state.invalid_dependencies", step_id=step_id))
        dependency_fingerprints = [fingerprint(dep) for dep in sorted(dependencies)]
        visiting.remove(step_id)

        payload = json.dumps(
            {
                "description": str(current.get("description") or "").strip(),
                "dependencies": dependency_fingerprints,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        cache[step_id] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return cache[step_id]

    requested_id = str(step.get("id") or step.get("step_id") or "")
    if requested_id not in steps_by_id:
        raise ValueError(t("engine.state.unknown_task", task_id=requested_id))
    return fingerprint(requested_id)


class StateManager:
    """Gerenciador central de persistência e máquina de estados do MeisterRouter."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            env_db = os.environ.get("MEISTER_DB_PATH")
            if env_db:
                self.db_path = env_db
            else:
                from meister.logger import find_project_root
                from meister.config import ensure_meister_dir
                root = find_project_root() or os.getcwd()
                meister_dir = ensure_meister_dir(root)
                self.db_path = os.path.join(meister_dir, "meister.db")
        else:
            self.db_path = db_path
            if self.db_path != ":memory:":
                target_dir = os.path.dirname(os.path.abspath(self.db_path))
                if target_dir:
                    os.makedirs(target_dir, exist_ok=True)

        self._mem_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row

        self.init_db()

    @contextmanager
    def _get_connection(self):
        """Fornece conexão SQLite transacional com tratamento robusto de rollback/commit."""
        if self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_db(self) -> None:
        """Inicializa esquema SQLite com WAL mode e tabelas relacionais indexadas."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if self.db_path != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA busy_timeout=5000;")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                task_prompt TEXT NOT NULL,
                cwd TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}'
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS subtasks (
                subtask_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                description TEXT NOT NULL,
                target_files_json TEXT DEFAULT '[]',
                depends_on_json TEXT DEFAULT '[]',
                assigned_tier TEXT DEFAULT '',
                status TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                pane_id TEXT,
                worktree_path TEXT,
                result_json TEXT DEFAULT '{}',
                error_message TEXT,
                integrated_sha TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            """)

            cursor.execute("PRAGMA table_info(subtasks);")
            sub_cols = [row["name"] for row in cursor.fetchall()]
            if "integrated_sha" not in sub_cols:
                cursor.execute("ALTER TABLE subtasks ADD COLUMN integrated_sha TEXT;")


            cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_panes (
                pane_id TEXT PRIMARY KEY,
                run_id TEXT,
                subtask_id TEXT,
                tab_id TEXT,
                pid INTEGER,
                created_at TEXT NOT NULL
            );
            """)

            cursor.execute("PRAGMA table_info(active_panes);")
            active_cols = [row["name"] for row in cursor.fetchall()]
            if "tab_id" not in active_cols:
                cursor.execute("ALTER TABLE active_panes ADD COLUMN tab_id TEXT;")
            if "pid" not in active_cols:
                cursor.execute("ALTER TABLE active_panes ADD COLUMN pid INTEGER;")


            cursor.execute("""
            CREATE TABLE IF NOT EXISTS circuit_breakers (
                harness TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'CLOSED',
                failure_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                last_failure_at REAL,
                cooldown_until REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS quota_accounting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                harness TEXT NOT NULL,
                model TEXT,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0,
                timestamp REAL NOT NULL
            );
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_subtasks_run_id ON subtasks(run_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_subtasks_status ON subtasks(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_panes_run_id ON active_panes(run_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_quota_harness_ts ON quota_accounting(harness, timestamp);")

    # =========================================================================
    # Gerenciamento de Runs
    # =========================================================================

    def create_or_get_run(
        self,
        task_prompt: str,
        cwd: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cria ou recupera deterministicamente um Run pelo hash do prompt e cwd."""
        resolved_cwd = os.path.abspath(cwd or os.getcwd())
        run_id = force_run_id or compute_run_id(task_prompt, resolved_cwd)
        now = utc_now_iso()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)

            meta_str = json.dumps(metadata or {}, ensure_ascii=False)
            cursor.execute(
                """
                INSERT INTO runs (run_id, task_prompt, cwd, state, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, task_prompt, resolved_cwd, RunState.PENDING.value, now, now, meta_str),
            )
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            return dict(cursor.fetchone())

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Recupera registro de um run pelo run_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_resumable_run(self, cwd: str, exclude_run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retorna o run FAILED/RUNNING mais recente do diretório ainda não substituído."""
        resolved_cwd = os.path.abspath(cwd)
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE cwd = ? AND state IN (?, ?)
                ORDER BY updated_at DESC, created_at DESC
                """,
                (resolved_cwd, RunState.FAILED.value, RunState.RUNNING.value),
            ).fetchall()
        for row in rows:
            run = dict(row)
            if run["run_id"] == exclude_run_id:
                continue
            try:
                metadata = json.loads(run.get("metadata_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not metadata.get("superseded_by"):
                return run
        return None

    def mark_superseded(self, source_run_id: str, replacement_run_id: str) -> Dict[str, Any]:
        """Marca a origem histórica como substituída sem alterar seu estado."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (source_run_id,))
            row = cursor.fetchone()
            if row is None:
                raise StateError(t("engine.state.run_missing", run_id=source_run_id))
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["superseded_by"] = replacement_run_id
            cursor.execute(
                "UPDATE runs SET metadata_json = ?, updated_at = ? WHERE run_id = ?",
                (json.dumps(metadata, ensure_ascii=False), utc_now_iso(), source_run_id),
            )
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (source_run_id,))
            return dict(cursor.fetchone())

    def transition_run(
        self,
        run_id: str,
        to_state: Union[RunState, str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Aplica transição de estado validada por FSM ao Run."""
        to_state_enum = RunState(to_state) if isinstance(to_state, str) else to_state

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                raise StateError(t("engine.state.run_missing", run_id=run_id))

            current_state = RunState(row["state"])
            if to_state_enum != current_state:
                valid_next = VALID_RUN_TRANSITIONS.get(current_state, set())
                if to_state_enum not in valid_next:
                    raise InvalidStateTransitionError(
                        t(
                            "engine.state.invalid_run_transition",
                            run_id=run_id,
                            current=current_state.value,
                            target=to_state_enum.value,
                        )
                    )

            now = utc_now_iso()
            if metadata:
                existing_meta = json.loads(row["metadata_json"] or "{}")
                existing_meta.update(metadata)
                new_meta_str = json.dumps(existing_meta, ensure_ascii=False)
                cursor.execute(
                    "UPDATE runs SET state = ?, updated_at = ?, metadata_json = ? WHERE run_id = ?",
                    (to_state_enum.value, now, new_meta_str, run_id),
                )
            else:
                cursor.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (to_state_enum.value, now, run_id),
                )

            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            return dict(cursor.fetchone())

    # =========================================================================
    # Gerenciamento de Subtasks
    # =========================================================================

    def add_subtasks(
        self,
        run_id: str,
        subtasks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Adiciona deterministicamente subtasks a um Run, preservando subtasks já completas."""
        now = utc_now_iso()
        results: List[Dict[str, Any]] = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for sub in subtasks:
                step_id = str(sub.get("id") or sub.get("step_id") or f"step_{len(results) + 1}")
                desc = str(sub.get("description") or step_id)
                subtask_id = compute_subtask_id(run_id, step_id, desc)

                # Verifica se a subtask já existe
                cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
                existing = cursor.fetchone()
                if existing:
                    results.append(dict(existing))
                    continue

                target_files = json.dumps(sub.get("target_files") or [], ensure_ascii=False)
                depends_on = json.dumps(sub.get("depends_on") or [], ensure_ascii=False)
                tier = str(sub.get("assigned_tier") or sub.get("tier") or "")

                cursor.execute(
                    """
                    INSERT INTO subtasks (
                        subtask_id, run_id, step_id, description, target_files_json,
                        depends_on_json, assigned_tier, status, attempts, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        subtask_id,
                        run_id,
                        step_id,
                        desc,
                        target_files,
                        depends_on,
                        tier,
                        SubtaskState.PENDING.value,
                        0,
                        now,
                        now,
                    ),
                )
                cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
                results.append(dict(cursor.fetchone()))

        return results

    def get_subtask(self, subtask_id: str) -> Optional[Dict[str, Any]]:
        """Recupera registro de uma subtask por subtask_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_subtasks(self, run_id: str) -> List[Dict[str, Any]]:
        """Recupera todas as subtasks associadas a um Run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subtasks WHERE run_id = ? ORDER BY created_at ASC", (run_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_subtasks(self, run_id: str) -> List[Dict[str, Any]]:
        """Recupera subtasks pendentes ou aguardando retry."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM subtasks WHERE run_id = ? AND status IN (?, ?) ORDER BY created_at ASC",
                (run_id, SubtaskState.PENDING.value, SubtaskState.RETRYING.value),
            )
            return [dict(row) for row in cursor.fetchall()]

    def transition_subtask(
        self,
        subtask_id: str,
        to_state: Union[SubtaskState, str],
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        attempts: Optional[int] = None,
        pane_id: Optional[str] = None,
        worktree_path: Optional[str] = None,
        assigned_tier: Optional[str] = None,
        integrated_sha: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aplica transição validada por FSM a uma subtask com registro de resultado e erros."""
        to_state_enum = SubtaskState(to_state) if isinstance(to_state, str) else to_state

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
            row = cursor.fetchone()
            if not row:
                raise StateError(t("engine.state.subtask_missing", subtask_id=subtask_id))

            current_state = SubtaskState(row["status"])
            if to_state_enum != current_state:
                valid_next = VALID_SUBTASK_TRANSITIONS.get(current_state, set())
                if to_state_enum not in valid_next:
                    raise InvalidStateTransitionError(
                        t(
                            "engine.state.invalid_subtask_transition",
                            subtask_id=subtask_id,
                            current=current_state.value,
                            target=to_state_enum.value,
                        )
                    )

            now = utc_now_iso()
            cur_attempts = row["attempts"]
            new_attempts = attempts if attempts is not None else (cur_attempts + 1 if to_state_enum == SubtaskState.RUNNING else cur_attempts)

            res_json = json.dumps(result, ensure_ascii=False) if result is not None else row["result_json"]
            err_msg = error if error is not None else row["error_message"]
            final_pane_id = pane_id if pane_id is not None else row["pane_id"]
            final_worktree = worktree_path if worktree_path is not None else row["worktree_path"]
            final_tier = assigned_tier if assigned_tier is not None else row["assigned_tier"]
            final_sha = integrated_sha if integrated_sha is not None else row["integrated_sha"]

            cursor.execute(
                """
                UPDATE subtasks
                SET status = ?, attempts = ?, result_json = ?, error_message = ?,
                    pane_id = ?, worktree_path = ?, assigned_tier = ?, integrated_sha = ?, updated_at = ?
                WHERE subtask_id = ?
                """,
                (
                    to_state_enum.value,
                    new_attempts,
                    res_json,
                    err_msg,
                    final_pane_id,
                    final_worktree,
                    final_tier,
                    final_sha,
                    now,
                    subtask_id,
                ),
            )
            cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
            return dict(cursor.fetchone())

    def adopt_subtask(
        self,
        subtask_id: str,
        source_run_id: str,
        source_subtask_id: str,
        integrated_sha: str,
        assigned_tier: str,
    ) -> Dict[str, Any]:
        """Adota uma tarefa concluída de outro run após a validação integral no bridge."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
            row = cursor.fetchone()
            if row is None:
                raise StateError(t("engine.state.subtask_missing", subtask_id=subtask_id))
            if row["status"] != SubtaskState.PENDING.value:
                raise InvalidStateTransitionError(
                    t(
                        "engine.state.subtask_adoption_pending_only",
                        subtask_id=subtask_id,
                        status=row["status"],
                    )
                )
            result_json = json.dumps(
                {"resumed_from": {"run_id": source_run_id, "subtask_id": source_subtask_id}},
                ensure_ascii=False,
            )
            cursor.execute(
                """
                UPDATE subtasks
                SET status = ?, result_json = ?, assigned_tier = ?, integrated_sha = ?, updated_at = ?
                WHERE subtask_id = ?
                """,
                (
                    SubtaskState.COMPLETED.value,
                    result_json,
                    assigned_tier,
                    integrated_sha,
                    utc_now_iso(),
                    subtask_id,
                ),
            )
            cursor.execute("SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,))
            return dict(cursor.fetchone())

    # =========================================================================
    # Retomada Idempotente e Recuperação de Falhas
    # =========================================================================

    def recover_stranded_tasks(self, run_id: Optional[str] = None) -> int:
        """Recupera subtasks deixadas em estado RUNNING após falha/interrupção, colocando-as em RETRYING."""
        now = utc_now_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if run_id:
                cursor.execute(
                    "UPDATE subtasks SET status = ?, updated_at = ? WHERE run_id = ? AND status = ?",
                    (SubtaskState.RETRYING.value, now, run_id, SubtaskState.RUNNING.value),
                )
            else:
                cursor.execute(
                    "UPDATE subtasks SET status = ?, updated_at = ? WHERE status = ?",
                    (SubtaskState.RETRYING.value, now, SubtaskState.RUNNING.value),
                )
            return cursor.rowcount

    # =========================================================================
    # Gerenciamento de Active Panes Multi-Slot (Achado #7)
    # =========================================================================

    def register_pane(
        self,
        pane_id: str,
        run_id: Optional[str] = None,
        subtask_id: Optional[str] = None,
        tab_id: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> None:
        """Registra terminal ativo para fechamento posterior determinístico."""
        now = utc_now_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO active_panes (pane_id, run_id, subtask_id, tab_id, pid, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pane_id, run_id, subtask_id, tab_id, pid, now),
            )

    def unregister_pane(self, pane_id: str) -> None:
        """Remove registro de pane ativo."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_panes WHERE pane_id = ?", (pane_id,))

    def get_active_panes(self, run_id: Optional[str] = None) -> List[str]:
        """Retorna lista de identificadores de todos os panes ativos."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if run_id:
                cursor.execute("SELECT pane_id FROM active_panes WHERE run_id = ?", (run_id,))
            else:
                cursor.execute("SELECT pane_id FROM active_panes")
            return [row["pane_id"] for row in cursor.fetchall()]

    def get_active_panes_details(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retorna registros detalhados (pane_id, tab_id, pid, run_id, subtask_id) dos panes ativos."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if run_id:
                cursor.execute("SELECT * FROM active_panes WHERE run_id = ?", (run_id,))
            else:
                cursor.execute("SELECT * FROM active_panes")
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Circuit Breakers & Quota Accounting (Achados #22, #23)
    # =========================================================================

    def record_harness_failure(
        self,
        harness: str,
        is_quota: bool = True,
        failure_threshold: int = 1,
        cooldown_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """Registra falha de harness (ex: quota 429), abrindo o circuit breaker se threshold atingido."""
        now_ts = time.time()
        now_iso = utc_now_iso()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM circuit_breakers WHERE harness = ?", (harness,))
            row = cursor.fetchone()

            if row:
                failures = row["failure_count"] + 1
            else:
                failures = 1

            should_open = is_quota or (failures >= failure_threshold)
            new_state = "OPEN" if should_open else "CLOSED"
            cooldown_until = (now_ts + cooldown_seconds) if should_open else 0.0

            cursor.execute(
                """
                INSERT INTO circuit_breakers (harness, state, failure_count, success_count, last_failure_at, cooldown_until, updated_at)
                VALUES (?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(harness) DO UPDATE SET
                    state = excluded.state,
                    failure_count = excluded.failure_count,
                    last_failure_at = excluded.last_failure_at,
                    cooldown_until = excluded.cooldown_until,
                    updated_at = excluded.updated_at
                """,
                (harness, new_state, failures, now_ts, cooldown_until, now_iso),
            )
            cursor.execute("SELECT * FROM circuit_breakers WHERE harness = ?", (harness,))
            res = dict(cursor.fetchone())
            res["is_available"] = (new_state != "OPEN")
            return res

    def record_harness_success(self, harness: str) -> Dict[str, Any]:
        """Registra sucesso na execução do harness, fechando o circuit breaker e zerando falhas."""
        now_iso = utc_now_iso()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO circuit_breakers (harness, state, failure_count, success_count, last_failure_at, cooldown_until, updated_at)
                VALUES (?, 'CLOSED', 0, 1, NULL, 0.0, ?)
                ON CONFLICT(harness) DO UPDATE SET
                    state = 'CLOSED',
                    failure_count = 0,
                    success_count = success_count + 1,
                    cooldown_until = 0.0,
                    updated_at = excluded.updated_at
                """,
                (harness, now_iso),
            )
            cursor.execute("SELECT * FROM circuit_breakers WHERE harness = ?", (harness,))
            res = dict(cursor.fetchone())
            res["is_available"] = True
            return res

    def get_circuit_breaker(self, harness: str) -> Dict[str, Any]:
        """Recupera estado atual do circuit breaker, transitando de OPEN para HALF_OPEN se cooldown expirou."""
        now_ts = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM circuit_breakers WHERE harness = ?", (harness,))
            row = cursor.fetchone()
            if not row:
                return {
                    "harness": harness,
                    "state": "CLOSED",
                    "failure_count": 0,
                    "success_count": 0,
                    "last_failure_at": None,
                    "cooldown_until": 0.0,
                    "is_available": True,
                }

            rec = dict(row)
            if rec["state"] == "OPEN" and now_ts >= rec["cooldown_until"]:
                # Cooldown expirou -> transição para HALF_OPEN
                now_iso = utc_now_iso()
                cursor.execute(
                    "UPDATE circuit_breakers SET state = 'HALF_OPEN', updated_at = ? WHERE harness = ?",
                    (now_iso, harness),
                )
                rec["state"] = "HALF_OPEN"

            rec["is_available"] = (rec["state"] in ("CLOSED", "HALF_OPEN"))
            return rec

    def is_harness_available(self, harness: str) -> bool:
        """Verifica se o harness está disponível para execução (não bloqueado por circuit breaker)."""
        return self.get_circuit_breaker(harness)["is_available"]

    def reset_circuit_breakers(self) -> None:
        """Reseta todos os circuit breakers para CLOSED."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM circuit_breakers")

    def record_quota_usage(
        self,
        harness: str,
        model: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Registra uso de tokens e custo na janela de contabilidade."""
        ts = timestamp if timestamp is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO quota_accounting (harness, model, tokens_in, tokens_out, cost, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (harness, model or "default", tokens_in, tokens_out, cost, ts),
            )

    def get_quota_usage(
        self,
        harness: Optional[str] = None,
        window_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """Calcula agregados de uso (tokens, custo, requisições) dentro de uma janela de tempo."""
        now_ts = time.time()
        min_ts = now_ts - window_seconds

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if harness:
                cursor.execute(
                    """
                    SELECT COUNT(*) as req_count,
                           COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                           COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                           COALESCE(SUM(cost), 0.0) as total_cost
                    FROM quota_accounting
                    WHERE harness = ? AND timestamp >= ?
                    """,
                    (harness, min_ts),
                )
            else:
                cursor.execute(
                    """
                    SELECT COUNT(*) as req_count,
                           COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                           COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                           COALESCE(SUM(cost), 0.0) as total_cost
                    FROM quota_accounting
                    WHERE timestamp >= ?
                    """,
                    (min_ts,),
                )
            row = cursor.fetchone()
            return {
                "harness": harness or "all",
                "window_seconds": window_seconds,
                "request_count": row["req_count"] if row else 0,
                "tokens_in": row["total_tokens_in"] if row else 0,
                "tokens_out": row["total_tokens_out"] if row else 0,
                "cost": round(row["total_cost"], 6) if row else 0.0,
            }

    def close(self) -> None:
        """Fecha conexão em memória, se aplicável."""
        if self._mem_conn is not None:
            try:
                self._mem_conn.close()
            except Exception:
                pass
            self._mem_conn = None
