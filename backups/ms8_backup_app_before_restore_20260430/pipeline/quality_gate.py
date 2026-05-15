from __future__ import annotations

from ms8.app.config import QualityGateConfig


def quality_gate(record: str, cfg: QualityGateConfig | None = None):
    cfg = cfg or QualityGateConfig()
    text = str(record or "")
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if cjk_count > 0 and len(text.strip()) < int(cfg.min_len_cjk):
        return False, "too_short_cjk"
    if cjk_count == 0 and len(text.strip()) < int(cfg.min_len_non_cjk):
        return False, "too_short_non_cjk"
    return True, "ok"
