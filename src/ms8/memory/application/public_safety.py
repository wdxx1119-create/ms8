"""Repository-content safety audit for eventual public memory-ledger integration."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN_BASENAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_ed25519",
    "secrets.json",
    "credentials.json",
    "SELF_HOSTED_RUNNER_SETUP.md",
}
_FORBIDDEN_SUFFIXES = {".pem", ".p12", ".pfx", ".key"}
_FORBIDDEN_PATH_PARTS = {"ms8_policy_core", "ms8-policy-core-private"}
_FORBIDDEN_DOC_PREFIXES = {"WEEKLY_UPDATE_", "INTERNAL_"}
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    r"\s+[A-Za-z0-9+/=\r\n]{64,}"
    r"-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    re.MULTILINE,
)
_SECRET_PATTERNS = (
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+\\"),
)
_PRIVATE_STAGING_REPOSITORY = "ms8-" + "macos-private"


@dataclass(frozen=True, slots=True)
class PublicSafetyFinding:
    severity: str
    code: str
    path: str
    line: int | None
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "line": self.line,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PublicSafetyResult:
    scanned_files: int
    text_files: int
    binary_files: int
    findings: tuple[PublicSafetyFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "scanned_files": self.scanned_files,
            "text_files": self.text_files,
            "binary_files": self.binary_files,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [item.to_dict() for item in self.findings],
        }


def _path_findings(relative: Path) -> list[PublicSafetyFinding]:
    findings: list[PublicSafetyFinding] = []
    path_text = relative.as_posix()
    if relative.name in _FORBIDDEN_BASENAMES or relative.suffix.casefold() in _FORBIDDEN_SUFFIXES:
        findings.append(
            PublicSafetyFinding("error", "forbidden_sensitive_file", path_text, None, "sensitive filename is tracked")
        )
    if any(part in _FORBIDDEN_PATH_PARTS for part in relative.parts):
        findings.append(
            PublicSafetyFinding("error", "closed_core_source_present", path_text, None, "closed policy-core source must remain separate")
        )
    if relative.name in {"SELF_HOSTED_RUNNER_SETUP.md"} or any(
        relative.name.startswith(prefix) for prefix in _FORBIDDEN_DOC_PREFIXES
    ):
        findings.append(
            PublicSafetyFinding("error", "internal_document_present", path_text, None, "internal operational document is not public-candidate material")
        )
    return findings


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def audit_public_candidate(repository_root: Path, tracked_paths: Iterable[Path]) -> PublicSafetyResult:
    root = Path(repository_root).resolve()
    findings: list[PublicSafetyFinding] = []
    scanned = text_files = binary_files = 0
    seen: set[str] = set()
    for raw_path in tracked_paths:
        relative = Path(raw_path)
        key = relative.as_posix()
        if key in seen:
            continue
        seen.add(key)
        scanned += 1
        findings.extend(_path_findings(relative))
        absolute = (root / relative).resolve()
        if not absolute.is_relative_to(root):
            findings.append(
                PublicSafetyFinding("error", "path_escape", key, None, "tracked path resolves outside repository")
            )
            continue
        if absolute.is_symlink():
            findings.append(
                PublicSafetyFinding("error", "tracked_symlink", key, None, "public candidate must not contain tracked symlinks")
            )
            continue
        if not absolute.is_file():
            continue
        data = absolute.read_bytes()
        if b"\x00" in data:
            binary_files += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            binary_files += 1
            continue
        text_files += 1
        private_key = _PRIVATE_KEY_BLOCK.search(text)
        if private_key is not None:
            findings.append(
                PublicSafetyFinding(
                    "error",
                    "private_key",
                    key,
                    _line_number(text, private_key.start()),
                    "complete credential-like private-key block detected",
                )
            )
        for line_number, line in enumerate(text.splitlines(), start=1):
            for code, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        PublicSafetyFinding("error", code, key, line_number, "credential-like material detected")
                    )
            for pattern in _LOCAL_PATH_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        PublicSafetyFinding("warning", "local_absolute_path", key, line_number, "local user path should be reviewed before public integration")
                    )
        if _PRIVATE_STAGING_REPOSITORY in text:
            findings.append(
                PublicSafetyFinding("warning", "private_repository_reference", key, None, "private staging repository name is present")
            )
    ordered = tuple(sorted(findings, key=lambda item: (item.severity, item.path, item.line or 0, item.code)))
    return PublicSafetyResult(
        scanned_files=scanned,
        text_files=text_files,
        binary_files=binary_files,
        findings=ordered,
    )


__all__ = ["PublicSafetyFinding", "PublicSafetyResult", "audit_public_candidate"]
