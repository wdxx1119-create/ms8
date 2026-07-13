from __future__ import annotations

from pathlib import Path

from ms8.memory.application.public_safety import audit_public_candidate


def test_public_safety_passes_clean_candidate_and_warns_private_reference(tmp_path: Path) -> None:
    clean = tmp_path / "src" / "module.py"
    clean.parent.mkdir(parents=True)
    clean.write_text("VALUE = 'safe'\n", encoding="utf-8")
    report = tmp_path / "docs" / "validation.md"
    report.parent.mkdir(parents=True)
    report.write_text("repository: " + "ms8-" + "macos-private\n", encoding="utf-8")

    result = audit_public_candidate(
        tmp_path,
        (Path("src/module.py"), Path("docs/validation.md")),
    )

    assert result.passed is True
    assert result.error_count == 0
    assert result.warning_count == 1
    assert result.findings[0].code == "private_repository_reference"


def test_public_safety_rejects_secret_internal_doc_and_closed_core(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=github_pat_" + "A" * 45 + "\n", encoding="utf-8")
    internal = tmp_path / "docs" / "WEEKLY_UPDATE_2026-07-12.md"
    internal.parent.mkdir(parents=True)
    internal.write_text("internal\n", encoding="utf-8")
    closed = tmp_path / "src" / "ms8_policy_core" / "engine.py"
    closed.parent.mkdir(parents=True)
    closed.write_text("value = 1\n", encoding="utf-8")

    result = audit_public_candidate(
        tmp_path,
        (
            Path(".env"),
            Path("docs/WEEKLY_UPDATE_2026-07-12.md"),
            Path("src/ms8_policy_core/engine.py"),
        ),
    )

    codes = {item.code for item in result.findings}
    assert result.passed is False
    assert "forbidden_sensitive_file" in codes
    assert "github_token" in codes
    assert "internal_document_present" in codes
    assert "closed_core_source_present" in codes


def test_public_safety_flags_local_absolute_paths_for_review(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "example.md"
    path.parent.mkdir(parents=True)
    path.write_text("/Users/example/private/workspace\n", encoding="utf-8")

    result = audit_public_candidate(tmp_path, (Path("docs/example.md"),))

    assert result.passed is True
    assert result.warning_count == 1
    assert result.findings[0].code == "local_absolute_path"


def test_public_safety_allows_short_private_key_attack_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "tests" / "attack_fixture.py"
    fixture.parent.mkdir(parents=True)
    begin = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    end = "-----END " + "OPENSSH PRIVATE KEY-----"
    fixture.write_text(
        f'SAMPLE = "{begin}abc{end}"\n',
        encoding="utf-8",
    )

    result = audit_public_candidate(tmp_path, (Path("tests/attack_fixture.py"),))

    assert result.passed is True
    assert result.error_count == 0


def test_public_safety_rejects_complete_private_key_block(tmp_path: Path) -> None:
    key = tmp_path / "docs" / "leaked.txt"
    key.parent.mkdir(parents=True)
    body = "A" * 80
    begin = "-----BEGIN " + "PRIVATE KEY-----"
    end = "-----END " + "PRIVATE KEY-----"
    key.write_text(
        f"{begin}\n{body}\n{end}\n",
        encoding="utf-8",
    )

    result = audit_public_candidate(tmp_path, (Path("docs/leaked.txt"),))

    assert result.passed is False
    assert result.error_count == 1
    assert result.findings[0].code == "private_key"
