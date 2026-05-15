from __future__ import annotations

from ms8.app.config import QualityGateConfig, ThresholdConfig


def quality_gate(
    text: str,
    cfg: QualityGateConfig | None = None,
    thresholds: ThresholdConfig | None = None,
) -> tuple[bool, str]:
    cfg = cfg or QualityGateConfig()
    thresholds = thresholds or ThresholdConfig()
    stripped = text.strip()
    if not stripped:
        return False, "empty"

    cjk_count = sum(1 for c in stripped if "\u4e00" <= c <= "\u9fff")
    cjk_ratio = cjk_count / max(1, len(stripped))
    min_len = cfg.min_len_cjk if cjk_ratio >= float(thresholds.cjk_ratio_threshold) else cfg.min_len_non_cjk

    if len(stripped) < min_len:
        return False, "too_short"
    if len(stripped) > cfg.max_len:
        return False, "too_long"
    noisy_ratio = sum(1 for c in stripped if c in "-_*/#") / max(1, len(stripped))
    if noisy_ratio > cfg.noisy_ratio_max:
        return False, "too_noisy"
    return True, "ok"
