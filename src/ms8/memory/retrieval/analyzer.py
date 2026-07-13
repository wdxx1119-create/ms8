"""Deterministic Chinese, English, and code-aware query analysis.

The analyzer preserves exact project tokens for later lexical/entity retrieval while
also producing normalized terms for deterministic matching. It does not call a
model and does not perform authorization decisions.
"""

from __future__ import annotations

import importlib
import re
import unicodedata
from dataclasses import dataclass

_CJK_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ENGLISH_PATTERN = re.compile(r"[A-Za-z][A-Za-z']*")
_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_-]*\b")
_CALL_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.:<>-]*\(\)")
_FLAG_PATTERN = re.compile(r"(?<![A-Za-z0-9_])--?[A-Za-z0-9][A-Za-z0-9_-]*")
_VERSION_PATTERN = re.compile(r"(?<![A-Za-z0-9_])v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?", re.IGNORECASE)
_CPP_PATTERN = re.compile(r"(?<![A-Za-z0-9_])C\+\+(?![A-Za-z0-9_])", re.IGNORECASE)
_QUOTED_PATH_PATTERN = re.compile(
    r"(?P<quote>['\"])(?P<path>(?:[A-Za-z]:\\|\.\.?/|/)[^'\"\r\n]+?)(?P=quote)"
)
_PATH_STOP_CHARS = r",;:()\[\]{}<>\"'，。；：（）【】《》“”‘’"
_PATH_PATTERN = re.compile(
    rf"(?:[A-Za-z]:\\(?:[^\\\s{_PATH_STOP_CHARS}]+\\)*"
    rf"[^\\\s{_PATH_STOP_CHARS}]+|(?:\.\.?/|/)[^\s{_PATH_STOP_CHARS}]+)"
)
_CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _unique(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _jieba_segments(value: str) -> tuple[str, ...]:
    """Return deterministic Jieba segments, or an empty tuple when unavailable."""

    try:
        module = importlib.import_module("jieba")
    except ImportError:
        return ()
    cutter = getattr(module, "lcut", None)
    if not callable(cutter):
        return ()
    try:
        raw = cutter(value, HMM=False)
    except (RuntimeError, TypeError, ValueError):
        return ()
    return _unique([str(item) for item in raw if str(item).strip()])


def _cjk_fallback(value: str) -> tuple[str, ...]:
    tokens: list[str] = list(value)
    if len(value) > 1:
        tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
    return _unique(tokens)


def _english_variants(token: str) -> tuple[str, ...]:
    normalized = token.casefold()
    variants = [normalized]
    if len(normalized) > 4 and normalized.endswith("ies"):
        variants.append(normalized[:-3] + "y")
    elif (
        len(normalized) > 3
        and normalized.endswith("s")
        and not normalized.endswith(("ss", "us", "is"))
    ):
        variants.append(normalized[:-1])
    return _unique(variants)


def _identifier_parts(token: str) -> tuple[str, ...]:
    stripped = token[:-2] if token.endswith("()") else token
    coarse = re.split(r"[._:/\\<>-]+", stripped)
    parts: list[str] = []
    for item in coarse:
        for underscore_part in item.split("_"):
            parts.extend(_CAMEL_BOUNDARY_PATTERN.split(underscore_part))
    return _unique([item.casefold() for item in parts if item])


def _exact_matches(text: str) -> tuple[str, ...]:
    matches: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in _QUOTED_PATH_PATTERN.finditer(text):
        start, end = match.span()
        path = match.group("path")
        matches.append((start, end, path, "path"))
        occupied.append((start, end))

    def overlaps(start: int, end: int) -> bool:
        return any(start < existing_end and end > existing_start for existing_start, existing_end in occupied)

    for pattern, kind in (
        (_PATH_PATTERN, "path"),
        (_CALL_PATTERN, "call"),
        (_FLAG_PATTERN, "flag"),
        (_VERSION_PATTERN, "version"),
        (_CPP_PATTERN, "language"),
    ):
        for match in pattern.finditer(text):
            start, end = match.span()
            if overlaps(start, end):
                continue
            matches.append((start, end, match.group(0), kind))
            occupied.append((start, end))

    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return _unique([item[2] for item in matches])


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    original_text: str
    normalized_text: str
    language_profile: tuple[str, ...]
    tokens: tuple[str, ...]
    exact_tokens: tuple[str, ...]
    code_tokens: tuple[str, ...]
    cjk_tokens: tuple[str, ...]
    english_tokens: tuple[str, ...]
    identifier_parts: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "original_text": self.original_text,
            "normalized_text": self.normalized_text,
            "language_profile": list(self.language_profile),
            "tokens": list(self.tokens),
            "exact_tokens": list(self.exact_tokens),
            "code_tokens": list(self.code_tokens),
            "cjk_tokens": list(self.cjk_tokens),
            "english_tokens": list(self.english_tokens),
            "identifier_parts": list(self.identifier_parts),
        }


def analyze_query(text: str) -> QueryAnalysis:
    original = str(text or "").strip()
    if not original:
        raise ValueError("query text must not be empty")
    normalized = " ".join(unicodedata.normalize("NFKC", original).casefold().split())

    exact_tokens = _exact_matches(original)
    cjk_tokens: list[str] = []
    for run in _CJK_RUN_PATTERN.findall(original):
        segments = _jieba_segments(run)
        cjk_tokens.extend(segments or _cjk_fallback(run))
        # Preserve robust deterministic fallback terms even when Jieba is available.
        cjk_tokens.extend(_cjk_fallback(run))

    english_tokens: list[str] = []
    for token in _ENGLISH_PATTERN.findall(original):
        english_tokens.extend(_english_variants(token))

    identifiers = list(_IDENTIFIER_PATTERN.findall(original))
    identifiers.extend(token for token in exact_tokens if token.endswith("()"))
    identifier_parts: list[str] = []
    for identifier in identifiers:
        identifier_parts.extend(_identifier_parts(identifier))

    code_tokens = _unique(
        [
            token
            for token in exact_tokens
            if (
                token.endswith("()")
                or token.startswith(("-", "/", "./", "../"))
                or ":\\" in token
                or "." in token
                or token.casefold() == "c++"
            )
        ]
    )

    profile: list[str] = []
    if cjk_tokens:
        profile.append("zh")
    if english_tokens:
        profile.append("en")
    if code_tokens or any("_" in token or "-" in token for token in identifiers):
        profile.append("code")
    if not profile:
        profile.append("unknown")

    all_tokens = _unique(
        [
            *exact_tokens,
            *cjk_tokens,
            *english_tokens,
            *identifier_parts,
        ]
    )
    return QueryAnalysis(
        original_text=original,
        normalized_text=normalized,
        language_profile=_unique(profile),
        tokens=all_tokens,
        exact_tokens=exact_tokens,
        code_tokens=code_tokens,
        cjk_tokens=_unique(cjk_tokens),
        english_tokens=_unique(english_tokens),
        identifier_parts=_unique(identifier_parts),
    )


__all__ = ["QueryAnalysis", "analyze_query"]
