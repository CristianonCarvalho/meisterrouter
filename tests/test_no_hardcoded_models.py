from pathlib import Path


import re


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "meister" / "templates"
SCANNED_FILES = (
    "meister/config.py",
    "meister/worker.py",
    "meister/herdr/workers.py",
    "meister/jev.py",
    "meister/cli.py",
    "meister/models.py",
    "meister/state.py",
    "meister/herdr/bridge.py",
    "meister/herdr/tui.py",
    "meister/logger.py",
)
FORBIDDEN = (
    "gpt-6",
    "gpt-4o",
    "gemini-3",
    "gemini_flash",
    "gemini-flash",
    "haiku",
    "sonnet",
    "opus",
    "typesafe/jev",
    "claude-sonnet",
    "anthropic/claude",
    "google/gemini",
    "openai/",
    '"luna"',
    "'luna'",
    "meister_luna_model",
    "meister_gemini_model",
    "meister_gemini_flash_model",
    "meister_haiku_model",
    "meister_sonnet_model",
    "meister_copilot_model",
    "meister_primary_worker",
    "meister_disable_luna",
    "meister_enable_copilot",
    "jev_model",
)


def test_runtime_modules_have_no_hardcoded_model_catalog():
    occurrences = []
    for relative_path in SCANNED_FILES:
        path = REPO_ROOT / relative_path
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            for forbidden in FORBIDDEN:
                if forbidden in lowered:
                    occurrences.append(f"{relative_path}:{line_number}: {line.strip()}")
    assert not occurrences, "Hardcoded model catalog found:\n" + "\n".join(occurrences)


def test_templates_have_no_hardcoded_model_names():
    forbidden = re.compile(r"gpt-6|gemini|haiku|sonnet|opus|\bluna\b|typesafe/jev|claude-", re.IGNORECASE)
    occurrences = []
    for path in sorted(TEMPLATES_DIR.iterdir()):
        if not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.search(line):
                occurrences.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")
    assert not occurrences, "Hardcoded model names found in templates:\n" + "\n".join(occurrences)
