import pytest

from meister.config_editor import apply_lanes, load_effective_lanes, restore_default_lanes

pytest.importorskip("ruamel.yaml")


PROJECT_CONFIG = (
    "# Comentario do topo\n"
    "router:\n"
    "  # comentario do router\n"
    "  mode: first\n"
    "workers:\n"
    "  # vias do projeto\n"
    "  tier_order:\n"
    "    - name: lane_a  # primeira\n"
    "      harness: codex\n"
    "      model: gpt-x\n"
    "      effort: medium\n"
    "    - name: lane_b\n"
    "      harness: claude\n"
    "      model: sonnet\n"
    "# fim\n"
)


def _counts(content: bytes) -> tuple:
    crlf = content.count(b"\r\n")
    return crlf, content.count(b"\n") - crlf, content.count(b"\r") - crlf


def _assert_all_crlf(content: bytes) -> None:
    crlf, lone_lf, lone_cr = _counts(content)
    assert crlf > 0
    assert lone_lf == 0
    assert lone_cr == 0


def _assert_all_lf(content: bytes) -> None:
    crlf, lone_lf, lone_cr = _counts(content)
    assert lone_lf > 0
    assert crlf == 0
    assert lone_cr == 0


EDITABLE = ("name", "harness", "model", "effort", "enabled", "max_parallel")


def _reversed_lanes(lanes: list) -> list:
    edits = [{key: lane[key] for key in EDITABLE if lane.get(key) is not None} for lane in lanes]
    return list(reversed(edits))


def _apply_reversed(path):
    document = load_effective_lanes(str(path))
    return apply_lanes(document, _reversed_lanes(document.lanes), document.stamp)


def _assert_comments(
    content: bytes, comments: tuple = (b"# Comentario do topo", b"# comentario do router", b"# fim")
) -> None:
    for comment in comments:
        assert comment in content


# Ao remover tier_order, o "# fim" (preso a ultima via pelo ruamel) sai junto; os demais ficam.
OUTSIDE_TIER_ORDER = (b"# Comentario do topo", b"# comentario do router")


def test_crlf_file_stays_crlf_after_apply_lanes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    original = PROJECT_CONFIG.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(original)

    _apply_reversed(path)

    written = path.read_bytes()
    _assert_all_crlf(written)
    _assert_comments(written)
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == original


def test_crlf_file_stays_crlf_after_restore_default_lanes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    original = PROJECT_CONFIG.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(original)

    document = load_effective_lanes(str(path))
    restore_default_lanes(document, document.stamp)

    written = path.read_bytes()
    _assert_all_crlf(written)
    _assert_comments(written, OUTSIDE_TIER_ORDER)
    assert b"tier_order" not in written
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == original


def test_crlf_router_only_file_stays_crlf_after_apply_lanes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    original = b"router:\r\n  # comentario\r\n  mode: first\r\n"
    path.write_bytes(original)

    document = _apply_reversed(path)

    written = path.read_bytes()
    _assert_all_crlf(written)
    assert b"# comentario" in written
    assert document.has_tier_order
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == original


def test_lf_file_stays_lf_after_apply_and_restore(tmp_path):
    path = tmp_path / "meister.config.yaml"
    original = PROJECT_CONFIG.encode("utf-8")
    path.write_bytes(original)

    _apply_reversed(path)
    applied = path.read_bytes()
    _assert_all_lf(applied)
    _assert_comments(applied)
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == original

    document = load_effective_lanes(str(path))
    restore_default_lanes(document, document.stamp)
    restored = path.read_bytes()
    _assert_all_lf(restored)
    _assert_comments(restored, OUTSIDE_TIER_ORDER)
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == applied


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_file_without_final_newline_keeps_its_newline(tmp_path, newline):
    path = tmp_path / "meister.config.yaml"
    original = PROJECT_CONFIG.rstrip("\n").replace("\n", newline).encode("utf-8")
    path.write_bytes(original)

    _apply_reversed(path)

    written = path.read_bytes()
    if newline == "\r\n":
        _assert_all_crlf(written)
    else:
        _assert_all_lf(written)
    _assert_comments(written)
    assert (tmp_path / "meister.config.yaml.bak").read_bytes() == original


def test_missing_file_is_written_with_lf(tmp_path):
    path = tmp_path / "meister.config.yaml"
    document = load_effective_lanes(str(path))

    apply_lanes(document, _reversed_lanes(document.lanes), document.stamp)

    _assert_all_lf(path.read_bytes())
    assert not (tmp_path / "meister.config.yaml.bak").exists()
