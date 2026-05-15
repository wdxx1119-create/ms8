from __future__ import annotations

from app.config import DedupeConfig
from app.memory.repository import MemoryRepository
from app.rules.dedupe_rules import jaccard_similarity, make_dedupe_key


def dedupe_check(
    repo: MemoryRepository,
    category: str,
    normalized_text: str,
    cfg: DedupeConfig | None = None,
) -> tuple[bool, str, str, str, int]:
    """
    Returns:
      allow_write, dedupe_key, duplicate_of, dedupe_mode, recent_dup_count

    dedupe_mode:
      - "new": no duplicate found
      - "soft_duplicate": historical duplicate exists, but keep record
      - "hard_duplicate": high-confidence duplicate, drop
    """
    cfg = cfg or DedupeConfig()
    key = make_dedupe_key(category, normalized_text)
    duplicates = repo.find_duplicates(key, limit=500)
    recent_exact = repo.find_recent_duplicates(key, within_minutes=cfg.hard_block_window_minutes, limit=500)
    latest_id = str(duplicates[0].get("meta", {}).get("id", "")) if duplicates else ""

    repeat_threshold = int(cfg.category_repeat_thresholds.get(category, cfg.hard_block_repeat_threshold))
    if len(recent_exact) >= max(1, repeat_threshold):
        return False, key, latest_id, "hard_duplicate_exact", len(recent_exact)
    if recent_exact:
        return True, key, latest_id, "soft_duplicate_exact", len(recent_exact)

    # Similarity dedupe: only within same category and short time window.
    similar_rows = repo.find_recent_by_category(category, within_minutes=cfg.similar_window_minutes, limit=300)
    best_sim = 0.0
    best_id = ""
    for item in similar_rows:
        cand = str(item.get("normalized_text", ""))
        sim = jaccard_similarity(normalized_text, cand)
        if sim > best_sim:
            best_sim = sim
            best_id = str(item.get("meta", {}).get("id", ""))

    if best_sim >= cfg.similar_hard_threshold:
        return False, key, best_id, "hard_duplicate_similar", len(similar_rows)
    if best_sim >= cfg.similar_soft_threshold:
        return True, key, best_id, "soft_duplicate_similar", len(similar_rows)

    return True, key, "", "new", 0
