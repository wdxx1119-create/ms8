from __future__ import annotations

from typing import Any

from ms8.app.config import DedupeConfig


def _levenshtein_ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i, ca in enumerate(a, start=1):
        prev = dp[0]
        dp[0] = i
        for j, cb in enumerate(b, start=1):
            old = dp[j]
            cost = 0 if ca == cb else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = old
    dist = dp[n]
    return 1.0 - (dist / max(m, n))


def dedupe_check(repo: Any, category: str, text: str, cfg: DedupeConfig | None = None):
    cfg = cfg or DedupeConfig()
    rows = repo.list() if hasattr(repo, "list") else []
    target = str(text or "").strip().lower()
    best_score = 0.0
    best_row: dict[str, Any] | None = None
    for row in rows:
        if str(row.get("category", "")) != str(category):
            continue
        base = str(row.get("normalized_text") or row.get("text") or "").strip().lower()
        if not base:
            continue
        score = _levenshtein_ratio(target, base)
        if score > best_score:
            best_score = score
            best_row = row
    hard_threshold = float(cfg.similar_hard_threshold)
    exact_match = bool(best_row) and target == str(
        (best_row or {}).get("normalized_text") or (best_row or {}).get("text") or ""
    ).strip().lower()
    if best_score >= hard_threshold and (exact_match or hard_threshold <= 0.9):
        return False, best_row, best_score, "hard_duplicate_similarity", {"score": best_score}
    if best_score >= float(cfg.similar_soft_threshold):
        return True, best_row, best_score, "soft_duplicate_similarity", {"score": best_score}
    return True, None, best_score, "unique", {"score": best_score}
