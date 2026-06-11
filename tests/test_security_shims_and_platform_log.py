from __future__ import annotations

import runpy
import subprocess

import pytest

from ms8.engine_core.security import crypto_manager, file_crypto, key_manager, security_schema
from ms8.engine_core.security.encryption import (
    crypto_manager as enc_crypto_manager,
)
from ms8.engine_core.security.encryption import (
    file_crypto as enc_file_crypto,
)
from ms8.engine_core.security.encryption import (
    key_manager as enc_key_manager,
)
from ms8.engine_core.security.encryption import (
    security_schema as enc_security_schema,
)
from ms8.engine_core.security.shadow.shadow_platform_log import emit_system_log


def test_security_shims_re_export_core_types() -> None:
    assert crypto_manager.CryptoManager is enc_crypto_manager.CryptoManager
    assert file_crypto.encrypt_bytes is enc_file_crypto.encrypt_bytes
    assert key_manager.KeyManager is enc_key_manager.KeyManager
    assert security_schema.SecurityStatus is enc_security_schema.SecurityStatus


def test_security_cli_main_shim_exits_with_encryption_main(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_main() -> int:
        return 7

    monkeypatch.setattr("ms8.engine_core.security.encryption.cli.main", _fake_main)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("ms8.engine_core.security.cli", run_name="__main__")
    assert exc.value.code == 7


def test_emit_system_log_invokes_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def _fake_run(cmd: list[str], **_: object) -> object:
        seen.append(cmd)
        return object()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    emit_system_log("seal", {"ok": True})
    assert seen
    assert seen[0][0:2] == ["logger", "-t"]
    assert seen[0][2] == "openclaw-shadow"


def test_emit_system_log_swallow_subprocess_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_: object, **__: object) -> object:
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(subprocess, "run", _boom)
    emit_system_log("seal", {"ok": True})
