"""Console streams that never break on the text the CLI prints."""

import codecs
import sys


def _is_utf8(encoding: object) -> bool:
    if not isinstance(encoding, str) or not encoding:
        return False
    try:
        return codecs.lookup(encoding).name == "utf-8"
    except LookupError:
        return False


def ensure_utf8_streams() -> None:
    """Switch stdout/stderr to UTF-8 (errors='replace') when they use another encoding.

    On Windows a subprocess without a terminal writes in cp1252, which cannot encode
    the emojis in our messages; streams without ``reconfigure`` are left untouched.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None or _is_utf8(getattr(stream, "encoding", None)):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError, TypeError):
            pass
