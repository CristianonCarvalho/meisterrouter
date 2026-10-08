from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from typing import Dict

import yaml


class LaneToggleError(Exception):
    """Raised when a lane toggle cannot be safely applied."""


def _name_list(names: Iterable[str], argument: str) -> list[str]:
    try:
        result = list(names)
    except TypeError as exc:
        raise LaneToggleError(f"{argument} must be a collection of lane names") from exc
    if any(not isinstance(name, str) or not name for name in result):
        raise LaneToggleError(f"{argument} must contain non-empty lane names")
    return list(dict.fromkeys(result))


def _write_temp(directory: str, prefix: str, content: bytes, mode: int | None = None) -> str:
    descriptor, path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(path, mode)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _restore_backup(backup_path: str, previous_backup: bytes | None, directory: str) -> None:
    if previous_backup is None:
        try:
            os.unlink(backup_path)
        except FileNotFoundError:
            pass
        return

    restore_path = _write_temp(
        directory,
        ".lane-toggle-restore-",
        previous_backup,
    )
    try:
        os.replace(restore_path, backup_path)
    finally:
        if os.path.exists(restore_path):
            os.unlink(restore_path)


def apply_toggle(
    config_path: str | os.PathLike[str],
    catalog_names: Sequence[str],
    enabled_now: Mapping[str, bool],
    enable: Iterable[str],
    disable: Iterable[str],
) -> Dict[str, bool]:
    """Update workers.enabled without exposing a partially written config."""
    path = os.fspath(config_path)
    directory = os.path.dirname(os.path.abspath(path))
    catalog = _name_list(catalog_names, "catalog_names")
    catalog_set = set(catalog)
    enable_names = _name_list(enable, "enable")
    disable_names = _name_list(disable, "disable")

    invalid = sorted((set(enable_names) | set(disable_names)) - catalog_set)
    if invalid:
        valid = ", ".join(catalog) if catalog else "(none)"
        raise LaneToggleError(
            f"Unknown lane name(s): {', '.join(invalid)}. Valid lane names: {valid}"
        )
    overlap = sorted(set(enable_names) & set(disable_names))
    if overlap:
        raise LaneToggleError(f"Cannot enable and disable the same lane: {', '.join(overlap)}")
    if not isinstance(enabled_now, Mapping):
        raise LaneToggleError("enabled_now must be a mapping of lane names to booleans")
    if any(not isinstance(name, str) or type(value) is not bool for name, value in enabled_now.items()):
        raise LaneToggleError("enabled_now must contain only lane names and boolean values")

    updated = dict(enabled_now)
    for name in disable_names:
        updated[name] = False
    for name in enable_names:
        updated[name] = True

    enabled_count = sum(
        updated.get(name, True) is True
        for name in catalog
    )
    if catalog and enabled_count == 0:
        raise LaneToggleError("Cannot disable the last enabled lane; at least one lane must remain enabled")

    if updated == dict(enabled_now):
        return updated

    original_exists = os.path.exists(path)
    original_bytes: bytes | None = None
    config_mode: int | None = None
    if original_exists:
        try:
            with open(path, "rb") as stream:
                original_bytes = stream.read()
            config_mode = stat.S_IMODE(os.stat(path).st_mode)
            parsed = yaml.safe_load(original_bytes.decode("utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise LaneToggleError(f"Cannot read configuration file {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LaneToggleError(f"Configuration file {path} must contain a YAML mapping")
        document = parsed
    else:
        document = {}

    workers = document.get("workers")
    if workers is None:
        workers = {}
        document["workers"] = workers
    if not isinstance(workers, dict):
        raise LaneToggleError(f"Configuration value workers in {path} must be a mapping")
    workers["enabled"] = updated

    try:
        serialized = yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
    except yaml.YAMLError as exc:
        raise LaneToggleError(f"Cannot serialize configuration file {path}: {exc}") from exc

    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise LaneToggleError(f"Cannot create configuration directory {directory}: {exc}") from exc
    backup_path = f"{path}.bak"
    previous_backup: bytes | None = None
    backup_existed = os.path.exists(backup_path)
    if backup_existed:
        try:
            with open(backup_path, "rb") as stream:
                previous_backup = stream.read()
        except OSError as exc:
            raise LaneToggleError(f"Cannot read backup file {backup_path}: {exc}") from exc

    config_temp: str | None = None
    backup_temp: str | None = None
    backup_installed = False
    try:
        config_temp = _write_temp(
            directory,
            ".lane-toggle-config-",
            serialized,
            config_mode,
        )
        if original_bytes is not None:
            backup_temp = _write_temp(
                directory,
                ".lane-toggle-backup-",
                original_bytes,
                config_mode,
            )
            os.replace(backup_temp, backup_path)
            backup_temp = None
            backup_installed = True
        os.replace(config_temp, path)
        config_temp = None
    except Exception as exc:
        rollback_error: Exception | None = None
        if backup_installed:
            try:
                _restore_backup(
                    backup_path,
                    previous_backup if backup_existed else None,
                    directory,
                )
            except Exception as restore_exc:
                rollback_error = restore_exc
        for temporary in (config_temp, backup_temp):
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        message = f"Cannot safely write configuration file {path}: {exc}"
        if rollback_error is not None:
            message += f"; could not restore previous backup: {rollback_error}"
        raise LaneToggleError(message) from exc

    return updated
