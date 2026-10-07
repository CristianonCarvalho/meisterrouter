"""Internationalization (i18n) foundation for MeisterRouter.

Provides active language resolution and string localization helpers.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

import importlib
import pkgutil

SUPPORTED_LANGUAGES = ("en", "pt-BR")
DEFAULT_LANGUAGE = "en"


def _load_catalogs() -> tuple[dict[str, dict[str, str]], list[str]]:
    """Reúne os catálogos de `meister/locales/`: `en.py`, `en_<area>.py`, `pt_br.py` e `pt_br_<area>.py`.

    Cada área da tradução tem o seu módulo (sem arquivo compartilhado para editar). Chave repetida entre
    módulos do mesmo idioma não é erro em tempo de execução (vale a última, em ordem alfabética), mas
    fica em `DUPLICATE_KEYS` para o teste de paridade acusar.
    """
    import meister.locales as package

    catalogs: dict[str, dict[str, str]] = {"en": {}, "pt-BR": {}}
    duplicates: list[str] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda module: module.name):
        name = info.name
        if name == "en" or name.startswith("en_"):
            language = "en"
        elif name == "pt_br" or name.startswith("pt_br_"):
            language = "pt-BR"
        else:
            continue
        messages = importlib.import_module(f"meister.locales.{name}").MESSAGES
        for key, value in messages.items():
            if key in catalogs[language]:
                duplicates.append(f"{language}:{key} ({name})")
            catalogs[language][key] = value
    return catalogs, duplicates


CATALOGS, DUPLICATE_KEYS = _load_catalogs()
EN_MESSAGES = CATALOGS["en"]
PT_BR_MESSAGES = CATALOGS["pt-BR"]

_explicit_language: Optional[str] = None
_cached_config_language: Optional[str] = None
_warned_invalid_env_lang: bool = False


def normalize_language(value: Optional[str]) -> Optional[str]:
    """Normalize language code to 'en' or 'pt-BR', or None if invalid."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip().replace("_", "-").lower()
    if cleaned in ("en", "en-us"):
        return "en"
    if cleaned in ("pt", "pt-br"):
        return "pt-BR"
    return None


def set_language(value: Optional[str]) -> None:
    """Set explicit active language, or None to clear explicit override."""
    global _explicit_language
    if value is None:
        _explicit_language = None
    else:
        norm = normalize_language(value)
        _explicit_language = norm if norm is not None else value


def reset_language_cache() -> None:
    """Reset language caches and explicit setting (helper for tests)."""
    global _explicit_language, _cached_config_language, _warned_invalid_env_lang
    _explicit_language = None
    _cached_config_language = None
    _warned_invalid_env_lang = False


def _get_config_language() -> str:
    """Lazy load language from meister.config.yaml in current directory."""
    global _cached_config_language
    if _cached_config_language is not None:
        return _cached_config_language

    try:
        for filename in ("meister.config.yaml", "meister.config.yml"):
            if os.path.exists(filename):
                import yaml

                with open(filename, "r", encoding="utf-8") as stream:
                    data = yaml.safe_load(stream) or {}
                if isinstance(data, dict) and "language" in data:
                    normalized = normalize_language(data["language"])
                    if normalized:
                        _cached_config_language = normalized
                        return _cached_config_language
                break
    except Exception:
        pass

    _cached_config_language = DEFAULT_LANGUAGE
    return _cached_config_language


def get_language() -> str:
    """Resolve active language following precedence:

    (a) set_language(value) explicit
    (b) MEISTER_LANG environment variable
    (c) language in meister.config.yaml (lazy cached)
    (d) 'en' default
    """
    global _warned_invalid_env_lang

    # (a) Explicit override
    if _explicit_language is not None:
        normalized_explicit = normalize_language(_explicit_language)
        if normalized_explicit is not None:
            return normalized_explicit

    # (b) MEISTER_LANG environment variable
    if "MEISTER_LANG" in os.environ:
        raw_env = os.environ.get("MEISTER_LANG")
        normalized_env = normalize_language(raw_env)
        if normalized_env is not None:
            return normalized_env
        if not _warned_invalid_env_lang:
            _warned_invalid_env_lang = True
            sys.stderr.write(
                f"WARNING: invalid language in MEISTER_LANG: {raw_env!r}. Accepted values: en, pt-BR\n"
            )

    # (c) meister.config.yaml in cwd & (d) default 'en'
    return _get_config_language()


def t(key: str, **fields: Any) -> str:
    """Retrieve message from active locale catalog, fallback to English, then key.

    Never raises an exception on missing keys or formatting errors.
    """
    lang = get_language()
    catalog = CATALOGS.get(lang, EN_MESSAGES)
    msg = catalog.get(key)
    if msg is None and lang != "en":
        msg = EN_MESSAGES.get(key)
    if msg is None:
        return key

    if fields:
        try:
            return msg.format(**fields)
        except Exception:
            return msg
    return msg
