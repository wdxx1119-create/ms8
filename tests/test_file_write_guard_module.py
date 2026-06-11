from __future__ import annotations

from pathlib import Path

from ms8.engine_core import file_write_guard as fw


def test_get_lock_is_stable_per_path(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    l1 = fw._get_lock(p)
    l2 = fw._get_lock(p)
    assert l1 is l2
    assert hasattr(l1, "acquire")


def test_guarded_file_write_context_and_atomic_write_text(tmp_path: Path) -> None:
    p = tmp_path / "x" / "a.txt"
    with fw.guarded_file_write(p):
        fw.atomic_write_text(p, "hello")
    assert p.read_text(encoding="utf-8") == "hello"


def test_atomic_write_bytes_retry_on_missing_tmp(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "retry.bin"
    calls = {"n": 0}
    original_replace = Path.replace

    def _replace(self: Path, target: Path):  # type: ignore[override]
        if self.name.endswith(".tmp") and calls["n"] == 0:
            calls["n"] += 1
            raise FileNotFoundError("transient")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", _replace, raising=False)
    fw.atomic_write_bytes(p, b"abc")
    assert p.read_bytes() == b"abc"
    assert calls["n"] == 1


def test_file_type_routing() -> None:
    assert fw._file_type(Path("a.md")) == "text"
    assert fw._file_type(Path("a.txt")) == "text"
    assert fw._file_type(Path("a.json")) == "json"
    assert fw._file_type(Path("a.jsonl")) == "json"
    assert fw._file_type(Path("a.log")) == "log"
    assert fw._file_type(Path("a.db")) == "sqlite"
    assert fw._file_type(Path("a.sqlite")) == "sqlite"
    assert fw._file_type(Path("a.bin")) == "binary"


def test_secure_read_text_empty_and_write_paths(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "secure.txt"

    class _Mgr:
        def __init__(self) -> None:
            self.last_file_type = ""
            self.last_target: Path | None = None
            self.last_allow_plaintext: bool | None = None

        def decrypt_after_read(self, data: bytes, target_path: Path, allow_plaintext: bool = False) -> bytes:
            self.last_target = target_path
            self.last_allow_plaintext = allow_plaintext
            return data

        def encrypt_before_write(self, content: bytes, file_type: str, target_path: Path) -> bytes:
            self.last_file_type = file_type
            self.last_target = target_path
            return content

    mgr = _Mgr()

    monkeypatch.setattr("ms8.engine_core.config.get_config", lambda: {"x": 1})
    monkeypatch.setattr("ms8.engine_core.security.get_crypto_manager", lambda _cfg: mgr)

    # missing file returns empty bytes/text
    assert fw.secure_read_bytes(p) == b""
    assert fw.secure_read_text(p) == ""

    fw.secure_write_text(p, "ok")
    assert p.read_text(encoding="utf-8") == "ok"
    assert mgr.last_file_type == "text"

    got = fw.secure_read_text(p, allow_plaintext=True)
    assert got == "ok"
    assert mgr.last_allow_plaintext is True


def test_secure_append_text_reads_then_writes(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "append.txt"

    class _Mgr:
        def decrypt_after_read(self, data: bytes, target_path: Path, allow_plaintext: bool = False) -> bytes:
            return data

        def encrypt_before_write(self, content: bytes, file_type: str, target_path: Path) -> bytes:
            return content

    monkeypatch.setattr("ms8.engine_core.config.get_config", lambda: {"x": 1})
    monkeypatch.setattr("ms8.engine_core.security.get_crypto_manager", lambda _cfg: _Mgr())

    fw.secure_append_text(p, "a")
    fw.secure_append_text(p, "b")
    assert p.read_text(encoding="utf-8") == "ab"
